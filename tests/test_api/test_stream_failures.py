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
from typing import Any, Literal, NoReturn
from unittest.mock import patch

import httpx
import pytest
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_aws.chat_models.bedrock_converse import _parse_stream_event
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError

from src.api.config import Settings
from src.api.routers.community import (
    AssistantWithMetrics,
    ChatSession,
    _get_session_store,
    _model_unavailable,
    _stream_ask_response,
    _stream_chat_response,
    create_community_router,
)
from src.assistants.community import CommunityAssistant
from src.core.services import stream_retry
from src.core.services.anthropic_llm import create_anthropic_llm
from src.core.services.anthropic_models import BEDROCK_MODELS, DEFAULT_MODEL
from src.core.services.bedrock_llm import _bedrock_client, create_bedrock_llm
from src.core.services.litellm_llm import DEFAULT_MODEL as OPENROUTER_MODEL
from src.core.services.litellm_llm import create_openrouter_llm
from src.metrics.db import init_metrics_db, metrics_connection
from src.metrics.middleware import MetricsMiddleware
from tests.helpers.anthropic_wire import refusal_with, served_by
from tests.helpers.bedrock_wire import EVENT_STREAM, Wire, converse_stream, frame, refusal
from tests.helpers.chat_models import StreamingScriptedChatModel
from tests.helpers.openrouter import FakeOpenRouter, HttpError
from tests.helpers.provider_replies import (
    COMMUNITY,
    ORIGIN,
    QUESTION,
    community_config,
    real_request,
    text_chunk,
)

BEDROCK_MODEL = sorted(BEDROCK_MODELS)[0]
ENDPOINT = "https://bedrock-runtime.us-east-2.amazonaws.com"

#: What each stream tells a reader when a failure was not a model call's and nothing is
#: known about retrying (a tool of ours failed, say). Spelled out here, not imported: it is
#: the wording a reader sees, and a change to it should fail.
RETRYABLE_TEXT = {
    "ask": "An error occurred while generating the response. Please try again.",
    "chat": "An error occurred while processing your request.",
}

#: What either stream tells a reader when their model failed in a way that can clear by
#: itself: there is no automatic switch to another model, so they are asked to make it.
UNAVAILABLE_TEXT = (
    "The current model ({model}) is not available right now. Please choose another model."
)

paths = pytest.mark.parametrize("path", ["ask", "chat"])


@pytest.fixture(autouse=True)
def _no_wait_before_a_retry(monkeypatch):
    """The second try waits a second so a throttle can clear; a test has nothing to wait for."""
    monkeypatch.setattr(stream_retry, "RETRY_DELAY_SECONDS", 0.0)


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


async def _run(
    path: str,
    llm: Any,
    model: str,
    tools: list[Any] | None = None,
    key_source: Literal["byok", "community", "platform"] = "platform",
) -> list[dict]:
    """Stream one turn of ``/ask`` or ``/chat`` over ``llm`` through the real graph."""
    from tests.helpers.provider_replies import collect

    assistant = CommunityAssistant(
        model=llm, config=community_config(), preload_docs=False, additional_tools=tools
    )
    wrapped = AssistantWithMetrics(assistant=assistant, model=model, key_source=key_source)
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


def _assert_retryable(events: list[dict], records: list[logging.LogRecord], detail: str) -> None:
    """The reader is told their model is unavailable and to choose another; the log says
    what happened, at WARNING."""
    assert events[-1]["event"] == "error", events
    assert events[-1]["message"] == UNAVAILABLE_TEXT.format(model=BEDROCK_MODEL)
    # The widget shows the message for a few seconds, so it has to be read at a glance.
    assert len(events[-1]["message"]) <= 120, events[-1]["message"]
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

        _assert_retryable(events, _failure_records(caplog), "ThrottlingException")
        assert [r["status_code"] for r in _rows()] == [500]

    @paths
    async def test_a_read_timeout_is_retryable(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        Wire(llm, raises=ReadTimeoutError(endpoint_url=ENDPOINT))

        events = await _run(path, llm, BEDROCK_MODEL)

        _assert_retryable(events, _failure_records(caplog), "ReadTimeoutError")

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

        _assert_retryable(events, _failure_records(caplog), "throttlingException")

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


#: A stream that opens and then ends with no ``messageStop``: what Bedrock sent for GPT-6
#: Luna in issue 578, and what ``langchain-aws`` raises as ``ConnectionError``.
CUT_SHORT = {"body": frame("messageStart", {"role": "assistant"}), "content_type": EVENT_STREAM}
ANSWER = {"body": converse_stream(["Hello", " there"]), "content_type": EVENT_STREAM}


def _retry_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "src.core.services.stream_retry" and r.getMessage().startswith("Retrying")
    ]


