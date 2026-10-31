import asyncio
import hashlib
import json
import math
import os
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from openai import AsyncOpenAI
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from jobmatch.config import Settings
from jobmatch.db import migrate
from jobmatch.matching import (
    RETRIEVAL_LIMIT,
    Digest,
    DigestAccountMissing,
    DigestProfileChanged,
    current_user,
    generate_digest,
    retrieve_candidates,
    router,
)
from jobmatch.models import EMBEDDING_DIMENSIONS, ModelResponseError, Models
from jobmatch.schemas import Preferences

GOLDEN = Path(__file__).with_name("golden")
FAMILIES = json.loads((GOLDEN / "families.json").read_text(encoding="utf-8"))
DAY = date(2026, 1, 15)

# These grade maps are independent of embeddings, ranking, explanations and ID allocation.
# 3 = best work aligns with the candidate's specialization; 2 = clear fit; 1 = plausible stretch.
# The rejected slots are known from the postings and rules below, not derived from filter output.
SCENARIOS = {
    "portfolio": ({"A": 3, "I": 2, "J": 1}, {"B", "C", "D", "E", "F", "H"}),
    "specialization": ({"B": 3, "H": 2, "J": 1}, {"A", "C", "D", "F"}),
    "consulting": ({"C": 3, "A": 2, "I": 1}, {"D", "E", "F", "H"}),
}
COUNTERFACTUALS = {
    "name": ("Morgan Fairprobe", "Taylor Swaprobe"),
    "gender": ("she/her", "he/him"),
    "college": ("Prestigepoint University", "Openridge Community College"),
}
PRIVATE_MARKERS = {
    "@privacy-eval.invalid", "privacy-postcode-44771", "privacy-resume-9831", "privacy-credentials-718",
    *[item.lower() for values in COUNTERFACTUALS.values() for item in values],
}


def posting_catalog() -> list[dict]:
    postings = []
    for family in FAMILIES:
        key, role, primary, secondary, project, adjacent = (
            family[name] for name in ("key", "role", "primary", "secondary", "project", "adjacent")
        )
        p = ", ".join(primary)
        s = ", ".join(secondary)
        for slot, company, title, description, locations, workplace, employment, low, high, currency in (
            ("A", "Broadcraft", role, f"Build {project} with {p}. Own production delivery.",
             ["Seattle, WA"], "remote", "full-time", 140000, 155000, "USD"),
            ("B", "Fieldlight", f"Senior {role}", f"Lead a specialist practice in {s}. Mentor a team.",
             ["Boston, MA"], "hybrid", "full-time", 120000, 145000, "USD"),
            ("C", "Juniper Market", f"{role} Consultant",
             f"Own independent consulting and client delivery for fixed-term projects using {p}. Lead discovery.",
             ["Seattle, WA", "Boston, MA"], "remote", "contract", None, 150000, "USD"),
            ("D", "Sharpline Recruiting", role, f"Work hands-on with {p} and {s}. Join our internship cohort.",
             ["Austin, TX"], "onsite", "internship", 65000, 70000, "USD"),
            ("E", "Smallstep", f"Junior {role}", f"Help an established practice with {p}. Assist the lead.",
             ["Seattle, WA"], "remote", "full-time", 70000, 85000, "USD"),
            ("F", "Unspecified Works", role, f"Run new programs around {project} using {p} and {s}.",
             [], None, None, None, None, None),
            ("G", "Expired Team", role, f"Deliver {project} using {p} and {s}. Apply to lead the program.",
             ["Boston, MA"], "remote", "full-time", 170000, 190000, "USD"),
            ("H", "Overseas Works", f"Staff {role}", f"Maintain our specialist program for {s}. Present to partners.",
             ["Boston, MA"], "remote", "full-time", 170000, None, "EUR"),
            ("I", "Threadmark", f"{role} II", f"Deliver {project} using {primary[0]} and {primary[1]}.",
             ["Boston, MA"], "remote", "full-time", 135000, 155000, "USD"),
            ("J", "Greylark", f"Associate {role}",
             f"Contribute to {project}; familiarity with {primary[0]} or {secondary[0]} helps. Learn {adjacent}.",
             ["Seattle, WA"], "remote", "full-time", 112000, 130000, "USD"),
        ):
            postings.append(dict(
                key=f"{key}/{slot}", company=company, title=title, description=description, locations=locations,
                workplace=workplace, employment=employment, low=low, high=high, currency=currency, active=slot != "G",
            ))
    # Distinct eligible distractors make the 50-result cap meaningful even after hard filtering.
    occupations = (
        ("Executive Chef", "Plan menus and oversee a commercial kitchen"),
        ("Litigation Associate", "Prepare litigation briefs and depositions"),
        ("Anesthesia Nurse", "Provide perioperative care at a surgical center"),
        ("Structural Architect", "Draft architectural plans and review building permits"),
        ("Petroleum Geologist", "Analyze core samples and advise drilling crews"),
        ("Farm Operations Supervisor", "Manage irrigation schedules and harvest crews"),
        ("Recording Technician", "Calibrate microphones and mix audio tracks"),
        ("Library Curator", "Preserve manuscripts and manage exhibit loans"),
        ("Transportation Planner", "Plan transit corridors and coordinate route studies"),
        ("Museum Conservator", "Inspect artifacts and develop conservation plans"),
        ("Nutrition Director", "Oversee meal planning and dietary compliance"),
        ("Clinical Pharmacist", "Verify medication orders and advise patients"),
    )
    for index, title_and_action in enumerate(occupations):
        title, action = title_and_action
        for scope in ("public sector", "regional program", "nonprofit", "cooperative", "private practice"):
            postings.append(dict(
                key=f"other/{index}-{scope}", title=title, company=f"{scope.title()} Group",
                description=f"{action} for a {scope}. Coordinate monthly reviews.", locations=["Seattle, WA"],
                workplace="remote", employment="full-time", low=160000, high=None, currency="USD", active=True,
            ))
    return postings


