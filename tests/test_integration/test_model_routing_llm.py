"""Quick and analysis routing against the real NEMAR community and the real models.

Real calls, paid for: Claude Haiku answers the quick turns and writes the summary, Claude
Sonnet runs the analysis, and the graph, the tool binding, the NEMAR tools and the handover
are all real. This is the companion to the stand-in tests in `tests/test_api/
test_model_routing.py`, which cover the same code path with a scripted model and so cannot
show what the real models do with the prompt, the tools or the summary.

What it checks, end to end:

- a question stays on the quick model, and an analysis request goes to the analysis model;
- the real summary of a quick conversation keeps the dataset the reader chose;
- the analysis session, which sees only that summary, can say which dataset was discussed.

The model each turn ran on is read from the assistant `create_community_assistant` built,
because an analysis turn that calls `execute_code` ends on a `tool_request` with no `done`
to carry a model.

Skip condition: see tests/test_integration/test_anthropic_router.py's module docstring.
"""

import json

import pytest
from fastapi.testclient import TestClient

import src.api.routers.community as community_module
from src.api.config import get_settings
from src.api.main import app
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
_DATASET = "on002718"


def _auth_headers() -> dict[str, str]:
    """The X-API-Key header this environment's admin auth requires, if it requires one.

    Read through Settings, as test_anthropic_router.py does, so the key is never printed.
    """
    settings = get_settings()
    if not settings.require_api_auth or not settings.api_keys:
        return {}
    admin_keys = settings.parse_admin_keys()
    return {"X-API-Key": next(iter(admin_keys))} if admin_keys else {}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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


def _chat(client: TestClient, message: str, session_id: str | None = None) -> list[dict]:
    body = {"message": message, "client_tools": _DECLARED_TOOLS}
    if session_id:
        body["session_id"] = session_id
    events: list[dict] = []
    with client.stream(
        "POST",
        "/nemar/chat",
        json=body,
        headers={"Origin": _NEMAR_ORIGIN, **_auth_headers()},
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    errors = [e for e in events if e.get("event") == "error"]
    assert not errors, f"Streaming error event: {errors}"
    return events


def _stages(events: list[dict]) -> list[str]:
    return [e["stage"] for e in events if e.get("event") == "handover"]


class TestTheTurnIsRouted:
    def test_a_search_stays_on_the_quick_model(self, client, models_used) -> None:
        events = _chat(client, "find datasets with face stimuli")

        assert models_used == [HAIKU]
        assert _stages(events) == []
        done = next(e for e in events if e.get("event") == "done")
        assert done["mode"] == "quick"
        assert done["model"] == HAIKU

    def test_a_plot_request_goes_to_the_analysis_model(self, client, models_used) -> None:
        events = _chat(client, "plot the ERP for famous faces")

        # A first message has nothing to summarize: it runs analysis on its own session.
        assert models_used == [SONNET]
        assert _stages(events) == []
        # The analysis model may answer or ask to run code (a `tool_request`, no `done`).
        finished = [e for e in events if e.get("event") in ("done", "tool_request")]
        assert finished, [e.get("event") for e in events]
        for event in finished:
            if event["event"] == "done":
                assert event["mode"] == "analysis"
                assert event["model"] == SONNET


class TestTheHandoverKeepsTheDataset:
    def test_the_summary_and_the_analysis_session_know_which_dataset(
        self, client, models_used
    ) -> None:
        first = _chat(client, f"Tell me about dataset {_DATASET} and what it contains.")
        quick_session = next(e for e in first if e.get("event") == "done")["session_id"]
        assert models_used == [HAIKU]

        second = _chat(client, f"Plot the ERP for famous faces in {_DATASET}", quick_session)

        assert _stages(second) == ["summarizing", "summarized", "started"]
        summarized = next(e for e in second if e.get("stage") == "summarized")
        assert _DATASET in summarized["summary"].lower()
        assert summarized["words"] <= 150
        started = next(e for e in second if e.get("stage") == "started")
        analysis_session = started["session_id"]
        assert analysis_session != quick_session
        assert models_used[-1] == SONNET

        # The analysis session holds the summary and the message, and nothing else. A
        # question with no keyword stays on it (the session is sticky) and can only be
        # answered from the summary.
        third = _chat(
            client,
            "Without running anything, which dataset were we discussing?",
            analysis_session,
        )

        assert _stages(third) == []
        done = next((e for e in third if e.get("event") == "done"), None)
        assert done is not None, [e.get("event") for e in third]
        assert done["session_id"] == analysis_session
        assert done["mode"] == "analysis"
        assert _DATASET in done["content"].lower()
