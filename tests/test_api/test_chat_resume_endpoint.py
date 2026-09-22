"""`POST /{community}/chat/resume` driven through the real ASGI app.

The tests in `test_client_tool_streaming.py` call `_stream_chat_response` and
`ChatSession` directly, one layer below the endpoint. That is the right level for the
graph and the session store, and the wrong level for everything this endpoint itself
owns: status codes, request validation, the response header, and the fact that the
handler passes arguments the streaming function actually accepts.

That last one is not hypothetical. While hardening this PR I added an argument at this
call site and not to the function, and the entire suite stayed green because nothing
drove the endpoint. These tests exist so the next such slip is caught here.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from src.api.routers.community import (
    AssistantWithMetrics,
    ChatSession,
    _get_session_store,
    create_community_router,
)
from src.api.tool_results import PendingClientCall
from src.assistants.community import CommunityAssistant
from src.core.config.community import CommunityConfig
from tests.helpers.chat_models import ScriptedChatModel

COMMUNITY = "resumetest"
CALL_ID = "toolu_01aaaaaaaaaaaaaaaaaaaaaa"
OTHER_CALL_ID = "toolu_01bbbbbbbbbbbbbbbbbbbbbb"


def _config() -> CommunityConfig:
    return CommunityConfig(
        id=COMMUNITY,
        name="Resume Test",
        description="Drives the resume endpoint",
        extensions={
            "client_tools": [
                {
                    "name": "execute_code",
                    "runtime": "python",
                    "description": "Run Python in the user's browser.",
                }
            ]
        },
        runtime={"python": {"pyodide_version": "314.0.6", "lockfile": "runtime/l.json"}},
    )


@pytest.fixture(autouse=True)
def _registered():
    """Register a synthetic community, and take it back out again.

    Registered rather than reusing a shipped community because no shipped config sets
    `client_tools` in this phase, by design, and this endpoint does nothing without one.
    """
    from src.assistants.registry import registry

    registry.register_from_config(_config())
    yield
    registry._assistants.pop(COMMUNITY, None)


@pytest.fixture
def client(_registered) -> TestClient:
    app = FastAPI()
    app.include_router(create_community_router(COMMUNITY))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_store():
    _get_session_store(COMMUNITY).clear()
    yield
    _get_session_store(COMMUNITY).clear()


def _parked_session(call_id: str = CALL_ID) -> ChatSession:
    """A session mid-turn: the assistant asked for a browser run and is waiting."""
    session = ChatSession("sess-parked", COMMUNITY)
    session.add_user_message("Plot the alpha power.")
    session.messages.append(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute_code",
                    "args": {"code": "plot()"},
                    "id": call_id,
                    "type": "tool_call",
                }
            ],
        )
    )
    session.set_pending_call(
        PendingClientCall.from_state(
            {
                "call_id": call_id,
                "tool": "execute_code",
                "args": {"code": "plot()"},
                "requires_permission": True,
            }
        )
    )
    _get_session_store(COMMUNITY)[session.session_id] = session
    return session


def _body(**overrides) -> dict:
    payload = {
        "session_id": "sess-parked",
        "result": {"call_id": CALL_ID, "status": "ok", "summary": "peak 10.2 Hz"},
        "client_tools": ["execute_code"],
    }
    payload.update(overrides)
    return payload


def _assistant(
    responses: list | None = None, browser_runs_left: int | None = None
) -> AssistantWithMetrics:
    model = ScriptedChatModel(responses=responses or [AIMessage(content="The peak is at 10.2 Hz.")])
    assistant = CommunityAssistant(
        model=model,
        config=_config(),
        preload_docs=False,
        declared_client_tools={"execute_code"},
        browser_runs_left=browser_runs_left,
    )
    return AssistantWithMetrics(
        assistant=assistant, model="claude-haiku-4-5", key_source="platform"
    )


class TestRefusals:
    def test_an_unknown_session_is_404(self, client: TestClient) -> None:
        response = client.post(f"/{COMMUNITY}/chat/resume", json=_body(session_id="nope"))

        assert response.status_code == 404

    def test_a_call_id_that_is_not_outstanding_is_409(self, client: TestClient) -> None:
        _parked_session()

        response = client.post(
            f"/{COMMUNITY}/chat/resume",
            json=_body(result={"call_id": OTHER_CALL_ID, "status": "ok"}),
        )

        assert response.status_code == 409

    def test_a_session_with_nothing_parked_is_409(self, client: TestClient) -> None:
        session = ChatSession("sess-idle", COMMUNITY)
        _get_session_store(COMMUNITY)[session.session_id] = session

        response = client.post(f"/{COMMUNITY}/chat/resume", json=_body(session_id="sess-idle"))

        assert response.status_code == 409

    def test_replaying_the_same_result_is_409(self, client: TestClient, monkeypatch) -> None:
        """Accept-at-most-once, asserted where a replay would actually arrive.

        This is the whole idempotency story for a transport with no checkpointer.
        """
        _parked_session()
        monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant",
            lambda *_a, **_k: _assistant(),
        )

        first = client.post(f"/{COMMUNITY}/chat/resume", json=_body())
        second = client.post(f"/{COMMUNITY}/chat/resume", json=_body())

        assert first.status_code == 200
        assert second.status_code == 409


class TestRequestValidation:
    def test_an_unknown_top_level_field_is_refused(self, client: TestClient) -> None:
        _parked_session()

        response = client.post(f"/{COMMUNITY}/chat/resume", json=_body(surprise="hi"))

        assert response.status_code == 422

    def test_an_unknown_result_field_is_refused(self, client: TestClient) -> None:
        _parked_session()

        response = client.post(
            f"/{COMMUNITY}/chat/resume",
            json=_body(result={"call_id": CALL_ID, "status": "ok", "surprise": "hi"}),
        )

        assert response.status_code == 422

    def test_oversized_stdout_is_refused_at_the_boundary(self, client: TestClient) -> None:
        """The caps are a containment control, so they have to hold at the edge rather
        than only where the text is later truncated for the model."""
        _parked_session()

        response = client.post(
            f"/{COMMUNITY}/chat/resume",
            json=_body(result={"call_id": CALL_ID, "status": "ok", "stdout": "x" * 20_000}),
        )

        assert response.status_code == 422

    def test_an_oversized_artifact_name_is_refused(self, client: TestClient) -> None:
        _parked_session()

        response = client.post(
            f"/{COMMUNITY}/chat/resume",
            json=_body(result={"call_id": CALL_ID, "status": "ok", "artifacts": ["a" * 600]}),
        )

        assert response.status_code == 422

    def test_a_missing_result_is_refused(self, client: TestClient) -> None:
        response = client.post(f"/{COMMUNITY}/chat/resume", json={"session_id": "sess-parked"})

        assert response.status_code == 422


class TestTheHappyPath:
    def test_it_streams_a_continuation(self, client: TestClient, monkeypatch) -> None:
        _parked_session()
        monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant",
            lambda *_a, **_k: _assistant(),
        )

        response = client.post(f"/{COMMUNITY}/chat/resume", json=_body())

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["X-Session-ID"] == "sess-parked"

        events = [
            json.loads(line[len("data: ") :])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        # `done` and the session it belongs to. Not the reply text: the scripted model
        # returns a whole message rather than streaming it, so no `content` events are
        # produced, and asserting on them here would be measuring the stand-in. The
        # text path is covered in test_client_tool_streaming.py.
        assert "done" in [e["event"] for e in events]
        assert next(e for e in events if e["event"] == "done")["session_id"] == "sess-parked"

    def test_the_result_is_recorded_in_the_session(self, client: TestClient, monkeypatch) -> None:
        session = _parked_session()
        monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant",
            lambda *_a, **_k: _assistant(),
        )

        client.post(f"/{COMMUNITY}/chat/resume", json=_body())

        assert session.pending_call is None
        from langchain_core.messages import ToolMessage

        assert any(
            isinstance(m, ToolMessage) and m.tool_call_id == CALL_ID for m in session.messages
        ), "the browser result was not recorded, so the next turn would be rejected"

    def test_run_ones_citations_reach_the_reply(self, client: TestClient, monkeypatch) -> None:
        """The endpoint hands the parked call's citations to run 2.

        Checked here rather than below the endpoint, because dropping the argument at
        this call site is exactly the slip the module docstring describes: run 2 would
        number from [1] again and every test one layer down would stay green.
        """
        from dataclasses import replace

        from src.agents.content import CitationMark

        session = _parked_session()
        mark = CitationMark(
            marker=1, source="https://doc.example/alpha", title="Alpha", cited_text="x"
        )
        session.set_pending_call(replace(session.pending_call, carried_citations=(mark,)))
        monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant",
            lambda *_a, **_k: _assistant(),
        )

        response = client.post(f"/{COMMUNITY}/chat/resume", json=_body())

        events = [
            json.loads(line[len("data: ") :])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        done = next(e for e in events if e["event"] == "done")
        assert done["citations"] == [
            {
                "marker": 1,
                "source": "https://doc.example/alpha",
                "title": "Alpha",
                "cited_text": "x",
            }
        ]


class TestTheRunBudget:
    """The endpoint is what turns a parked call's run count into the next run's budget."""

    def _resume_at(self, client, monkeypatch, runs_before: int) -> list[dict]:
        from dataclasses import replace

        from tests.helpers.chat_models import tool_call_response

        session = _parked_session()
        session.set_pending_call(replace(session.pending_call, runs_before=runs_before))
        # The model asks for another run every time; only the budget can stop it.
        monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant",
            lambda *_a, **k: _assistant(
                responses=[
                    tool_call_response(
                        "execute_code", {"code": "again", "description": "d"}, OTHER_CALL_ID
                    ),
                    AIMessage(content="That is as far as the runs go."),
                ],
                browser_runs_left=k.get("browser_runs_left"),
            ),
        )
        response = client.post(f"/{COMMUNITY}/chat/resume", json=_body())
        return [
            json.loads(line[len("data: ") :])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]

    def test_the_last_result_in_budget_gets_no_further_run(
        self, client: TestClient, monkeypatch
    ) -> None:
        from src.core.limits import MAX_BROWSER_RUNS_PER_REPLY

        events = self._resume_at(client, monkeypatch, runs_before=MAX_BROWSER_RUNS_PER_REPLY - 1)

        names = [e["event"] for e in events]
        assert "tool_request" not in names, "the reply ran code past its budget"
        assert "done" in names

    def test_a_result_inside_the_budget_may_ask_again(
        self, client: TestClient, monkeypatch
    ) -> None:
        from src.core.limits import MAX_BROWSER_RUNS_PER_REPLY

        events = self._resume_at(client, monkeypatch, runs_before=MAX_BROWSER_RUNS_PER_REPLY - 2)

        request = next(e for e in events if e["event"] == "tool_request")
        assert request["call_id"] == OTHER_CALL_ID
        session = _get_session_store(COMMUNITY)["sess-parked"]
        assert session.pending_call.runs_before == MAX_BROWSER_RUNS_PER_REPLY - 1