class TestARetryBeforeTheReaderSawAnything:
    """A model call that fails fast before any output is run once more (issue 578), and
    nothing is retried that the reader has already seen part of."""

    @paths
    async def test_a_stream_cut_short_is_answered_by_the_second_try(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **CUT_SHORT, then=[ANSWER])

        events = await _run(path, llm, BEDROCK_MODEL)

        assert [e["event"] for e in events if e["event"] == "error"] == []
        assert events[-1]["event"] == "done", events
        assert events[-1]["content"] == "Hello there"
        assert len(wire.requests) == 2
        assert _failure_records(caplog) == [], "nothing failed for the reader"
        (record,) = _retry_records(caplog)
        assert record.levelno == logging.WARNING
        text = record.getMessage()
        for expected in (COMMUNITY, BEDROCK_MODEL, "req-failure", "ConnectionError"):
            assert expected in text, f"{expected!r} missing from {text!r}"
        assert [r["status_code"] for r in _rows()] == [200]

    @paths
    async def test_a_second_failure_ends_the_stream_after_one_retry(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **CUT_SHORT)

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "error", events
        assert events[-1]["message"] == UNAVAILABLE_TEXT.format(model=BEDROCK_MODEL)
        assert events[-1]["retryable"] is True
        assert len(wire.requests) == 2, "one retry and no more"
        assert len(_retry_records(caplog)) == 1
        assert len(_failure_records(caplog)) == 1, "the reader's error is logged once"
        assert [r["status_code"] for r in _rows()] == [500]

    @paths
    async def test_a_stall_is_not_retried(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """It has spent the whole read timeout; a second one would double the wait."""
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(
            llm,
            frame("messageStart", {"role": "assistant"}),
            EVENT_STREAM,
            then_raises=Urllib3ReadTimeoutError(None, None, "Read timed out."),
        )

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "error", events
        assert events[-1]["message"] == UNAVAILABLE_TEXT.format(model=BEDROCK_MODEL)
        assert events[-1]["retryable"] is True, "classified, so the reader is told it can clear"
        assert len(wire.requests) == 1
        assert _retry_records(caplog) == []
        (record,) = _failure_records(caplog)
        assert "ReadTimeoutError" in record.getMessage() and "retryable=yes" in record.getMessage()

    @paths
    async def test_a_refusal_is_not_retried(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **refusal("ValidationException", "reasoning_effort is not supported"))

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["retryable"] is False
        assert len(wire.requests) == 1
        assert _retry_records(caplog) == []

    @paths
    async def test_a_reply_that_began_is_not_run_again(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The reader has seen "Hel": a second try would write it twice."""
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        partial = frame("messageStart", {"role": "assistant"}) + frame(
            "contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "Hel"}}
        )
        wire = Wire(llm, partial, EVENT_STREAM, then=[ANSWER])

        events = await _run(path, llm, BEDROCK_MODEL)

        assert [e["content"] for e in events if e["event"] == "content"] == ["Hel"]
        assert events[-1]["event"] == "error", events
        assert len(wire.requests) == 1
        assert _retry_records(caplog) == []


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

        assert events[-1]["message"] == UNAVAILABLE_TEXT.format(model=DEFAULT_MODEL)
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


#: What a reader is told when the provider refused a key, whose key it was. A new
#: conversation fixes neither, so neither says to start one.
KEY_REFUSED_TEXT = (
    "The provider refused your API key. Check that it is valid and can use this model."
)
SERVER_KEY_TEXT = (
    "The assistant is unavailable because of a server problem, and trying again will not "
    "help. Please contact support."
)
CANNOT_RETRY_TEXT = {
    "ask": "The assistant could not process this request, and trying again will not help.",
    "chat": (
        "The assistant could not process this request, and trying again will not help. "
        "Start a new conversation."
    ),
}


class TestAKeyTheProviderRefused:
    """The fix for a refused key depends on whose it is: the caller's own (BYOK) is theirs
    to check, the platform's or a community's is the operator's. Neither is helped by a new
    conversation, and ``/ask`` has no conversation to start."""

    @paths
    async def test_a_callers_own_key_is_theirs_to_check_and_is_not_an_error(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        with served_by(refusal_with(401, "authentication_error", "invalid x-api-key")):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-revoked", settings=Settings(_env_file=None)
            )
            events = await _run(path, llm, DEFAULT_MODEL, key_source="byok")

        assert events[-1]["message"] == KEY_REFUSED_TEXT
        assert events[-1]["retryable"] is False
        assert events[-1]["error_id"] and events[-1]["request_id"] == "req-failure"
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.WARNING, "the operator did nothing wrong"
        assert not record.exc_info, "a revoked key of the caller's needs no traceback"
        assert "(HTTP 401)" in record.getMessage() and record.key_source == "byok"
        assert record.error_id == events[-1]["error_id"]

    @paths
    async def test_the_platforms_own_key_is_an_error_with_its_traceback(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        with served_by(refusal_with(401, "authentication_error", "invalid x-api-key")):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-platform", settings=Settings(_env_file=None)
            )
            events = await _run(path, llm, DEFAULT_MODEL, key_source="platform")

        assert events[-1]["message"] == SERVER_KEY_TEXT
        assert len(events[-1]["message"]) <= 120
        assert events[-1]["retryable"] is False
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info
        assert record.key_source == "platform"

    @paths
    async def test_a_community_key_is_the_operators_to_fix_too(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        with served_by(refusal_with(403, "permission_error", "no access to this model")):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-community", settings=Settings(_env_file=None)
            )
            events = await _run(path, llm, DEFAULT_MODEL, key_source="community")

        assert events[-1]["message"] == SERVER_KEY_TEXT
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info

    @paths
    async def test_a_request_the_provider_refused_keeps_the_generic_text_with_a_callers_key(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Only a refused credential is the key's fault: a 400 is still the request's."""
        caplog.set_level(logging.WARNING)
        with served_by(refusal_with(400, "invalid_request_error", "effort is not supported")):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-test", settings=Settings(_env_file=None)
            )
            events = await _run(path, llm, DEFAULT_MODEL, key_source="byok")

        assert events[-1]["message"] == CANNOT_RETRY_TEXT[path]
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info

    async def test_ask_has_no_conversation_to_start_and_chat_does(self) -> None:
        with served_by(refusal_with(400, "invalid_request_error", "effort is not supported")):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-test", settings=Settings(_env_file=None)
            )
            ask = await _run("ask", llm, DEFAULT_MODEL)
            chat = await _run("chat", llm, DEFAULT_MODEL)

        assert "conversation" not in ask[-1]["message"]
        assert "Start a new conversation" in chat[-1]["message"]


