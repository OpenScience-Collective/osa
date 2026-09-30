"""A stream that fails says what failed, and only promises a retry that can work
(release review, finding 3).

A throttle, a read timeout and a request the provider refused as invalid (a 400 for a
reasoning field the model does not take) all used to reach the reader as "try again" and
the log as one undifferentiated error. A refusal fails the same way every time, so telling
the reader to retry is wrong, and the log did not say which failure it was.

What is real: the graph, the router's two streams, the metrics database, and the provider
clients. Bedrock's goes through botocore with the network hook staged to refuse, time out or
send an exception event; Anthropic's and OpenRouter's through their real clients with only
the HTTP answer staged. A scripted model raises the exceptions no provider call produces.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from botocore.exceptions import ReadTimeoutError
from langchain_aws.chat_models.bedrock_converse import _parse_stream_event
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool

from src.api.config import Settings
from src.api.routers.community import (
    AssistantWithMetrics,
    ChatSession,
    _stream_ask_response,
    _stream_chat_response,
)
from src.assistants.community import CommunityAssistant
from src.core.services.anthropic_llm import create_anthropic_llm
from src.core.services.anthropic_models import BEDROCK_MODELS, DEFAULT_MODEL
from src.core.services.bedrock_llm import _bedrock_client, create_bedrock_llm
from src.core.services.litellm_llm import DEFAULT_MODEL as OPENROUTER_MODEL
from src.core.services.litellm_llm import create_openrouter_llm
from src.metrics.db import init_metrics_db, metrics_connection
from tests.helpers.anthropic_wire import refusal_with, served_by
from tests.helpers.bedrock_wire import EVENT_STREAM, Wire, frame, refusal
from tests.helpers.chat_models import StreamingScriptedChatModel
from tests.helpers.openrouter import FakeOpenRouter, HttpError
from tests.helpers.provider_replies import (
    COMMUNITY,
    QUESTION,
    community_config,
    real_request,
    text_chunk,
)

BEDROCK_MODEL = sorted(BEDROCK_MODELS)[0]
ENDPOINT = "https://bedrock-runtime.us-east-2.amazonaws.com"

#: What each stream tells a reader when a retry can work or nothing is known. Spelled out
#: here, not imported: it is the wording a reader sees, and a change to it should fail.
RETRYABLE_TEXT = {
    "ask": "An error occurred while generating the response. Please try again.",
    "chat": "An error occurred while processing your request.",
}

paths = pytest.mark.parametrize("path", ["ask", "chat"])


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


async def _run(path: str, llm: Any, model: str, tools: list[Any] | None = None) -> list[dict]:
    """Stream one turn of ``/ask`` or ``/chat`` over ``llm`` through the real graph."""
    from tests.helpers.provider_replies import collect

    assistant = CommunityAssistant(
        model=llm, config=community_config(), preload_docs=False, additional_tools=tools
    )
    wrapped = AssistantWithMetrics(assistant=assistant, model=model, key_source="platform")
    request = real_request("req-failure")
    with patch("src.api.routers.community.create_community_assistant", return_value=wrapped):
        if path == "ask":
            return await collect(
                _stream_ask_response(COMMUNITY, QUESTION, None, None, None, http_request=request)
            )
        session = ChatSession("sess-failure", COMMUNITY)
        session.add_user_message(QUESTION)
        return await collect(
            _stream_chat_response(COMMUNITY, session, None, None, None, http_request=request)
        )


def _failure_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "src.api.routers.community"
        and (
            "Model call failed" in r.getMessage() or "Unexpected streaming error" in r.getMessage()
        )
    ]


def _bedrock_llm():
    _bedrock_client.cache_clear()
    return create_bedrock_llm(
        BEDROCK_MODEL,
        settings=Settings(
            _env_file=None,
            bedrock_api_key="test-bedrock-key",
            bedrock_region="us-east-2",
            bedrock_max_output_tokens=16000,
        ),
    )


@pytest.fixture(autouse=True)
def _fresh_bedrock_clients():
    yield
    _bedrock_client.cache_clear()


def _assert_retryable(
    events: list[dict], records: list[logging.LogRecord], path: str, detail: str
) -> None:
    """The reader's text is what it always was; the log says what happened, at WARNING."""
    assert events[-1]["event"] == "error", events
    assert events[-1]["message"] == RETRYABLE_TEXT[path]
    assert events[-1]["retryable"] is True
    assert events[-1]["error_id"]
    assert events[-1]["request_id"] == "req-failure", "the reader's report finds its row"
    assert len(records) == 1, [r.getMessage() for r in records]
    record = records[0]
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    for expected in (COMMUNITY, BEDROCK_MODEL, "req-failure", detail, "retryable=yes"):
        assert expected in message, f"{expected!r} missing from {message!r}"
    assert not record.exc_info, "a failure that can clear by itself needs no traceback"
    assert record.error_id == events[-1]["error_id"]
    assert record.retryable is True


