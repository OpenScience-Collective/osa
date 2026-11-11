"""Reseeding a chat the server forgot, against the real NEMAR community and the real models.

Real calls, paid for. This is the companion to `tests/test_api/test_reseed.py`, whose model
answers from a rule and so cannot show what a real model does with the framing the seed is
given. A request names a session the server has never seen and carries the turns the widget
stored; the checks are that the real model answers with the context in them, on the tier the
widget reported, and that no handover runs.

The dataset is a test dataset of NEMAR's staging server (`xx0999NN`), which the develop
deployment reads; no tool call is needed to answer, so the id is only ever in the transcript.

Skip condition: see tests/test_integration/test_anthropic_router.py's module docstring.
"""

import json

import pytest
from fastapi.testclient import TestClient

import src.api.routers.community as community_module
from src.api.config import get_settings
from src.api.main import app
from src.api.routers.community import _get_session_store
from src.core.services.anthropic_models import HAIKU, SONNET

pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm,
    pytest.mark.skipif(
        not get_settings().anthropic_api_key,
        reason="ANTHROPIC_API_KEY is not configured (server mode requires it)",
    ),
]

_NEMAR_ORIGIN = "https://nemar.org"
_DECLARED_TOOLS = ["execute_code", "get_full_output"]
_DATASET = "xx099904"
# "Without running anything" is the whole instruction needed to keep the model from calling
# `execute_code`, which would end the stream with a `tool_request` and no `done`: the
# answer is in the transcript, and the declared tools stay because routing (the tier the
# checks below read) only applies to a request that declares `execute_code`.
_ASK = "Without running anything, which dataset were we discussing?"
_SESSION_IDS = ["reseed-llm-quick", "reseed-llm-analysis", "reseed-llm-none"]
_PRIOR_TURNS = [
    {"role": "user", "text": "find datasets with face stimuli"},
    {"role": "assistant", "text": f"The closest match is {_DATASET}, with 18 participants."},
]


def _auth_headers() -> dict[str, str]:
    """The X-API-Key header this environment's admin auth requires, if it requires one."""
    settings = get_settings()
    if not settings.require_api_auth or not settings.api_keys:
        return {}
    admin_keys = settings.parse_admin_keys()
    return {"X-API-Key": next(iter(admin_keys))} if admin_keys else {}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_sessions_left_behind():
    """The real app's in-memory store keeps these sessions; take them out before and after."""
    store = _get_session_store("nemar")
    for session_id in _SESSION_IDS:
        store.pop(session_id, None)
    yield
    for session_id in _SESSION_IDS:
        store.pop(session_id, None)


@pytest.fixture
def models_used(monkeypatch) -> list[str]:
    """The model of every assistant the router builds, in order."""
    used: list[str] = []
    real_create = community_module.create_community_assistant

    def spy(*args, **kwargs):
        awm = real_create(*args, **kwargs)
        used.append(awm.model)
        return awm

    monkeypatch.setattr(community_module, "create_community_assistant", spy)
    return used


def _chat(client: TestClient, message: str, **body) -> list[dict]:
    payload = {"message": message, "client_tools": _DECLARED_TOOLS, **body}
    events: list[dict] = []
    with client.stream(
        "POST",
        "/nemar/chat",
        json=payload,
        headers={"Origin": _NEMAR_ORIGIN, **_auth_headers()},
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    errors = [e for e in events if e.get("event") == "error"]
    assert not errors, f"Streaming error event: {errors}"
    return events


def _done(events: list[dict]) -> dict:
    done = next((e for e in events if e.get("event") == "done"), None)
    assert done is not None, (
        "no done event; a tool_request here means the model tried to run code instead of "
        f"answering from the transcript: {[e.get('event') for e in events]}"
    )
    return done


class TestAReseededSession:
    def test_a_quick_conversation_answers_with_the_prior_context(self, client, models_used) -> None:
        events = _chat(
            client, _ASK, session_id=_SESSION_IDS[0], prior_turns=_PRIOR_TURNS, mode="quick"
        )

        done = _done(events)
        assert _DATASET in done["content"].lower()
        assert done["mode"] == "quick"
        assert done["model"] == HAIKU
        assert models_used == [HAIKU]
        assert done["session_id"] == _SESSION_IDS[0]

    def test_an_analysis_conversation_stays_on_the_analysis_model_without_a_handover(
        self, client, models_used
    ) -> None:
        events = _chat(
            client,
            _ASK,
            session_id=_SESSION_IDS[1],
            prior_turns=_PRIOR_TURNS,
            mode="analysis",
        )

        assert [e for e in events if e.get("event") == "handover"] == []
        done = _done(events)
        assert _DATASET in done["content"].lower()
        assert done["mode"] == "analysis"
        assert done["model"] == SONNET
        assert models_used == [SONNET]

    def test_without_the_turns_the_model_cannot_know(self, client, models_used) -> None:
        events = _chat(client, _ASK, session_id=_SESSION_IDS[2])

        assert _DATASET not in _done(events)["content"].lower()
        assert models_used == [HAIKU]