def candidate(family: dict, scenario: str) -> tuple[dict, dict]:
    role, primary, secondary, project = (family[name] for name in ("role", "primary", "secondary", "project"))
    locations = ["Boston" if scenario == "specialization" else "Seattle", "privacy-postcode-44771"]
    if scenario == "portfolio":
        skills, experience, target = primary, f"Built {project} and owned production delivery.", role
        rules = [("workplace", "remote"), ("employment_type", "full-time"), ("min_salary", "USD 110000")]
        remote = "remote"
    elif scenario == "specialization":
        skills, experience, target = secondary, f"Led a specialist practice in {', '.join(secondary)}.", f"Senior {role}"
        rules = [("location", "Boston"), ("location", "Seattle"), ("workplace", "remote"),
                 ("workplace", "hybrid"), ("employment_type", "full-time"), ("company", "broadcraft")]
        remote = "hybrid"
    else:
        skills = [*primary, "consulting", "client delivery"]
        experience, target = f"Owned client delivery on fixed-term projects for {project}.", f"{role} Consultant"
        rules = [("employment_type", "contract"), ("employment_type", "full-time"), ("min_salary", "USD 125000")]
        remote = "any"
    profile = dict(headline=target, skills=skills, experience=[experience], credentials=[])
    prefs = dict(target_roles=[target], locations=locations, remote_preference=remote,
                 dealbreakers=[dict(kind=kind, value=value) for kind, value in rules])
    return profile, prefs


@pytest.fixture
async def matching_db():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to a PostgreSQL database with pgvector")
    schema = f"match_eval_{uuid4().hex}"
    async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        options = conninfo_to_dict(database_url).get("options", "")
        url = make_conninfo(database_url, options=f"{options} -c search_path={schema},public".strip())
        try:
            # One connection makes a provider callback that opens a connection a regression test
            # for accidentally holding a database transaction during inference.
            async with AsyncConnectionPool(
                url, min_size=1, max_size=1, timeout=5, kwargs={"row_factory": dict_row}, open=False,
            ) as db:
                await migrate(db)
                yield db
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


class DeterministicModels:
    """Input-driven fixture, never imported by the running application and never given gold labels."""

    settings = SimpleNamespace(embedding_model="matching-feature-hash-v1")

    def __init__(self, during_rerank=None):
        self.during_rerank = during_rerank

    async def embed(self, texts):
        vectors = []
        for text in texts:
            features = re.findall(r"[a-z0-9]+", text.lower())
            vector = [0.0] * EMBEDDING_DIMENSIONS
            vector[-1] = 1.0
            for feature in features:
                digest = hashlib.blake2s(feature.encode(), digest_size=4).digest()
                index = int.from_bytes(digest, "big") % (EMBEDDING_DIMENSIONS - 1)
                vector[index] += 1.0
            vectors.append(vector)
        return vectors

    async def structured(self, name, messages, schema):
        assert name == "rerank"
        if self.during_rerank is not None:
            await self.during_rerank()
        jobs = json.loads(messages[1]["content"])["jobs"]
        return schema.model_validate({"matches": [
            {"job_id": job["job_id"], "fit": "fit", "reasons": [
                {"text": f"Posting evidence: {quote}", "quote": quote},
            ], "gaps": []}
            for job in jobs[:3] if (quote := job["description"].split(".")[0])
        ]})


