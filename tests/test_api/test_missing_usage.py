"""A request whose cost row is incomplete says so in the log (release review, finding 2).

Two things left the row wrong with nothing to see. ``_extract_token_usage`` answered zeros
when a finished model run carried no usage metadata, so the row had a NULL cost, and
``_ProviderUsage.final`` fell back to LiteLLM's own token estimate when the provider
reported none, so the row was priced without cache or reasoning counts. Neither raises.

Now the request's model runs are counted as they finish, and one warning is logged when
the request ends, naming the community, the model and the request id. As in
``test_cut_off_replies.py``, the chat model is a scripted one yielding each adapter's
chunks; the last class drives the real Bedrock and OpenRouter client stacks.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk

from src.api.routers.community import (
    AssistantWithMetrics,
    ChatSession,
    _get_session_store,
    _stream_ask_response,
    _stream_chat_response,
    create_community_router,
)
from src.assistants.community import CommunityAssistant
from src.core.services.anthropic_models import BEDROCK_MODELS
from src.core.services.litellm_llm import DEFAULT_MODEL as OPENROUTER_MODEL
from src.metrics.db import init_metrics_db, metrics_connection
from src.metrics.middleware import MetricsMiddleware
from tests.helpers.provider_replies import (
    ANSWER,
    COMMUNITY,
    ORIGIN,
    PROVIDERS,
    QUESTION,
    Provider,
    assistant_for,
    collect,
    community_config,
    real_request,
    scripted_reply,
)
from tests.test_api.test_tool_call_streaming import _anthropic_call

provider_param = pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.name)
LOOKUP = _anthropic_call("lookup_scriptedreply_docs", "toolu_01usage", {"query": "tags"})


@pytest.fixture(autouse=True)
def metrics_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    init_metrics_db()


def _rows() -> list[dict]:
    with metrics_connection() as conn:
        conn.row_factory = lambda cursor, row: dict(
            zip([c[0] for c in cursor.description], row, strict=True)
        )
        return conn.execute("SELECT * FROM request_log ORDER BY timestamp").fetchall()


def _usage_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "Token usage is incomplete" in r.getMessage()]


def _assert_one_warning(
    caplog: pytest.LogCaptureFixture, model: str, consequence: str, request_id: str = "req-usage"
) -> None:
    records = _usage_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    for expected in (COMMUNITY, model, request_id, consequence):
        assert expected in message, f"{expected!r} missing from {message!r}"


async def _chat(provider: Provider, script: list[list[AIMessageChunk]]) -> list[dict]:
    session = ChatSession("sess-usage", COMMUNITY)
    session.add_user_message(QUESTION)
    with patch(
        "src.api.routers.community.create_community_assistant",
        return_value=assistant_for(provider, script),
    ):
        return await collect(
            _stream_chat_response(
                COMMUNITY, session, None, None, None, http_request=real_request("req-usage")
            )
        )


async def _ask(provider: Provider, script: list[list[AIMessageChunk]]) -> list[dict]:
    with patch(
        "src.api.routers.community.create_community_assistant",
        return_value=assistant_for(provider, script),
    ):
        return await collect(
            _stream_ask_response(
                COMMUNITY, QUESTION, None, None, None, http_request=real_request("req-usage")
            )
        )


@provider_param
class TestStreamedChat:
    async def test_runs_that_report_no_usage_warn_once_for_the_whole_request(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Three model runs, none with usage: one warning, and a NULL cost on the row."""
        caplog.set_level(logging.WARNING)
        script = [
            [*LOOKUP, *provider.end(provider.finished, False, False)],
            [*LOOKUP, *provider.end(provider.finished, False, False)],
            scripted_reply(provider, ANSWER, usage=False),
        ]

        events = await _chat(provider, script)

        assert events[-1]["event"] == "done" and events[-1]["content"] == ANSWER
        _assert_one_warning(caplog, provider.model, "missing")
        assert "3 of 3 model runs" in _usage_records(caplog)[0].getMessage()
        (row,) = _rows()
        assert row["input_tokens"] is None and row["estimated_cost"] is None

    async def test_some_runs_without_usage_leave_the_cost_too_low(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        script = [
            [*LOOKUP, *provider.end(provider.finished, True, False)],
            scripted_reply(provider, ANSWER, usage=False),
        ]

        await _chat(provider, script)

        _assert_one_warning(caplog, provider.model, "too low")
        assert "1 of 2 model runs" in _usage_records(caplog)[0].getMessage()
        (row,) = _rows()
        assert row["input_tokens"] is not None

    async def test_runs_that_report_usage_say_nothing(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        await _chat(provider, [[*LOOKUP, *provider.end(provider.finished, True, False)]])
        await _chat(provider, [scripted_reply(provider, ANSWER)])

        assert _usage_records(caplog) == []

    async def test_a_reply_cut_off_with_no_usage_warns_about_both(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The error path that ends a reply with no answer still says what it cost."""
        caplog.set_level(logging.WARNING)

        events = await _chat(provider, [scripted_reply(provider, "", cut_off=True, usage=False)])

        assert events[-1]["event"] == "error"
        _assert_one_warning(caplog, provider.model, "missing")


class TestAnEstimateIsNotTheProvidersNumber:
    async def test_an_estimate_leaves_the_cost_approximate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        provider = next(p for p in PROVIDERS if p.name == "openrouter")

        await _chat(provider, [scripted_reply(provider, ANSWER, estimated=True)])

        _assert_one_warning(caplog, provider.model, "approximate")
        assert "1 used LiteLLM's estimate" in _usage_records(caplog)[0].getMessage()


class TestAParkedBrowserCall:
    async def test_the_run_that_parks_a_call_warns_too(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A run that ends on a browser call returns through its own branch."""
        from tests.test_api.test_tool_call_streaming import COMMUNITY as BROWSER_COMMUNITY
        from tests.test_api.test_tool_call_streaming import _assistant as browser_assistant

        caplog.set_level(logging.WARNING)
        call = _anthropic_call("execute_code", "toolu_01park", {"code": "x", "description": "d"})
        session = ChatSession("sess-parked", BROWSER_COMMUNITY)
        session.add_user_message(QUESTION)
        with patch(
            "src.api.routers.community.create_community_assistant",
            return_value=browser_assistant([[*call, AIMessageChunk(content=[])]]),
        ):
            events = await collect(
                _stream_chat_response(
                    BROWSER_COMMUNITY,
                    session,
                    None,
                    None,
                    None,
                    http_request=real_request("req-usage"),
                    declared_client_tools={"execute_code"},
                )
            )

        assert events[-1]["event"] == "tool_request"
        records = _usage_records(caplog)
        assert len(records) == 1
        assert BROWSER_COMMUNITY in records[0].getMessage()


@provider_param
class TestStreamedAsk:
    async def test_runs_that_report_no_usage_warn_once(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        await _ask(provider, [scripted_reply(provider, ANSWER, usage=False)])

        _assert_one_warning(caplog, provider.model, "missing")

    async def test_runs_that_report_usage_say_nothing(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        await _ask(provider, [scripted_reply(provider, ANSWER)])

        assert _usage_records(caplog) == []


@pytest.fixture
def client(monkeypatch):
    from src.api.config import get_settings
    from src.assistants.registry import registry

    monkeypatch.setenv("REQUIRE_API_AUTH", "false")
    get_settings.cache_clear()
    registry.register_from_config(community_config())
    _get_session_store(COMMUNITY).clear()
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)
    app.include_router(create_community_router(COMMUNITY))
    yield TestClient(app)
    _get_session_store(COMMUNITY).clear()
    registry._assistants.pop(COMMUNITY, None)
    monkeypatch.undo()
    get_settings.cache_clear()


@provider_param
class TestWithoutStreaming:
    def _serve(self, monkeypatch, provider: Provider, script) -> None:
        monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant",
            lambda *_a, **_k: assistant_for(provider, script),
        )

    def test_ask_warns_once(
        self, provider: Provider, client: TestClient, monkeypatch, caplog
    ) -> None:
        caplog.set_level(logging.WARNING)
        self._serve(monkeypatch, provider, [scripted_reply(provider, ANSWER, usage=False)])

        response = client.post(
            f"/{COMMUNITY}/ask",
            headers={"Origin": ORIGIN},
            json={"question": QUESTION, "stream": False},
        )

        assert response.status_code == 200
        (row,) = _rows()
        _assert_one_warning(caplog, provider.model, "missing", request_id=row["request_id"])

    def test_chat_warns_once_and_only_for_this_turn(
        self, provider: Provider, client: TestClient, monkeypatch, caplog
    ) -> None:
        """Earlier turns in the history are not this request's runs."""
        caplog.set_level(logging.WARNING)
        post = lambda: client.post(  # noqa: E731 (a one-line helper for two calls)
            f"/{COMMUNITY}/chat",
            headers={"Origin": ORIGIN},
            json={"message": QUESTION, "session_id": "sess-usage-plain", "stream": False},
        )
        self._serve(monkeypatch, provider, [scripted_reply(provider, ANSWER, usage=False)])
        assert post().status_code == 200
        assert len(_usage_records(caplog)) == 1
        self._serve(monkeypatch, provider, [scripted_reply(provider, ANSWER)])

        assert post().status_code == 200

        assert len(_usage_records(caplog)) == 1, "the second turn reported its usage"


class TestThroughTheRealClients:
    async def test_bedrock_with_no_metadata_event(self, caplog: pytest.LogCaptureFixture) -> None:
        """``langchain-aws`` reads usage from the stream's ``metadata`` event and nowhere
        else; a stream that ends after ``messageStop`` has none."""
        from src.api.config import Settings
        from src.core.services.bedrock_llm import _bedrock_client, create_bedrock_llm
        from tests.helpers.bedrock_wire import EVENT_STREAM, Wire, converse_stream

        caplog.set_level(logging.WARNING)
        model = sorted(BEDROCK_MODELS)[0]
        _bedrock_client.cache_clear()
        try:
            llm = create_bedrock_llm(
                model,
                settings=Settings(
                    _env_file=None,
                    bedrock_api_key="test-bedrock-key",
                    bedrock_region="us-east-2",
                    bedrock_max_output_tokens=16000,
                ),
            )
            Wire(llm, converse_stream([ANSWER], usage={}), EVENT_STREAM)
            events = await _chat_with(llm, model)
        finally:
            _bedrock_client.cache_clear()

        assert events[-1]["content"] == ANSWER
        _assert_one_warning(caplog, model, "missing")

    async def test_openrouter_that_sends_no_usage(
        self, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from src.core.services.litellm_llm import create_openrouter_llm
        from tests.helpers.openrouter import FakeOpenRouter, stream_of

        caplog.set_level(logging.WARNING)
        server = FakeOpenRouter()
        monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
        try:
            server.reply(stream_of(ANSWER))
            llm = create_openrouter_llm(model=OPENROUTER_MODEL, api_key="sk-or-test")
            events = await _chat_with(llm, OPENROUTER_MODEL)
        finally:
            server.close()

        assert events[-1]["content"] == ANSWER
        _assert_one_warning(caplog, OPENROUTER_MODEL, "approximate")

    async def test_openrouter_that_sends_its_usage_says_nothing(
        self, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from src.core.services.litellm_llm import create_openrouter_llm
        from tests.helpers.openrouter import FakeOpenRouter, stream_of

        caplog.set_level(logging.WARNING)
        server = FakeOpenRouter()
        monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
        try:
            usage = {"prompt_tokens": 40, "completion_tokens": 9, "total_tokens": 49}
            server.reply(stream_of(ANSWER, usage=usage))
            llm = create_openrouter_llm(model=OPENROUTER_MODEL, api_key="sk-or-test")
            await _chat_with(llm, OPENROUTER_MODEL)
        finally:
            server.close()

        assert _usage_records(caplog) == []


async def _chat_with(llm, model: str) -> list[dict]:
    assistant = CommunityAssistant(model=llm, config=community_config(), preload_docs=False)
    wrapped = AssistantWithMetrics(assistant=assistant, model=model, key_source="platform")
    session = ChatSession("sess-real-usage", COMMUNITY)
    session.add_user_message(QUESTION)
    with patch("src.api.routers.community.create_community_assistant", return_value=wrapped):
        return await collect(
            _stream_chat_response(
                COMMUNITY, session, None, None, None, http_request=real_request("req-usage")
            )
        )