def _assert_permanent(events: list[dict], records: list[logging.LogRecord], detail: str) -> None:
    """The reader is not told to retry; the log says so, at ERROR with the traceback."""
    assert events[-1]["event"] == "error", events
    message = events[-1]["message"]
    assert "trying again will not help" in message
    assert "try again" not in message.lower().replace("trying again", "")
    # The widget shows the message for a few seconds: short enough to read at a glance,
    # and the id is a field of its own (and in the log), not part of the text.
    assert len(message) <= 120, message
    assert events[-1]["error_id"] and events[-1]["error_id"] not in message
    assert events[-1]["request_id"] == "req-failure"
    assert events[-1]["retryable"] is False
    assert len(records) == 1, [r.getMessage() for r in records]
    record = records[0]
    assert record.levelno == logging.ERROR
    text = record.getMessage()
    for expected in (COMMUNITY, BEDROCK_MODEL, "req-failure", detail, "retryable=no"):
        assert expected in text, f"{expected!r} missing from {text!r}"
    assert events[-1]["error_id"] in text and record.error_id == events[-1]["error_id"]
    assert record.exc_info
    assert record.retryable is False


class TestBedrock:
    @paths
    async def test_a_throttle_is_retryable(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        Wire(llm, **refusal("ThrottlingException", "Too many requests, please wait"))

        events = await _run(path, llm, BEDROCK_MODEL)

        _assert_retryable(events, _failure_records(caplog), path, "ThrottlingException")
        assert [r["status_code"] for r in _rows()] == [500]

    @paths
    async def test_a_read_timeout_is_retryable(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        Wire(llm, raises=ReadTimeoutError(endpoint_url=ENDPOINT))

        events = await _run(path, llm, BEDROCK_MODEL)

        _assert_retryable(events, _failure_records(caplog), path, "ReadTimeoutError")

    @paths
    async def test_a_service_exception_inside_the_stream_is_retryable(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        body = frame("messageStart", {"role": "assistant"}) + frame(
            "throttlingException", {"message": "Too many requests"}, message_type="exception"
        )
        Wire(llm, body, EVENT_STREAM)

        events = await _run(path, llm, BEDROCK_MODEL)

        _assert_retryable(events, _failure_records(caplog), path, "throttlingException")

    @paths
    async def test_a_validation_error_is_not(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The request the release review names: a reasoning field the model rejects."""
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        Wire(llm, **refusal("ValidationException", "reasoning_effort is not supported"))

        events = await _run(path, llm, BEDROCK_MODEL)

        _assert_permanent(events, _failure_records(caplog), "ValidationException")
        assert "reasoning_effort" not in events[-1]["message"], (
            "the provider's words stay in the log"
        )

    @paths
    async def test_a_refused_credential_is_not_retryable_either(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        Wire(llm, **refusal("AccessDeniedException", "You do not have access to the model"))

        events = await _run(path, llm, BEDROCK_MODEL)

        _assert_permanent(events, _failure_records(caplog), "AccessDeniedException")


class TestAnthropic:
    @paths
    async def test_a_rate_limit_is_retryable(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        with served_by(refusal_with(429, "rate_limit_error", "slow down")):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-test", settings=Settings(_env_file=None)
            )
            events = await _run(path, llm, DEFAULT_MODEL)

        assert events[-1]["message"] == RETRYABLE_TEXT[path]
        assert events[-1]["retryable"] is True
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.WARNING
        assert "(HTTP 429)" in record.getMessage() and DEFAULT_MODEL in record.getMessage()

    @paths
    async def test_a_bad_request_is_not(self, path: str, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        with served_by(refusal_with(400, "invalid_request_error", "effort is not supported")):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-test", settings=Settings(_env_file=None)
            )
            events = await _run(path, llm, DEFAULT_MODEL)

        assert "trying again will not help" in events[-1]["message"]
        assert events[-1]["retryable"] is False
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR
        assert "(HTTP 400)" in record.getMessage()


class TestOpenRouter:
    @paths
    async def test_a_bad_request_is_not(
        self, path: str, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        server = FakeOpenRouter()
        monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
        try:
            server.reply(HttpError(400, {"error": {"message": "reasoning is not supported"}}))
            llm = create_openrouter_llm(model=OPENROUTER_MODEL, api_key="sk-or-test")

            events = await _run(path, llm, OPENROUTER_MODEL)
        finally:
            server.close()

        assert "trying again will not help" in events[-1]["message"]
        assert events[-1]["retryable"] is False
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR
        assert "(HTTP 400)" in record.getMessage()
        assert OPENROUTER_MODEL in record.getMessage()


class _FailingChatModel(StreamingScriptedChatModel):
    """Replays its script, then raises ``error``, as a stream that dies part way."""

    error: Exception

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from super()._stream(messages, stop, run_manager, **kwargs)
        raise self.error


def _failing(error: Exception, *, said: str = "") -> _FailingChatModel:
    script = [[text_chunk(said)] if said else []]
    return _FailingChatModel(chunk_script=script, error=error)


class TestWhatNoProviderCallRaises:
    @paths
    async def test_an_unrecognized_error_keeps_the_streams_text_and_says_so_in_the_log(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        events = await _run(path, _failing(RuntimeError("a bug of ours")), "some-model")

        assert events[-1]["message"] == RETRYABLE_TEXT[path]
        assert "retryable" not in events[-1], "nothing is claimed about an unknown failure"
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info
        text = record.getMessage()
        assert "Unexpected streaming error" in text
        assert "retryable=unknown" in text and "RuntimeError" in text and "some-model" in text

    @paths
    async def test_the_text_already_streamed_is_kept_before_the_error(self, path: str) -> None:
        events = await _run(path, _failing(RuntimeError("late"), said="Part of it. "), "m")

        names = [e["event"] for e in events]
        assert "content" in names and names[-1] == "error"
        assert names.index("content") < names.index("error")

    @paths
    async def test_a_service_error_langchain_aws_raised_as_a_value_error_is_not_the_readers(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """It used to be labelled "Invalid request" (and, in a chat, shown as a session
        limit), as if the reader had sent something wrong."""
        caplog.set_level(logging.WARNING)
        with pytest.raises(ValueError, match="Received AWS exception") as raised:
            _parse_stream_event({"throttlingException": {"message": "Too many requests"}})

        events = await _run(path, _failing(raised.value), BEDROCK_MODEL)

        assert events[-1]["message"] == RETRYABLE_TEXT[path]
        assert events[-1]["retryable"] is True
        assert "Invalid request" not in events[-1]["message"]
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.WARNING
        assert "throttlingException" in record.getMessage()
        assert [r["status_code"] for r in _rows()] == [500]

    @paths
    async def test_a_value_error_of_ours_is_still_the_requests_fault(self, path: str) -> None:
        events = await _run(path, _failing(ValueError("Message too long (20000 chars)")), "m")

        assert [r["status_code"] for r in _rows()] == [400]
        if path == "ask":
            assert events[-1]["message"] == "Invalid request: Message too long (20000 chars)"
            assert events[-1]["retryable"] is False
        else:
            assert events[-1]["message"] == "Message too long (20000 chars)"


def _flaky_tool(error: Exception):
    """A real server tool whose call fails with ``error``, as a tool that fetches a page
    fails. langgraph's ``ToolNode`` re-raises what it does not recognize, so the stream
    sees this exactly as it would see any tool of ours failing."""

    @tool
    def flaky_lookup(query: str) -> str:  # noqa: ARG001
        """Look something up over the network."""
        raise error

    return flaky_lookup


def _calls_the_tool() -> StreamingScriptedChatModel:
    from tests.test_api.test_tool_call_streaming import _anthropic_call

    return StreamingScriptedChatModel(
        chunk_script=[_anthropic_call("flaky_lookup", "toolu_01flaky", {"query": "x"})]
    )


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://docs.example/page")
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=request, response=httpx.Response(status, request=request)
    )


class TestAToolFailureIsNotAModelFailure:
    """The errors a tool that goes to the network raises are the ones this module used to
    read as the model's: ``httpx`` errors, the built-in ``TimeoutError`` and
    ``ConnectionError``. A tool of ours failing is a bug in a tool, so it is logged as one
    (ERROR, with the traceback) and the reader is told nothing about retrying."""

    @paths
    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("slow"),
            _http_status_error(404),
            _http_status_error(503),
            TimeoutError("fetch timed out"),
            ConnectionResetError("reset by peer"),
        ],
        ids=lambda e: type(e).__name__,
    )
    async def test_it_keeps_its_traceback_and_is_not_called_a_model_call(
        self, path: str, error: Exception, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        events = await _run(path, _calls_the_tool(), "some-model", [_flaky_tool(error)])

        assert events[-1]["event"] == "error"
        assert events[-1]["message"] == RETRYABLE_TEXT[path]
        assert "retryable" not in events[-1], "nothing is claimed about a failure of ours"
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR
        assert record.exc_info, "a tool failure needs its traceback"
        text = record.getMessage()
        assert "Unexpected streaming error" in text and "Model call failed" not in text
        assert "retryable=unknown" in text and type(error).__name__ in text
        assert [r["status_code"] for r in _rows()] == [500]


class TestWhatLangchainAwsRaisesItself:
    """The one built-in exception that is the model call's: ``langchain-aws`` raises
    ``ConnectionError`` when a stream ends without its ``messageStop`` event. It stays a
    retryable model failure, recognized by where it was raised, through the real client."""

    @paths
    async def test_a_stream_that_ends_with_no_message_stop_is_retryable(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        body = frame("messageStart", {"role": "assistant"}) + frame(
            "contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "Sensory-event "}}
        )
        Wire(llm, body, EVENT_STREAM)

        events = await _run(path, llm, BEDROCK_MODEL)

        records = _failure_records(caplog)
        _assert_retryable(events, records, path, "ConnectionError")
        assert "Model call failed" in records[0].getMessage()