class RecordingModels:
    def __init__(self, provider):
        self.provider = provider
        self.settings = provider.settings
        self.structured_attempts = self.structured_valid = 0
        self.privacy_violations = 0
        self.query = None
        self.messages = None
        self.candidate_ids = []

    def inspect(self, body: str):
        self.privacy_violations += sum(marker in body.casefold() for marker in PRIVATE_MARKERS)

    async def embed(self, texts):
        self.query = texts
        self.inspect(json.dumps(texts))
        return await self.provider.embed(texts)

    async def structured(self, name, messages, schema):
        self.messages = messages
        self.inspect(json.dumps(messages))
        self.candidate_ids = [job["job_id"] for job in json.loads(messages[1]["content"])["jobs"]]
        self.structured_attempts += 1
        response = await self.provider.structured(name, messages, schema)
        schema.model_validate(response)
        self.structured_valid += 1
        return response


async def seed_catalog(db, models, postings):
    vectors = await models.embed([job["title"] + "\n" + job["description"] for job in postings])
    ids = {}
    async with db.connection() as conn:
        for job, embedding in zip(postings, vectors, strict=True):
            row = await (await conn.execute(
                """INSERT INTO jobs (company, title, description, locations, workplace, employment_type,
                                     salary_min, salary_max, salary_currency, content_hash, embedding, embedding_model)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s) RETURNING id""",
                (job["company"], job["title"], job["description"], job["locations"], job["workplace"], job["employment"],
                 job["low"], job["high"], job["currency"], hashlib.sha256(job["key"].encode()).digest(),
                 json.dumps(embedding), models.settings.embedding_model),
            )).fetchone()
            ids[job["key"]] = job_id = row["id"]
            await conn.execute(
                "INSERT INTO job_sources (board, external_id, job_id, url, active) VALUES (%s, %s, %s, %s, %s)",
                ("golden", job["key"], job_id, f"https://z.example/{job_id}", job["active"]),
            )
            if job["key"].endswith("/A"):
                await conn.execute(
                    """INSERT INTO job_sources (board, external_id, job_id, url, active)
                       VALUES ('second', %s, %s, %s, true), ('third', %s, %s, %s, false)""",
                    (job["key"], job_id, f"https://a.example/{job_id}", job["key"], job_id, f"https://0.example/{job_id}"),
                )
    return ids


async def seed_account(db, profile, preferences, people=None):
    account_id = uuid4()
    profile = {**profile, **people} if people is not None else profile
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO accounts (id, email, password_hash, profile, preferences) VALUES (%s, %s, %s, %s, %s)",
            (account_id, f"{account_id}@privacy-eval.invalid", "privacy-credentials-718", Jsonb(profile), Jsonb(preferences)),
        )
        if people is not None:
            raw_resume = f"{people['name']} · {people['gender']} · {people['college']} · privacy-resume-9831"
            await conn.execute(
                "INSERT INTO resumes (account_id, filename, pdf, original_text) VALUES (%s, %s, %s, %s)",
                (account_id, "privacy-resume-9831.pdf", raw_resume.encode(), raw_resume),
            )
    return account_id


def ndcg_at_3(ids, labels):
    def dcg(grades):
        return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))

    return dcg([labels.get(job, 0) for job in ids[:3]]) / dcg(sorted(labels.values(), reverse=True)[:3])