class TestTheMetricsRowSaysWhy:
    """``agent_errors`` counts the rows that carry an ``error_message``, and the error
    event's ``request_id`` points at its row: a failed stream's row used to say neither
    what failed nor that it was the agent's (only the 502 of an empty reply did)."""

    @staticmethod
    def _agent_errors() -> int:
        from src.metrics.queries import get_quality_metrics

        with metrics_connection() as conn:
            buckets = get_quality_metrics(COMMUNITY, conn)["buckets"]
        return sum(bucket["agent_errors"] for bucket in buckets)

    @paths
    async def test_a_provider_failure_is_named_and_counted(self, path: str) -> None:
        llm = _bedrock_llm()
        Wire(llm, **refusal("ThrottlingException", "Too many requests, please wait"))

        events = await _run(path, llm, BEDROCK_MODEL)

        (row,) = _rows()
        assert row["request_id"] == events[-1]["request_id"]
        assert row["status_code"] == 500
        assert "ThrottlingException" in row["error_message"]
        assert "Too many requests" not in row["error_message"], (
            "the provider's words stay in the log"
        )
        assert self._agent_errors() == 1

    @paths
    async def test_an_unexpected_failure_is_named_and_counted(self, path: str) -> None:
        events = await _run(path, _failing(RuntimeError("a bug of ours")), "some-model")

        (row,) = _rows()
        assert row["request_id"] == events[-1]["request_id"]
        assert "RuntimeError" in row["error_message"]
        assert "a bug of ours" not in row["error_message"]
        assert self._agent_errors() == 1

    async def test_the_provider_error_in_a_value_error_is_named_too(self) -> None:
        """``langchain-aws`` raises a service error as a ValueError; that row says why too."""
        with pytest.raises(ValueError, match="Received AWS exception") as raised:
            _parse_stream_event({"throttlingException": {"message": "Too many requests"}})

        await _run("chat", _failing(raised.value), BEDROCK_MODEL)

        (row,) = _rows()
        assert row["status_code"] == 500 and "throttlingException" in row["error_message"]

    @paths
    async def test_a_request_that_was_just_invalid_is_not_an_agent_error(self, path: str) -> None:
        await _run(path, _failing(ValueError("Message too long (20000 chars)")), "m")

        (row,) = _rows()
        assert row["status_code"] == 400 and row["error_message"] is None
        assert self._agent_errors() == 0


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
        """It used to be labeled "Invalid request" (and, in a chat, shown as a session
        limit), as if the reader had sent something wrong."""
        caplog.set_level(logging.WARNING)
        with pytest.raises(ValueError, match="Received AWS exception") as raised:
            _parse_stream_event({"throttlingException": {"message": "Too many requests"}})

        events = await _run(path, _failing(raised.value), BEDROCK_MODEL)

        assert events[-1]["message"] == UNAVAILABLE_TEXT.format(model=BEDROCK_MODEL)
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
        _assert_retryable(events, records, "ConnectionError")
        assert "Model call failed" in records[0].getMessage()


