import json

import pytest
from pydantic import ValidationError

from jobmatch.matching import RerankResult, grounded_matches, professional_profile, rerank_messages, retrieval_text
from jobmatch.schemas import Preferences


PROFESSIONAL = {
    "headline": "Backend Engineer", "skills": ["Python", "PostgreSQL"],
    "experience": ["Ships job queues"], "credentials": [],
}
POSTING = {
    "job_id": 1, "title": "Backend Engineer", "company": "Example Labs", "locations": ["", "Seattle, WA"],
    "workplace": "hybrid", "url": "https://jobs.example/1",
    "description": "Build Python APIs and maintain durable queues. Experience with Kafka is preferred.",
}
SUGGESTION = {
    "job_id": 1, "fit": "strong_fit",
    "reasons": [{"text": "Your Python skills match the API work.", "quote": "Build Python APIs"}],
    "gaps": [{"text": "Kafka experience is not shown in your profile.", "quote": "Experience with Kafka is preferred."}],
}


def test_only_exact_posting_quotes_and_retrieved_ids_ship():
    reason = {"text": "Unverifiable", "quote": "maintain DURABLE queues"}
    fake_gap = {"text": "C++ gap", "quote": "C++ knowledge is required."}
    result = RerankResult.model_validate({"matches": [
        {**SUGGESTION, "job_id": 444},
        {**SUGGESTION, "reasons": [reason, *SUGGESTION["reasons"]], "gaps": [fake_gap, *SUGGESTION["gaps"]]},
        {**SUGGESTION, "fit": "stretch"},
    ]})
    assert grounded_matches(result, [POSTING]) == [{
        "job_id": 1, "title": "Backend Engineer", "company": "Example Labs", "location": "Seattle, WA",
        "url": "https://jobs.example/1", "fit": "strong_fit", "reasons": SUGGESTION["reasons"],
        "gaps": [SUGGESTION["gaps"][0]["text"]],
    }]


@pytest.mark.parametrize("wrong_quote", ["", "  ", "Example Labs", "Build Python APIs … queues"])
def test_unsupported_or_empty_reasons_do_not_cause_filler(wrong_quote):
    if not wrong_quote:
        with pytest.raises(ValidationError):
            RerankResult.model_validate({"matches": [{**SUGGESTION, "reasons": [{"text": "No evidence", "quote": ""}]}]})
        return
    result = RerankResult.model_validate({"matches": [
        {**SUGGESTION, "reasons": [{"text": "No evidence", "quote": wrong_quote}]},
    ]})
    assert grounded_matches(result, [POSTING]) == []
    assert grounded_matches(RerankResult(matches=[]), [POSTING]) == []


@pytest.mark.parametrize("matches", [
    [{**SUGGESTION, "fit": "perfect_fit"}],
    [{**SUGGESTION, "reasons": SUGGESTION["reasons"] * 4}],
    [{**SUGGESTION, "gaps": SUGGESTION["gaps"] * 3}],
    [{**SUGGESTION, "company": "not model-owned"}],
    [SUGGESTION] * 4,
])
def test_rerank_schema_guards_limits_and_fields(matches):
    with pytest.raises(ValidationError):
        RerankResult.model_validate({"matches": matches})


def test_only_professional_projection_and_soft_signals_reach_provider():
    profile = professional_profile({
        **PROFESSIONAL, "name": "Leak Name", "pronouns": "private pronouns",
        "university": "Private Credentials", "raw_pdf": "secret document", "email": "address@secret.example",
    })
    preferences = Preferences.model_validate({
        "target_roles": ["API Engineer"], "locations": ["Seattle", "secret postcode"],
        "remote_preference": "remote", "dealbreakers": [{"kind": "company", "value": "private blocked employer"}],
    })
    messages = rerank_messages(profile, preferences, [POSTING])
    request = json.loads(messages[1]["content"])
    assert request == {
        "profile": PROFESSIONAL, "target_roles": ["API Engineer"],
        "jobs": [{"job_id": 1, "title": "Backend Engineer", "description": POSTING["description"],
                  "preferred_location_match": True, "preferred_workplace_match": False}],
    }
    semantic, keyword = retrieval_text(profile, preferences)
    assert semantic == "API Engineer\nBackend Engineer\nPython\nPostgreSQL\nShips job queues"
    assert keyword == "API Engineer Backend Engineer Python PostgreSQL"