async def evaluate(db, provider, mode):
    postings = posting_catalog()
    ids = await seed_catalog(db, provider, postings)
    canonical = {ids[job["key"]]: job["description"] for job in postings}
    inactive = {ids[job["key"]] for job in postings if not job["active"]}
    people = {kind: values[0] for kind, values in COUNTERFACTUALS.items()}
    semaphore = asyncio.Semaphore(4)

    async def run_variant(profile, prefs, person):
        account_id = await seed_account(db, profile, prefs, person)
        recording = RecordingModels(provider)
        try:
            digest = Digest.model_validate(await generate_digest(db, recording, account_id, DAY))
            matches = digest.matches
        except ModelResponseError:
            # A refusal, malformed completion, or failed structured request contributes to
            # the live denominator rather than being silently dropped or counted as a success.
            if recording.structured_attempts == 0:
                raise
            matches = []
        return recording, matches

    async def run_case(family, scenario):
        async with semaphore:
            profile, prefs = candidate(family, scenario)
            base, matches = await run_variant(profile, prefs, people)
            alts = []
            for kind, variants in COUNTERFACTUALS.items():
                alt, alt_matches = await run_variant(profile, prefs, {**people, kind: variants[1]})
                assert alt.query == base.query
                assert alt.messages == base.messages
                alts.append((alt, alt_matches))
            return scenario, family["key"], (base, matches), alts

    results = await asyncio.gather(*(
        run_case(family, scenario) for family in FAMILIES for scenario in SCENARIOS
    ))
    recall_at_50 = retrieval_ndcg_at_3 = reranked_ndcg_at_3 = 0.0
    structured_valid = structured_attempts = top3_agreements = counterfactual_pairs = 0
    dealbreaker_violations = ungrounded_reasons = privacy_violations = 0
    for scenario, family_key, (base, matches), alts in results:
        label_slots, rejected_slots = SCENARIOS[scenario]
        labels = {ids[f"{family_key}/{slot}"]: grade for slot, grade in label_slots.items()}
        forbidden = {ids[f"{family['key']}/{slot}"] for family in FAMILIES for slot in rejected_slots}
        base_ids = [match.job_id for match in matches]
        recall_at_50 += len(set(base.candidate_ids) & labels.keys()) / len(labels)
        retrieval_ndcg_at_3 += ndcg_at_3(base.candidate_ids, labels)
        reranked_ndcg_at_3 += ndcg_at_3(base_ids, labels)
        for variant, selected in [(base, matches), *alts]:
            structured_valid += variant.structured_valid
            structured_attempts += variant.structured_attempts
            privacy_violations += variant.privacy_violations
            retrieved = set(variant.candidate_ids)
            returned = {match.job_id for match in selected}
            assert len(retrieved) == len(variant.candidate_ids) <= RETRIEVAL_LIMIT
            assert returned <= retrieved
            assert not ((retrieved | returned) & inactive)
            dealbreaker_violations += len((retrieved | returned) & forbidden)
            ungrounded_reasons += sum(
                reason.quote not in canonical[match.job_id] for match in selected for reason in match.reasons
            )
        for alt, alt_matches in alts:
            counterfactual_pairs += 1
            # Failed model responses do not earn fairness credit from equal empty lists.
            top3_agreements += bool(base.structured_valid and alt.structured_valid and
                                    base_ids == [match.job_id for match in alt_matches])

    size = len(results)
    return {
        "mode": mode,
        "labelled_profiles": size,
        "counterfactual_pairs": counterfactual_pairs,
        "structured_attempts": structured_attempts,
        "structured_valid_rate": structured_valid / max(1, structured_attempts),
        "dealbreaker_violations": dealbreaker_violations,
        "ungrounded_reasons_shipped": ungrounded_reasons,
        "privacy_violations": privacy_violations,
        "recall_at_50": recall_at_50 / size,
        "retrieval_ndcg_at_3": retrieval_ndcg_at_3 / size,
        "ndcg_at_3": reranked_ndcg_at_3 / size,
        "fairness_top3_agreement_rate": top3_agreements / counterfactual_pairs,
    }


def assert_metrics(metrics, baseline):
    print(json.dumps({"event": "MATCH_EVAL", **metrics}, sort_keys=True))
    assert metrics["dealbreaker_violations"] == 0
    assert metrics["ungrounded_reasons_shipped"] == 0
    assert metrics["privacy_violations"] == 0
    assert metrics["structured_valid_rate"] >= 0.99
    assert metrics["fairness_top3_agreement_rate"] >= 0.95
    if baseline is not None:
        for metric in ("recall_at_50", "retrieval_ndcg_at_3", "ndcg_at_3"):
            assert metrics[metric] >= baseline[metric] - 0.05, (metric, metrics[metric], baseline[metric])


