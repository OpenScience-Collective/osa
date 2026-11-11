"""A reader who turns off "Share usage data" (#706) leaves a usage row with no conversation.

The widget's Privacy choices dialog sends ``share_usage: false`` in the body of /chat,
/chat/resume and /ask. The request still runs, and its row still says what it cost (the
tokens, the cost, the tier, the counts, the status and the duration), so the budget adds
up. What it no longer carries is what links the row to a conversation or to what the
assistant did: ``conversation_id`` and ``tools_called`` (and the Langfuse trace id).

These run the real app with the real metrics middleware, on a temporary metrics database,
one request with the choice off and one with it on, per route and per way the reply is
produced (streamed or not). The model is the scripted one the other router tests use
(``tests/helpers/provider_replies.py``); everything past it is the real code path.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.community import _get_session_store, create_community_router
from src.assistants.registry import registry
from src.core.config.community import BudgetConfig
from src.metrics.budget import check_budget
from src.metrics.db import init_metrics_db, metrics_connection
from src.metrics.middleware import MetricsMiddleware
from tests.helpers.provider_replies import (
    ANSWER,
    COMMUNITY,
    ORIGIN,
    PROVIDERS,
    QUESTION,
    USAGE,
    assistant_for,
    community_config,
    scripted_reply,
)
from tests.test_api import test_tool_call_streaming as resume_fixtures
from tests.test_api.test_tool_call_streaming import _anthropic_call

PROVIDER = next(p for p in PROVIDERS if p.name == "anthropic")
LOOKUP = _anthropic_call("lookup_scriptedreply_docs", "toolu_01optout", {"query": "tags"})

# What an opted-out row keeps, and what it drops.
KEPT_NOT_NULL = (
    "request_id",
    "timestamp",
    "community_id",
    "endpoint",
    "method",
    "duration_ms",
    "status_code",
    "model",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated_cost",
)
DROPPED = ("conversation_id", "tools_called", "langfuse_trace_id")


@pytest.fixture(autouse=True)
def metrics_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    init_metrics_db()


@pytest.fixture
def client(monkeypatch):
    from src.api.config import get_settings

    monkeypatch.setenv("REQUIRE_API_AUTH", "false")
    get_settings.cache_clear()
    registry.register_from_config(community_config())
    registry.register_from_config(resume_fixtures._config())
    _get_session_store(COMMUNITY).clear()
    _get_session_store(resume_fixtures.COMMUNITY).clear()
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)
    app.include_router(create_community_router(COMMUNITY))
    app.include_router(create_community_router(resume_fixtures.COMMUNITY))
    yield TestClient(app)
    _get_session_store(COMMUNITY).clear()
    _get_session_store(resume_fixtures.COMMUNITY).clear()
    registry._assistants.pop(COMMUNITY, None)
    registry._assistants.pop(resume_fixtures.COMMUNITY, None)
    monkeypatch.undo()
    get_settings.cache_clear()


def _rows() -> list[dict]:
    with metrics_connection() as conn:
        conn.row_factory = lambda cursor, row: dict(
            zip([c[0] for c in cursor.description], row, strict=True)
        )
        return conn.execute("SELECT * FROM request_log ORDER BY timestamp").fetchall()


def _script() -> list[list]:
    """One run that calls a server tool, then one that answers; both report their usage."""
    return [
        [*LOOKUP, *PROVIDER.end(PROVIDER.finished, True, False)],
        scripted_reply(PROVIDER, ANSWER),
    ]


def _serve_scripted(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.api.routers.community.create_community_assistant",
        lambda *_a, **_k: assistant_for(PROVIDER, _script()),
    )


def _serve_resume(monkeypatch) -> None:
    """The browser-run community's assistant, replaying a search run and then an answer
    that both report their usage."""
    search = _anthropic_call(
        "nemar_search_datasets", resume_fixtures.SEARCH_CALL_ID, resume_fixtures.SEARCH_ARGS
    )
    script = [
        [*search, *PROVIDER.end(PROVIDER.finished, True, False)],
        scripted_reply(PROVIDER, ANSWER),
    ]
    monkeypatch.setattr(
        "src.api.routers.community.create_community_assistant",
        lambda *_a, **_k: resume_fixtures._assistant(script),
    )


def _chat(client: TestClient, share: bool | None, *, stream: bool) -> None:
    body: dict = {"message": QUESTION, "stream": stream, "session_id": f"sess-{share}-{stream}"}
    if share is not None:
        body["share_usage"] = share
    response = client.post(f"/{COMMUNITY}/chat", headers={"Origin": ORIGIN}, json=body)
    assert response.status_code == 200, response.text


def _ask(client: TestClient, share: bool | None, *, stream: bool) -> None:
    body: dict = {"question": QUESTION, "stream": stream}
    if share is not None:
        body["share_usage"] = share
    response = client.post(f"/{COMMUNITY}/ask", headers={"Origin": ORIGIN}, json=body)
    assert response.status_code == 200, response.text


def _resume(client: TestClient, share: bool | None) -> None:
    resume_fixtures._parked_session()
    body: dict = {
        "session_id": "sess-parked-toolcall",
        "result": {
            "call_id": resume_fixtures.CODE_CALL_ID,
            "status": "ok",
            "summary": "peak 10.2 Hz",
        },
        "client_tools": ["execute_code"],
    }
    if share is not None:
        body["share_usage"] = share
    response = client.post(
        f"/{resume_fixtures.COMMUNITY}/chat/resume",
        headers={"Origin": resume_fixtures.ALLOWED_ORIGIN},
        json=body,
    )
    assert response.status_code == 200, response.text
    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["event"] == "done", events


def _routes():
    """Each route and way of answering: (id, serve, send)."""
    return [
        pytest.param(_serve_scripted, lambda c, s: _chat(c, s, stream=True), id="chat-streaming"),
        pytest.param(_serve_scripted, lambda c, s: _chat(c, s, stream=False), id="chat-plain"),
        pytest.param(_serve_resume, _resume, id="chat-resume"),
        pytest.param(_serve_scripted, lambda c, s: _ask(c, s, stream=False), id="ask-plain"),
        pytest.param(_serve_scripted, lambda c, s: _ask(c, s, stream=True), id="ask-streaming"),
    ]


@pytest.mark.parametrize(("serve", "send"), _routes())
class TestOptedOutRow:
    def test_a_default_request_keeps_its_conversation_and_tools(
        self, client, monkeypatch, serve, send
    ) -> None:
        serve(monkeypatch)
        send(client, None)

        (row,) = _rows()
        assert row["tools_called"] is not None, "the scripted run called a tool"
        assert json.loads(row["tools_called"]), row
        # /ask has no conversation; every other route's row names one.
        if "/ask" not in row["endpoint"]:
            assert row["conversation_id"] is not None

    def test_choosing_to_share_is_the_default(self, client, monkeypatch, serve, send) -> None:
        serve(monkeypatch)
        send(client, True)

        (row,) = _rows()
        assert row["tools_called"] is not None

    def test_an_opted_out_row_drops_the_conversation_and_tools_but_keeps_the_cost(
        self, client, monkeypatch, serve, send
    ) -> None:
        serve(monkeypatch)
        send(client, None)
        send(client, False)

        default, opted_out = _rows()
        for column in DROPPED:
            assert opted_out[column] is None, column
        for column in KEPT_NOT_NULL:
            assert opted_out[column] is not None, column
        # A turn is two model runs, each reporting USAGE.
        assert opted_out["input_tokens"] >= USAGE["input_tokens"]
        assert opted_out["output_tokens"] >= USAGE["output_tokens"]
        # Everything the budget and the cost report read is the default request's.
        for column in (
            "community_id",
            "endpoint",
            "method",
            "model",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "estimated_cost",
            "status_code",
            "tier",
            "key_source",
            "stream",
            "tool_call_count",
        ):
            assert opted_out[column] == default[column], column
        assert opted_out["request_id"] != default["request_id"]

    def test_the_budget_counts_an_opted_out_request(self, client, monkeypatch, serve, send) -> None:
        serve(monkeypatch)
        send(client, None)
        send(client, False)

        default, opted_out = _rows()
        assert opted_out["estimated_cost"] > 0
        community = default["community_id"]
        config = BudgetConfig(daily_limit_usd=1000.0, monthly_limit_usd=1000.0)
        with metrics_connection() as conn:
            status = check_budget(community, config, conn)
        assert status.daily_spend_usd == pytest.approx(
            default["estimated_cost"] + opted_out["estimated_cost"]
        )


class TestTheBodyField:
    def test_it_defaults_to_sharing_and_must_be_a_boolean(self, client, monkeypatch) -> None:
        _serve_scripted(monkeypatch)
        response = client.post(
            f"/{COMMUNITY}/chat",
            headers={"Origin": ORIGIN},
            json={"message": QUESTION, "stream": False, "share_usage": "maybe"},
        )
        assert response.status_code == 422
        # The middleware's stub row for the refused body has no model run to price.
        assert all(row["model"] is None for row in _rows())

    def test_resume_still_refuses_any_other_extra_field(self, client) -> None:
        resume_fixtures._parked_session()
        response = client.post(
            f"/{resume_fixtures.COMMUNITY}/chat/resume",
            headers={"Origin": resume_fixtures.ALLOWED_ORIGIN},
            json={
                "session_id": "sess-parked-toolcall",
                "result": {"call_id": resume_fixtures.CODE_CALL_ID, "status": "ok"},
                "share_usage": False,
                "share_everything": True,
            },
        )
        assert response.status_code == 422

    def test_a_failed_request_is_opted_out_too(self, client, monkeypatch) -> None:
        """The middleware's own row (no handler detail) honors the choice as well."""

        def boom(*_a, **_k):
            raise RuntimeError("provider down")

        monkeypatch.setattr("src.api.routers.community.create_community_assistant", boom)
        response = client.post(
            f"/{COMMUNITY}/ask",
            headers={"Origin": ORIGIN},
            json={"question": QUESTION, "stream": False, "share_usage": False},
        )
        assert response.status_code >= 400
        (row,) = _rows()
        assert row["conversation_id"] is None and row["tools_called"] is None
        assert row["status_code"] == response.status_code
