import asyncio
import json
import os
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from openai.types.chat import ChatCompletionMessage
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from starlette.requests import Request

from jobmatch.accounts import (
    DUMMY_HASH,
    PASSWORDS,
    SESSION_COOKIE,
    ProposedEdit,
    UpdatePreferencesArgs,
    _apply_preference_patch,
    _chat_tools,
    _extract_pdf,
    _professional_lines,
    _profile_projection,
    _rate_limit,
    _render_tailored,
    _valid_spans,
    redact_identity,
    router,
)
from jobmatch.db import migrate, pool_for
from jobmatch.schemas import Dealbreaker, Preferences, Profile


def pdf_with_lines(*lines: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if lines:
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })}),
        })
        stream = DecodedStreamObject()
        escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
        stream.set_data(("BT /F1 12 Tf 40 730 Td " + " 0 -18 Td ".join(f"({line}) Tj" for line in escaped) + " ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def tool_call(id: str, name: str, args: dict) -> ChatCompletionMessage:
    return ChatCompletionMessage.model_validate({
        "role": "assistant", "tool_calls": [{
            "id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)},
        }],
    })


def test_pdf_text_extraction_rejects_corrupt_and_scanned_files():
    original = _extract_pdf(pdf_with_lines("Jane Doe", "Built reliable services."))
    assert "Jane Doe\nBuilt reliable services." in original
    for payload, detail in ((b"not a PDF", "valid PDF"), (pdf_with_lines(), "not a scan")):
        with pytest.raises(HTTPException, match=detail) as error:
            _extract_pdf(payload)
        assert error.value.status_code == 422


def test_scrubs_sensitive_resume_and_chat_before_matching():
    private = "Jane Doe\njane@example.com\nName: any-person\nSome University, class of 2014\nThey/them\nAge: 43\n"
    original = private + "Built distributed services.\n"
    assert _professional_lines(original) == [(len(private), len(original) - 1, "Built distributed services.")]
    chat = redact_identity("My name is Jane Doe; contact jane@example.com. I'm 43. I only want remote roles.")
    assert "Jane Doe" not in chat and "jane@example.com" not in chat and "43" not in chat
    assert "I only want remote roles." in chat
    result = _profile_projection(Profile(
        headline="Jane Doe at Some University", skills=["jane@example.com", "distributed systems"],
        experience=["Graduated in 2014", "Built distributed services."],
        credentials=["Bachelor of Science", "Some University", "2014 cohort"],
    ))
    assert result.headline == ""
    assert result.skills == ["distributed systems"]
    assert result.experience == ["Built distributed services."]
    assert result.credentials == ["Bachelor of Science"]


def test_patch_preserves_omitted_fields_and_rejects_unstated_rules():
    current = Preferences(locations=["Portland"], target_roles=["data engineer"], remote_preference="hybrid")
    patch = UpdatePreferencesArgs.model_validate({
        "target_roles": ["data engineer", "platform engineer"],
        "add_dealbreakers": [
            {"rule": {"kind": "workplace", "value": "remote"}, "evidence": "I only want remote positions"},
            {"rule": {"kind": "min_salary", "value": "EUR 120000"}, "evidence": "annual minimum is EUR 120k"},
        ],
    })
    message = "Add platform engineer. I only want remote positions; annual minimum is EUR 120k."
    changed = _apply_preference_patch(current, patch, message)
    assert changed.model_dump() == {
        "target_roles": ["data engineer", "platform engineer"], "locations": ["Portland"],
        "remote_preference": "hybrid", "dealbreakers": [
            {"kind": "workplace", "value": "remote"}, {"kind": "min_salary", "value": "EUR 120000"},
        ],
    }
    with pytest.raises(ValueError, match="explicit statement"):
        _apply_preference_patch(current, patch, "Include platform engineer; remote would be nice.")
    with pytest.raises(ValueError, match="explicitly requested"):
        _apply_preference_patch(current, UpdatePreferencesArgs(target_roles=[]), "I like my settings.")
    with pytest.raises(ValueError, match="null is not"):
        _apply_preference_patch(current, UpdatePreferencesArgs(locations=None), "Clear locations.")
    min_hourly = UpdatePreferencesArgs.model_validate({"add_dealbreakers": [{
        "rule": {"kind": "min_salary", "value": "USD 120000"}, "evidence": "minimum is USD 120k monthly",
    }]})
    with pytest.raises(ValueError, match="explicit statement"):
        _apply_preference_patch(current, min_hourly, "The minimum is USD 120k monthly.")
    remove = UpdatePreferencesArgs.model_validate({"remove_dealbreakers": [{
        "rule": {"kind": "workplace", "value": "remote"}, "evidence": "Remove the remote rule",
    }]})
    assert _apply_preference_patch(changed, remove, "Remove the remote rule.").dealbreakers == [
        Dealbreaker(kind="min_salary", value="EUR 120000"),
    ]


async def test_chat_loop_handles_real_tool_frames_and_rejects_unrelated_fields():
    class ScriptedModel:
        async def chat(self, name, messages, tools):
            assert name == "preferences_chat"
            assert {tool["function"]["name"] for tool in tools} == {"get_preferences", "update_preferences"}
            assert all("user_id" not in json.dumps(tool) for tool in tools)
            if len(messages) == 2:
                return tool_call("read", "get_preferences", {})
            if len(messages) == 4:
                assert json.loads(messages[-1]["content"])["preferences"]["target_roles"] == ["data engineer"]
                return tool_call("forbidden", "update_preferences", {"user_id": "another-account"})
            if len(messages) == 6:
                assert "error" in json.loads(messages[-1]["content"])
                assert "another-account" not in json.dumps(messages)
                return tool_call("patch", "update_preferences", {"remote_preference": "remote"})
            assert json.loads(messages[-1]["content"])["preferences"]["remote_preference"] == "remote"
            return ChatCompletionMessage(role="assistant", content="Saved your remote preference.")

    reply, preferences = await _chat_tools(ScriptedModel(), "I prefer remote jobs.", Preferences(target_roles=["data engineer"]))
    assert reply == "Saved your remote preference."
    assert preferences.target_roles == ["data engineer"] and preferences.remote_preference == "remote"


def test_before_text_must_be_exact_unique_and_nonoverlapping_and_after_keeps_numbers():
    unique, repeat = "Built 4 services.", "Ran checks."
    original = f"{unique}\n{repeat}\n{repeat}\n"
    lines = {1: (0, len(unique), unique), 2: (len(unique) + 1, len(unique) + 1 + len(repeat), repeat)}
    candidate = ProposedEdit(line=1, before=unique, after="Built four services.")
    assert _valid_spans(original, lines, [
        ProposedEdit(line=1, before="Built 4 service.", after="Built services."),
        ProposedEdit(line=2, before=repeat, after="Performed checks."),
        ProposedEdit(line=1, before=unique, after="Built 10 services."),
        candidate,
        ProposedEdit(line=1, before=unique, after="Built services."),
    ]) == [(0, len(unique), candidate)]
    assert _render_tailored(original, [{
        "start_offset": 0, "end_offset": len(unique), "before": unique, "after": candidate.after,
    }]) == f"Built four services.\n{repeat}\n{repeat}\n"
    with pytest.raises(RuntimeError, match="original source"):
        _render_tailored(original, [{"start_offset": 0, "end_offset": 4, "before": "Bad span", "after": "Changed"}])


async def test_rate_limit_uses_server_peer_not_forwarded_headers_and_expires():
    class LimitedRedis:
        async def eval(self, script, numkeys, *args):
            assert "EXPIRE" in script and "INCR" in script
            assert numkeys == 2 and args[2:] == (900, 20, 600)
            assert "203.0.113.23" not in " ".join(args[:2])
            return 42

    request = Request({
        "type": "http", "client": ("203.0.113.23", 12345),
        "headers": [(b"x-forwarded-for", b"another-user")],
        "app": SimpleNamespace(state=SimpleNamespace(redis=LimitedRedis())),
    })
    with pytest.raises(HTTPException) as error:
        await _rate_limit(request, "login", 20, 600)
    assert error.value.status_code == 429 and error.value.headers == {"Retry-After": "42"}
    from argon2.exceptions import VerifyMismatchError

    with pytest.raises(VerifyMismatchError):
        PASSWORDS.verify(DUMMY_HASH, "no-such-account")


async def test_authenticated_routes_require_session_before_accessing_services():
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for method, path, kwargs in (
            ("GET", "/api/me", {}), ("DELETE", "/api/me", {}),
            ("POST", "/api/resume", {"content": b""}), ("GET", "/api/resume/tailored", {}),
            ("POST", "/api/chat", {"json": {"message": "I prefer remote"}}),
            ("POST", "/api/resume/edits", {"json": {"job_id": 1}}),
            ("GET", "/api/resume/edits", {}),
        ):
            assert (await client.request(method, path, **kwargs)).status_code == 401


@pytest.fixture
async def accounts_db():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for the accounts integration workflow")
    schema = f"accounts_{uuid4().hex}"
    async with pool_for(url) as admin:
        async with admin.connection() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with pool_for(make_conninfo(url, options=f"-c search_path={schema},public")) as db:
                await migrate(db)
                yield db
        finally:
            async with admin.connection() as conn:
                await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_account_pdf_chat_edit_and_delete_workflow(accounts_db):
    class RedisWithinLimit:
        async def eval(self, script, numkeys, *args):
            return 0

    class ScriptedModel:
        async def structured(self, name, messages, schema):
            contents = json.loads(messages[-1]["content"]) if name != "resume_profile" else messages[-1]["content"]
            assert "Jane Doe" not in messages[-1]["content"] and "jane@example.com" not in messages[-1]["content"]
            if name == "resume_profile":
                assert "Built reliable services." in contents
                return schema(headline="Software engineer", skills=[], experience=["Built reliable services."], credentials=[])
            if name == "resume_edits":
                line = next(value for value in contents["resume_lines"] if value["text"] == "Built reliable services.")
                return schema(edits=[{"line": line["line"], "before": line["text"], "after": "Developed reliable services."}])
            assert name == "resume_edit_grounding"
            return schema(grounded_lines=[contents[0]["line"]])

        async def chat(self, name, messages, tools):
            assert "Jane Doe" not in messages[1]["content"] and "jane@example.com" not in messages[1]["content"]
            if len(messages) == 2:
                return tool_call("read", "get_preferences", {})
            if len(messages) == 4:
                assert json.loads(messages[-1]["content"])["preferences"]["remote_preference"] == "any"
                return tool_call("patch", "update_preferences", {"remote_preference": "remote"})
            return ChatCompletionMessage(role="assistant", content="Saved your preference for remote jobs.")

    async def save_digest(email, day, ids):
        async with accounts_db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO digests (account_id, day, revision, payload)
                SELECT id, %s::date, revision, %s FROM accounts WHERE email = %s
                ON CONFLICT (account_id, day) DO UPDATE
                SET payload = excluded.payload, revision = excluded.revision
                """,
                (day, Jsonb({"matches": [{"job_id": value} for value in ids]}), email),
            )

    app = FastAPI()
    app.include_router(router)
    app.state.db = accounts_db
    app.state.redis = RedisWithinLimit()
    app.state.models = ScriptedModel()
    app.state.settings = SimpleNamespace(cookie_secure=True)
    app_transport = ASGITransport(app=app)
    auth = {"email": "jane@example.com", "password": "A valid password"}
    async with (
        AsyncClient(transport=app_transport, base_url="https://test") as candidate,
        AsyncClient(transport=app_transport, base_url="https://test") as second_session,
        AsyncClient(transport=app_transport, base_url="https://test") as other,
    ):
        response = await candidate.post("/api/auth/register", json=auth)
        assert response.status_code == 201
        assert response.json()["resume"] is None and response.json()["profile"] is None
        cookie = response.headers["set-cookie"]
        assert all(attr in cookie.lower() for attr in ("httponly", "secure", "samesite=lax", "max-age=604800", "path=/api"))
        assert (await candidate.post("/api/auth/register", json=auth)).status_code == 409
        assert (await second_session.post("/api/auth/login", json={**auth, "password": "incorrect"})).status_code == 401
        assert (await second_session.post("/api/auth/login", json=auth)).status_code == 200
        assert candidate.cookies[SESSION_COOKIE] != second_session.cookies[SESSION_COOKIE]
        assert (await other.post("/api/auth/register", json={**auth, "email": "other@example.com"})).status_code == 201

        assert (await candidate.post("/api/resume", files={"file": ("scan.pdf", pdf_with_lines(), "application/pdf")})).status_code == 422
        pdf = pdf_with_lines("Jane Doe", "jane@example.com", "Built reliable services.")
        response = await candidate.post("/api/resume", files={"file": ("original.pdf", pdf, "application/pdf")})
        assert response.status_code == 200 and response.json()["resume"] == {"filename": "original.pdf"}
        assert "Jane Doe" not in response.text
        response = await candidate.post("/api/chat", json={"message": "My name is Jane Doe. Email: jane@example.com. I prefer remote."})
        assert response.status_code == 200 and response.json()["preferences"]["remote_preference"] == "remote"
        assert (await candidate.get("/api/me")).json()["preferences"]["remote_preference"] == "remote"

        async with accounts_db.connection() as conn:
            account = await (await conn.execute(
                "SELECT a.id, a.password_hash, r.pdf, r.original_text FROM accounts a JOIN resumes r ON r.account_id = a.id "
                "WHERE a.email = %s",
                (auth["email"],),
            )).fetchone()
            assert account["password_hash"].startswith("$argon2id$")
            assert account["pdf"] == pdf and "Jane Doe" in account["original_text"]
            job_ids = []
            for number in (1, 2):
                row = await (await conn.execute(
                    """
                    INSERT INTO jobs (company, title, description, locations, content_hash, embedding, embedding_model)
                    VALUES ('Employer', 'Engineer', 'Build services.', ARRAY['Remote'], %s, %s::vector, 'test')
                    RETURNING id
                    """,
                    (bytes([number]), "[" + ",".join(["1", *(["0"] * 1535)]) + "]"),
                )).fetchone()
                job_ids.append(row["id"])
        older_job, job_id = job_ids
        await save_digest(auth["email"], "2026-11-17", [older_job])
        await save_digest(auth["email"], "2026-11-18", [job_id, str(older_job)])
        await save_digest("other@example.com", "2026-11-18", [older_job])
        assert (await candidate.post("/api/resume/edits", json={"job_id": older_job})).status_code == 404
        assert (await other.post("/api/resume/edits", json={"job_id": job_id})).status_code == 404

        response = await candidate.post("/api/resume/edits", json={"job_id": job_id})
        assert response.status_code == 200
        edit = response.json()["edits"][0]
        path = f"/api/resume/edits/{edit['id']}"
        assert edit["status"] == "pending" and edit["job_id"] == job_id
        assert (await candidate.get("/api/resume/edits")).json()["edits"] == [edit]
        assert (await other.patch(path, json={"decision": "approve"})).status_code == 404
        assert (await candidate.get("/api/resume/tailored")).text == account["original_text"]
        approvals = await asyncio.gather(*[
            client.patch(path, json={"decision": "approve"}) for client in (candidate, second_session)
        ])
        assert all(response.status_code == 200 and response.json()["status"] == "approved" for response in approvals)
        assert (await candidate.patch(path, json={"decision": "reject"})).status_code == 409
        response = await candidate.get("/api/resume/tailored")
        assert response.headers["content-type"].startswith("text/plain")
        assert response.text == account["original_text"].replace("Built reliable services.", "Developed reliable services.")
        duplicate = await candidate.post("/api/resume/edits", json={"job_id": job_id})
        assert duplicate.json() == {"edits": []}

        assert (await candidate.post("/api/resume", files={"file": ("replacement.pdf", pdf, "application/pdf")})).status_code == 200
        assert (await candidate.get("/api/resume/edits")).json() == {"edits": []}
        assert (await candidate.patch(path, json={"decision": "approve"})).status_code == 404
        assert (await candidate.get("/api/resume/tailored")).text == account["original_text"]
        assert (await candidate.post("/api/resume/edits", json={"job_id": job_id})).status_code == 404
        await save_digest(auth["email"], "2026-11-18", [job_id])
        assert len((await candidate.post("/api/resume/edits", json={"job_id": job_id})).json()["edits"]) == 1

        other_logout = await other.post("/api/auth/logout")
        assert other_logout.status_code == 204 and (await other.get("/api/me")).status_code == 401
        assert (await candidate.delete("/api/me")).status_code == 204
        assert (await candidate.get("/api/me")).status_code == 401
        assert (await second_session.get("/api/me")).status_code == 401
        async with accounts_db.connection() as conn:
            for table, column in (("accounts", "id"), ("resumes", "account_id"), ("auth_sessions", "account_id"), ("digests", "account_id")):
                row = await (await conn.execute(
                    sql.SQL("SELECT count(*) AS n FROM {} WHERE {} = %s").format(sql.Identifier(table), sql.Identifier(column)),
                    (account["id"],),
                )).fetchone()
                assert row["n"] == 0
            row = await (await conn.execute("SELECT count(*) AS n FROM resume_edits")).fetchone()
            assert row["n"] == 0