async def test_deterministic_matching_eval(matching_db):
    metrics = await evaluate(matching_db, DeterministicModels(), "deterministic_sql_test_double")
    if os.getenv("MATCH_EVAL_CAPTURE_BASELINE") == "1":
        assert_metrics(metrics, None)
        return
    filename = Path(os.getenv("MATCH_EVAL_BASELINE", GOLDEN / "matching_baseline.json"))
    assert_metrics(metrics, json.loads(filename.read_text(encoding="utf-8")))


@pytest.mark.live
async def test_live_openai_matching_eval(matching_db):
    if os.getenv("RUN_LIVE_OPENAI") != "1":
        pytest.skip("set RUN_LIVE_OPENAI=1 to opt into real OpenAI requests")
    settings = Settings()
    if not settings.openai_api_key:
        pytest.fail("RUN_LIVE_OPENAI requires OPENAI_API_KEY")
    if settings.openai_base_url:
        pytest.fail("Unset OPENAI_BASE_URL to run this eval against OpenAI")
    provider = Models(settings)
    await provider.client.close()
    wire_calls = {"embeddings": 0, "chat/completions": 0}

    async def inspect_request(request: httpx.Request):
        assert request.url.host == "api.openai.com"
        path = next(path for path in wire_calls if request.url.path.endswith(f"/{path}"))
        wire_calls[path] += 1
        raw = (await request.aread()).decode()
        assert not any(marker in raw.casefold() for marker in PRIVATE_MARKERS), "private canary in wire payload"
        body = json.loads(raw)
        if path == "chat/completions":
            assert body["store"] is False
            content = json.loads(body["messages"][1]["content"])
            assert set(content) == {"profile", "target_roles", "jobs"}
            assert set(content["profile"]) == {"headline", "skills", "experience", "credentials"}

    provider.client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url="https://api.openai.com/v1",
        timeout=50,
        max_retries=0,
        http_client=httpx.AsyncClient(event_hooks={"request": [inspect_request]}),
    )
    try:
        metrics = await evaluate(matching_db, provider, "live_openai")
        assert wire_calls["embeddings"] > 0 and wire_calls["chat/completions"] == metrics["structured_attempts"]
        metrics["wire_privacy_checks"] = sum(wire_calls.values())
        baseline = os.getenv("MATCH_LIVE_EVAL_BASELINE")
        assert_metrics(metrics, json.loads(Path(baseline).read_text(encoding="utf-8")) if baseline else None)
    finally:
        await provider.close()


async def test_exclusions_and_revision_cutover(matching_db):
    models = DeterministicModels()
    ids = await seed_catalog(matching_db, models, posting_catalog())
    family = FAMILIES[0]
    profile, prefs = candidate(family, "portfolio")
    account = await seed_account(matching_db, profile, prefs)
    app = FastAPI()
    app.state.db, app.state.models = matching_db, models
    app.dependency_overrides[current_user] = lambda: account
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/digest")).json() == {"day": None, "matches": [], "excluded_count": 0}
        assert (await client.get("/api/digest/exclusions")).json() == {"exclusions": [], "total": 0}
        ran = await client.post("/api/digest/run")
        assert ran.status_code == 200, ran.text
        digest = ran.json()
        assert (await client.get("/api/digest")).json() == digest
        assert 0 < len(digest["matches"]) <= 3
        assert next(x for x in digest["matches"] if x["job_id"] == ids["backend/A"])["url"] == f"https://a.example/{ids['backend/A']}"
        page1 = (await client.get("/api/digest/exclusions")).json()
        page2 = (await client.get("/api/digest/exclusions", params={"limit": 40, "offset": 25})).json()
        assert page1["total"] == page2["total"] == digest["excluded_count"] == 60
        assert len(page1["exclusions"]) == 25 and len(page2["exclusions"]) == 35
        assert {entry["job_id"] for entry in page1["exclusions"] + page2["exclusions"]} == {
            ids[f"{family['key']}/{slot}"] for family in FAMILIES for slot in SCENARIOS["portfolio"][1]
        }
        unknown = next(entry for entry in page1["exclusions"] if entry["job_id"] == ids["backend/F"])
        assert [rule["rule"] for rule in unknown["rules"]] == [
            "Required workplace: remote", "Required employment type: full-time", "Minimum salary: USD 110000",
        ]
        assert all(rule["reason"].startswith("Uncertain:") for rule in unknown["rules"])
        salary = next(entry for entry in page1["exclusions"] if entry["job_id"] == ids["backend/H"])
        assert salary["rules"] == [{"rule": "Minimum salary: USD 110000",
                                   "reason": "Uncertain: the listed salary is in a different currency."}]
        assert (await client.get("/api/digest/exclusions", params={"offset": 100})).json() == {
            "total": 60, "exclusions": [],
        }
        async with matching_db.connection() as conn:
            await conn.execute("UPDATE accounts SET preferences = '{}', revision = revision + 1 WHERE id = %s", (account,))
        assert (await client.get("/api/digest")).json()["day"] is None
        assert (await client.get("/api/digest/exclusions")).json()["total"] == 0
        assert (await client.post("/api/digest/run")).json()["excluded_count"] == 0
        async with matching_db.connection() as conn:
            assert (await (await conn.execute("SELECT count(*) AS n FROM digest_exclusions")).fetchone())["n"] == 0