class _RaisingChatModel(_FailingChatModel):
    """Raises ``error`` on a call that is not streamed, as a provider client does."""

    def _generate(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise self.error


@pytest.fixture
def client(monkeypatch):
    from src.api.config import get_settings
    from src.assistants.registry import registry

    # What is under test is the failure, not the API key check (see test_tool_call_streaming).
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


class TestAChatThatIsNotStreamed:
    """The streams classify what a model call raised; a ``ValueError`` from ``langchain-aws``
    (a service exception event it could not raise as a ``ClientError``) used to come back
    from ``/chat`` as HTTP 400 carrying the provider's text, as if the caller had sent a bad
    request."""

    @staticmethod
    def _post(client: TestClient, error: Exception, monkeypatch):
        assistant = CommunityAssistant(
            model=_RaisingChatModel(chunk_script=[[]], error=error),
            config=community_config(),
            preload_docs=False,
        )
        wrapped = AssistantWithMetrics(
            assistant=assistant, model=BEDROCK_MODEL, key_source="platform"
        )
        monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant", lambda *_a, **_k: wrapped
        )
        return client.post(
            f"/{COMMUNITY}/chat",
            headers={"Origin": ORIGIN},
            json={"message": QUESTION, "stream": False},
        )

    def test_a_provider_value_error_is_a_logged_server_error_not_the_callers_400(
        self, client: TestClient, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        with pytest.raises(ValueError, match="Received AWS exception") as raised:
            _parse_stream_event({"throttlingException": {"message": "Too many requests"}})

        response = self._post(client, raised.value, monkeypatch)

        assert response.status_code == 500
        assert "Too many requests" not in response.text, "the provider's text is not echoed"
        assert "throttlingException" not in response.text
        (record,) = [r for r in caplog.records if "Error in chat endpoint" in r.getMessage()]
        assert record.levelno == logging.ERROR and record.exc_info
        assert "throttlingException" in record.getMessage()

    def test_a_stream_event_langchain_aws_cannot_parse_is_the_providers_too(
        self, client: TestClient, monkeypatch
    ) -> None:
        with pytest.raises(ValueError, match="unsupported stream event") as raised:
            _parse_stream_event({"somethingNewEvent": {"x": 1}})

        response = self._post(client, raised.value, monkeypatch)

        assert response.status_code == 500
        assert "somethingNewEvent" not in response.text

    def test_a_value_error_of_ours_is_still_the_requests_fault(
        self, client: TestClient, monkeypatch
    ) -> None:
        response = self._post(client, ValueError("Message too long (20000 chars)"), monkeypatch)

        assert response.status_code == 400
        assert response.json()["detail"] == "Message too long (20000 chars)"


class _FailsOnTheSecondCall(StreamingScriptedChatModel):
    """Replays its script for the first call and raises ``error`` from every call after."""

    error: Exception

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        if self.calls >= 1:
            self.calls += 1
            raise self.error
        yield from super()._stream(messages, stop, run_manager, **kwargs)


class TestNoRetryOnceAToolHasRun:
    """A retry runs the turn again from the start, which would run the tool a second time
    and show the reader its call twice. Past a tool call, a failure ends the stream."""

    @paths
    async def test_a_failure_after_a_tool_ran_is_not_retried(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        from tests.test_api.test_tool_call_streaming import _anthropic_call

        caplog.set_level(logging.WARNING)
        runs: list[str] = []

        @tool
        def lookup(query: str) -> str:
            """Look something up."""
            runs.append(query)
            return "found"

        model = _FailsOnTheSecondCall(
            chunk_script=[_anthropic_call("lookup", "toolu_01lookup", {"query": "x"})],
            error=EndpointConnectionError(endpoint_url=ENDPOINT),
        )

        events = await _run(path, model, "some-model", [lookup])

        assert [e["event"] for e in events].count("tool_start") == 1
        assert runs == ["x"], "the tool ran once"
        assert events[-1]["event"] == "error", events
        assert events[-1]["retryable"] is True
        assert model.calls == 2, "the failed call was not tried again"
        assert _retry_records(caplog) == []


class TestTheUnavailableModelMessage:
    def test_it_names_the_model_and_asks_for_another(self) -> None:
        assert _model_unavailable("openai.gpt-6-luna") == UNAVAILABLE_TEXT.format(
            model="openai.gpt-6-luna"
        )

    def test_it_still_reads_when_the_model_is_not_known(self) -> None:
        assert _model_unavailable(None) == (
            "The current model is not available right now. Please choose another model."
        )