@pytest.mark.parametrize("interruption", ["revision", "delete"])
async def test_provider_call_does_not_hold_connection_or_resurrect_snapshot(matching_db, interruption):
    profile, prefs = candidate(FAMILIES[0], "portfolio")
    account = await seed_account(matching_db, profile, prefs)
    await seed_catalog(matching_db, DeterministicModels(), posting_catalog()[:10])
    await generate_digest(matching_db, DeterministicModels(), account, DAY)

    async def modify():
        async with matching_db.connection() as conn:
            if interruption == "revision":
                await conn.execute("UPDATE accounts SET revision = revision + 1 WHERE id = %s", (account,))
            else:
                await conn.execute("DELETE FROM accounts WHERE id = %s", (account,))

    expected = DigestProfileChanged if interruption == "revision" else DigestAccountMissing
    with pytest.raises(expected):
        await generate_digest(matching_db, DeterministicModels(modify), account, DAY)
    async with matching_db.connection() as conn:
        rows = await (await conn.execute("SELECT revision FROM digests WHERE account_id = %s", (account,))).fetchall()
        assert rows == ([{"revision": 0}] if interruption == "revision" else [])
        exclusions = await (await conn.execute(
            "SELECT count(*) AS n FROM digest_exclusions WHERE account_id = %s", (account,),
        )).fetchone()
        assert exclusions["n"] == (6 if interruption == "revision" else 0)


async def test_rrf_uses_both_ranks_and_filters_before_cutoff(matching_db):
    models = DeterministicModels()
    source = posting_catalog()[0]
    # Sixty otherwise top-ranked but excluded postings, plus one contribution unique
    # to each rank channel and one that appears in both. The old vector version must
    # not join the semantic rank but can still surface through keyword retrieval.
    blocked = [
        dict(source, key=f"blocked/{i}", title="Python instrumentation", workplace="onsite")
        for i in range(60)
    ]
    choices = [
        dict(source, key="semantic", title="Systems Developer", description="Build control planes", workplace="remote"),
        dict(source, key="keyword", title="Rust", description="Python analytics instrumentation", workplace="remote"),
        dict(source, key="both", title="Python", description="Python supplies a Python workshop", workplace="remote"),
        dict(source, key="outdated", title="Ruby", description="Unrelated", workplace="remote"),
    ]
    ids = await seed_catalog(matching_db, models, [*blocked, *choices])
    [embedding] = await models.embed(["Python"])
    async with matching_db.connection() as conn:
        await conn.execute("UPDATE jobs SET embedding_model = 'old' WHERE id = ANY(%s)", ([ids["keyword"], ids["outdated"]],))
        await conn.execute(
            "UPDATE jobs SET embedding = %s::vector WHERE id = ANY(%s)",
            (json.dumps(embedding), [ids["semantic"], *(ids[job["key"]] for job in blocked)]),
        )
    candidates, rejected = await retrieve_candidates(
        matching_db, embedding, "Python instrumentation", Preferences.model_validate({
            "dealbreakers": [{"kind": "workplace", "value": "remote"}],
        }), models.settings.embedding_model,
    )
    ranked = [job["job_id"] for job in candidates]
    assert set(ranked) == {ids["semantic"], ids["keyword"], ids["both"]}
    assert ranked[0] == ids["both"]
    assert {row["job_id"] for row in rejected} == {ids[job["key"]] for job in blocked}
    assert all(row["rules"][0]["rule"] == "Required workplace: remote" for row in rejected)
