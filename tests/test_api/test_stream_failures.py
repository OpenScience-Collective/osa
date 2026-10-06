"""A stream that fails says what failed, tries a failure that is worth it once more, and
tells the reader the truth about it (release review, finding 3; issue #578).

A throttle, a read timeout and a request the provider refused as invalid (a 400 for a
reasoning field the model does not take) are different failures. A refusal fails the same
way every time, so the reader is told so; a model that failed in a way that can clear is
reported as unavailable, with the ask to choose another; and the log says which failure it
was. A stream that fails fast before the reader has seen anything is run once more.

What is real: the graph, the router's two streams, the metrics database, and the provider
clients. Bedrock's goes through botocore with the network hook staged to refuse, time out,
send an exception event, end with no ``messageStop``, or die part way with urllib3's own
error, and to answer differently the second time; Anthropic's and OpenRouter's through
their real clients with only the HTTP answer staged. A scripted model raises the exceptions
no provider call produces.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any, Literal, NoReturn
from unittest.mock import patch

import httpx
import httpx2
import pytest
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_aws.chat_models.bedrock_converse import _parse_stream_event
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool

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
from src.core.services.stream_retry import RetryOutcome
from src.metrics.db import init_metrics_db, metrics_connection
from src.metrics.middleware import MetricsMiddleware
from tests.helpers.anthropic_wire import message_stream, refusal_with, served_by
from tests.helpers.bedrock_wire import (
    CUT_SHORT,
    EVENT_STREAM,
    Wire,
    converse_stream,
    dropped_stream_error,
    frame,
    refusal,
    stalled_stream_error,
)
from tests.helpers.chat_models import (
    ScriptedChatModel,
    StreamingScriptedChatModel,
    tool_call_response,
)
from tests.helpers.openrouter import FakeOpenRouter, HttpError, stream_of
from tests.helpers.provider_replies import (
    COMMUNITY,
    ORIGIN,
    QUESTION,
    collect,
    community_config,
    real_request,
    text_chunk,
)

BEDROCK_MODEL = sorted(BEDROCK_MODELS)[0]
ENDPOINT = "https://bedrock-runtime.us-east-2.amazonaws.com"

#: What each stream tells a reader when a failure was not a model call's (a tool of ours
#: failed, say), so nothing is known about retrying. Spelled out here, not imported: it is
#: the wording a reader sees, and a change to it should fail.
UNRECOGNIZED_TEXT = {
    "ask": "An error occurred while generating the response. Please try again.",
    "chat": "An error occurred while processing your request.",
}

#: What either stream tells a reader when their model failed in a way that can clear by
#: itself: there is no automatic switch to another model, so they are asked to make it.
UNAVAILABLE_TEXT = (
    "The current model ({model}) is not available right now. Please choose another model."
)
BEDROCK_UNAVAILABLE = UNAVAILABLE_TEXT.format(model=BEDROCK_MODEL)

paths = pytest.mark.parametrize("path", ["ask", "chat"])


@pytest.fixture(autouse=True)
def _no_wait_before_a_retry(monkeypatch):
    """The second try waits so a failure can clear; a test has nothing to wait for."""
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
    assert events[-1]["message"] == BEDROCK_UNAVAILABLE
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
        wire = Wire(llm, body, EVENT_STREAM)

        events = await _run(path, llm, BEDROCK_MODEL)

        _assert_retryable(events, _failure_records(caplog), "throttlingException")
        # A throttle that arrives inside the stream is not tried again either: botocore
        # cannot retry it, but a second try a moment later would only add load to the
        # account being throttled. The reader is asked to choose another model instead.
        assert len(wire.requests) == 1
        assert _retry_records(caplog) == []

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


#: A whole answer, the second try's reply when the first was ``CUT_SHORT``.
ANSWER: dict[str, Any] = {
    "body": converse_stream(["Hello", " there"]),
    "content_type": EVENT_STREAM,
}


def _retry_logs(caplog: pytest.LogCaptureFixture, starts_with: str) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "src.core.services.stream_retry" and r.getMessage().startswith(starts_with)
    ]


def _retry_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """The records that say a request is being tried a second time."""
    return [r for r in caplog.records if r.__dict__.get("retry") is True]


class TestARetryBeforeTheReaderSawAnything:
    """A model call that fails fast before any output is run once more (issue #578), and
    nothing is retried that the reader has already seen part of."""

    @paths
    async def test_a_stream_cut_short_is_answered_by_the_second_try(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        llm = _bedrock_llm()
        wire = Wire(llm, **CUT_SHORT, then=[ANSWER])

        events = await _run(path, llm, BEDROCK_MODEL)

        assert "error" not in [e["event"] for e in events]
        assert events[-1]["event"] == "done", events
        assert events[-1]["content"] == "Hello there"
        assert len(wire.requests) == 2
        assert _failure_records(caplog) == [], "nothing failed for the reader"
        (record,) = _retry_records(caplog)
        assert record.levelno == logging.WARNING
        text = record.getMessage()
        expected_in_log = [
            COMMUNITY,
            BEDROCK_MODEL,
            "req-failure",
            "ConnectionError",
            "missing messageStop",
            "connection",
            *(["sess-failure"] if path == "chat" else []),
        ]
        for expected in expected_in_log:
            assert expected in text, f"{expected!r} missing from {text!r}"
        assert (record.__dict__["error_type"], record.__dict__["failure_kind"]) == (
            "ConnectionError",
            "connection",
        )
        assert record.__dict__["endpoint"] == f"/{COMMUNITY}/{path}"
        recovered = _retry_logs(caplog, "The second try")
        assert [r.levelno for r in recovered] == [logging.INFO]
        assert [r["status_code"] for r in _rows()] == [200]

    @paths
    async def test_a_second_failure_ends_the_stream_after_one_retry(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **CUT_SHORT)

        # A regression that retried without bound would loop here; fail it instead.
        events = await asyncio.wait_for(_run(path, llm, BEDROCK_MODEL), timeout=30)

        assert events[-1]["event"] == "error", events
        assert events[-1]["message"] == BEDROCK_UNAVAILABLE
        assert events[-1]["retryable"] is True
        assert len(wire.requests) == 2, "one retry and no more"
        assert len(_retry_records(caplog)) == 1
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info, (
            "a failure that did not clear when tried again is not one that clears by itself"
        )
        assert "(after one retry)" in record.getMessage()
        (row,) = _rows()
        assert row["status_code"] == 500
        assert row["error_message"] == "ConnectionError (after one retry)"

    @paths
    async def test_a_stall_is_not_retried(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """It has spent the whole read timeout; a second one would double the wait."""
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **CUT_SHORT, then_raises=stalled_stream_error())

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "error", events
        assert events[-1]["message"] == BEDROCK_UNAVAILABLE
        assert events[-1]["retryable"] is True, (
            "classified as a model failure, so the event carries retryable"
        )
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
    async def test_a_connection_that_dropped_part_way_is_tried_again(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(
            llm,
            **CUT_SHORT,
            then_raises=dropped_stream_error(),
            then=[ANSWER],
        )

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "done", events
        assert len(wire.requests) == 2
        assert len(_retry_records(caplog)) == 1

    @paths
    async def test_a_failure_that_took_long_to_arrive_is_not_retried(
        self, path: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reader has waited that long already; a second try could take as long again."""
        monkeypatch.setattr(stream_retry, "RETRY_WINDOW_SECONDS", -1.0)
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **CUT_SHORT, then=[ANSWER])

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "error", events
        assert len(wire.requests) == 1
        assert _retry_records(caplog) == []

    @paths
    async def test_a_throttle_is_not_retried_here(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """botocore and the Anthropic SDK retry one with backoff before the response
        begins; a second try a second later only adds load to the account being throttled."""
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        Wire(llm, **refusal("ThrottlingException", "Too many requests"))

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "error", events
        assert _retry_records(caplog) == []
        assert len(_failure_records(caplog)) == 1

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

        assert events[-1]["message"] == UNRECOGNIZED_TEXT[path]
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

        assert events[-1]["message"] == BEDROCK_UNAVAILABLE
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
        assert events[-1]["message"] == UNRECOGNIZED_TEXT[path]
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
        assert _model_unavailable(BEDROCK_MODEL) == BEDROCK_UNAVAILABLE

    def test_it_still_reads_when_the_model_is_not_known(self) -> None:
        assert _model_unavailable(None) == (
            "The current model is not available right now. Please choose another model."
        )


class TestTheReaderLeavesDuringTheWait:
    async def test_cancelling_during_the_delay_stops_cleanly_with_one_call(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A disconnect while the second try waits is the reader leaving: no second call
        starts, the cancellation propagates, and the log says why the retry never ran."""
        monkeypatch.setattr(stream_retry, "RETRY_DELAY_SECONDS", 30.0)
        caplog.set_level(logging.INFO)
        model = _failing(EndpointConnectionError(endpoint_url=ENDPOINT))
        assistant = CommunityAssistant(model=model, config=community_config(), preload_docs=False)
        graph = assistant.build_graph()
        state = {
            "messages": [HumanMessage(content=QUESTION)],
            "retrieved_docs": [],
            "tool_calls": [],
        }

        async def consume() -> None:
            events = stream_retry.astream_events_with_retry(
                graph,
                state,
                {},
                community_id=COMMUNITY,
                model="some-model",
                endpoint="/x/ask",
                request_id="req-cancel",
                outcome=RetryOutcome(),
            )
            async for _ in events:
                pass

        task = asyncio.create_task(consume())
        for _ in range(100):
            assert not task.done(), task.exception()
            if _retry_records(caplog):
                break
            await asyncio.sleep(0.05)
        assert _retry_records(caplog), "the first try failed and the wait began"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert model.calls == 1, "no second call started"
        left = [r for r in caplog.records if "The reader left" in r.getMessage()]
        assert len(left) == 1 and "req-cancel" in left[0].getMessage()


def _service_error_in_the_stream(code: str) -> dict[str, Any]:
    """A stream that opens and then carries the service's own exception event."""
    body = frame("messageStart", {"role": "assistant"}) + frame(
        code, {"message": "boom"}, message_type="exception"
    )
    return {"body": body, "content_type": EVENT_STREAM}


class TestWhatElseIsTriedAgain:
    @paths
    async def test_a_service_error_inside_the_stream_is_answered_by_the_second_try(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **_service_error_in_the_stream("modelStreamErrorException"), then=[ANSWER])

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "done", events
        assert len(wire.requests) == 2
        (record,) = _retry_records(caplog)
        assert record.__dict__["failure_kind"] == "unavailable"

    @paths
    async def test_a_service_error_that_comes_twice_ends_the_stream_at_error_level(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **_service_error_in_the_stream("serviceUnavailableException"))

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["message"] == BEDROCK_UNAVAILABLE
        assert len(wire.requests) == 2
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info
        assert "(after one retry)" in record.getMessage()
        (row,) = _rows()
        assert row["error_message"].endswith("(after one retry)"), row["error_message"]

    @paths
    async def test_openrouter_unavailable_is_answered_by_the_second_try(
        self, path: str, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        server = FakeOpenRouter()
        monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
        try:
            server.reply(HttpError(503, {"error": {"message": "down"}}), stream_of("Hi"))
            llm = create_openrouter_llm(model=OPENROUTER_MODEL, api_key="sk-or-test")

            events = await _run(path, llm, OPENROUTER_MODEL)
            requests = len(server.requests)
        finally:
            server.close()

        assert events[-1]["event"] == "done", events
        assert requests == 2
        assert len(_retry_records(caplog)) == 1

    @paths
    async def test_openrouter_throttle_is_not_tried_again(
        self, path: str, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Decided, not assumed: LiteLLM does not retry a throttle here, so for OpenRouter
        nothing does. A throttle is the one kind left to the reader's choice of model."""
        caplog.set_level(logging.WARNING)
        server = FakeOpenRouter()
        monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
        try:
            server.reply(HttpError(429, {"error": {"message": "slow down"}}), stream_of("Hi"))
            llm = create_openrouter_llm(model=OPENROUTER_MODEL, api_key="sk-or-test")

            events = await _run(path, llm, OPENROUTER_MODEL)
            requests = len(server.requests)
        finally:
            server.close()

        assert events[-1]["message"] == UNAVAILABLE_TEXT.format(model=OPENROUTER_MODEL)
        assert requests == 1
        assert _retry_records(caplog) == []

    @paths
    async def test_anthropic_overloaded_inside_the_stream_is_answered_by_the_second_try(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        calls: list[int] = []

        def handler(_request):
            calls.append(1)
            if len(calls) == 1:
                opening = message_stream([]).decode().split("\n\n")[0] + "\n\n"
                error = (
                    "event: error\n"
                    'data: {"type":"error","error":{"type":"overloaded_error",'
                    '"message":"Overloaded"}}\n\n'
                )
                return httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream", "x-should-retry": "false"},
                    content=(opening + error).encode(),
                )
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=message_stream(["Hello", " there"]),
            )

        with served_by(handler):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-test", settings=Settings(_env_file=None)
            )
            events = await _run(path, llm, DEFAULT_MODEL)

        assert events[-1]["event"] == "done", events
        assert len(calls) == 2
        (record,) = _retry_records(caplog)
        assert record.__dict__["failure_kind"] == "unavailable"


class TestWordingForAProviderFailureNobodyRecognizes:
    @paths
    async def test_it_is_called_unavailable_and_makes_no_claim_about_retrying(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        with pytest.raises(ValueError, match="unsupported stream event") as raised:
            _parse_stream_event({"somethingNewEvent": {"x": 1}})
        model = _failing(raised.value)

        events = await _run(path, model, BEDROCK_MODEL)

        assert events[-1]["message"] == BEDROCK_UNAVAILABLE
        assert "retryable" not in events[-1], "nothing is claimed about retrying"
        assert model.calls == 1
        assert _retry_records(caplog) == []
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR


class TestEveryKindOfOutputEndsTheChanceToRetry:
    """The unit tests pin what counts as output by event kind. These run each kind through
    the real graph and both streams: each is something the reader was shown (or a tool
    that ran), so a second try would show it twice."""

    @paths
    async def test_a_tool_call_alone(self, path: str, caplog: pytest.LogCaptureFixture) -> None:
        """The OpenAI-style shape: no text, only the call being written."""
        caplog.set_level(logging.WARNING)
        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": "lookup", "args": "", "id": "call_1", "index": 0}],
        )
        model = _FailingChatModel(
            chunk_script=[[chunk]], error=EndpointConnectionError(endpoint_url=ENDPOINT)
        )

        events = await _run(path, model, "some-model")

        assert [e["event"] for e in events] == [
            *(["session"] if path == "chat" else []),
            "tool_call",
            "error",
        ]
        assert model.calls == 1
        assert _retry_records(caplog) == []

    @paths
    async def test_reasoning_alone(self, path: str, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        chunk = AIMessageChunk(content=[{"type": "thinking", "thinking": "hmm", "index": 0}])
        model = _FailingChatModel(
            chunk_script=[[chunk]], error=EndpointConnectionError(endpoint_url=ENDPOINT)
        )

        events = await _run(path, model, "some-model")

        names = [e["event"] for e in events]
        assert "thinking" in names and names[-1] == "error"
        assert model.calls == 1
        assert _retry_records(caplog) == []

    @paths
    async def test_a_tool_that_ran_after_a_model_that_does_not_stream(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No chunk events exist here, so only the finished model call and the tool's own
        events say progress was made."""
        caplog.set_level(logging.WARNING)
        runs: list[str] = []

        @tool
        def lookup(query: str) -> str:
            """Look something up."""
            runs.append(query)
            return "found"

        class _FailsSecond(ScriptedChatModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                if self.calls >= 1:
                    self.calls += 1
                    raise EndpointConnectionError(endpoint_url=ENDPOINT)
                return super()._generate(messages, stop, run_manager, **kwargs)

        model = _FailsSecond(responses=[tool_call_response("lookup", {"query": "x"}, "call_1")])

        events = await _run(path, model, "some-model", [lookup])

        assert runs == ["x"], "the tool ran once"
        assert events[-1]["event"] == "error"
        assert model.calls == 2
        assert _retry_records(caplog) == []


class TestTheRetryOnTheResumePath:
    """``/chat/resume`` continues a reply after the browser ran code, so its first model
    call carries the browser's result. That is where a second try matters most, and it
    has to send the very same request."""

    async def test_the_second_try_sends_the_same_request_and_is_labeled_for_resume(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **CUT_SHORT, then=[ANSWER])
        assistant = CommunityAssistant(model=llm, config=community_config(), preload_docs=False)
        wrapped = AssistantWithMetrics(
            assistant=assistant, model=BEDROCK_MODEL, key_source="platform"
        )
        resume = f"/{COMMUNITY}/chat/resume"
        call = {
            "name": "run_code",
            "args": {"code": "1+1"},
            "id": "toolu_01abc",
            "type": "tool_call",
        }
        initial = [
            HumanMessage(content=QUESTION),
            AIMessage(content="", tool_calls=[call]),
            ToolMessage(content="RESULT-FROM-THE-BROWSER", tool_call_id="toolu_01abc"),
        ]
        session = ChatSession("sess-resume", COMMUNITY)
        session.add_user_message(QUESTION)

        with patch("src.api.routers.community.create_community_assistant", return_value=wrapped):
            events = await collect(
                _stream_chat_response(
                    COMMUNITY,
                    session,
                    None,
                    None,
                    None,
                    http_request=real_request("req-resume"),
                    endpoint=resume,
                    initial_messages=initial,
                )
            )

        assert events[-1]["event"] == "done", events
        assert len(wire.requests) == 2
        assert wire.requests[0].body == wire.requests[1].body
        assert b"RESULT-FROM-THE-BROWSER" in wire.requests[1].body
        (record,) = _retry_records(caplog)
        assert record.__dict__["endpoint"] == resume


class TestTheReaderLeavesTheRouterDuringTheWait:
    async def test_the_turn_is_released_and_no_second_call_starts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The chat stream's ``finally`` releases the session's turn however the generator
        ends; with a wait in the middle, a disconnect there must still release it."""
        monkeypatch.setattr(stream_retry, "RETRY_DELAY_SECONDS", 30.0)
        llm = _bedrock_llm()
        wire = Wire(llm, **CUT_SHORT, then=[ANSWER])
        assistant = CommunityAssistant(model=llm, config=community_config(), preload_docs=False)
        wrapped = AssistantWithMetrics(
            assistant=assistant, model=BEDROCK_MODEL, key_source="platform"
        )
        session = ChatSession("sess-cancel", COMMUNITY)
        session.add_user_message(QUESTION)

        with patch("src.api.routers.community.create_community_assistant", return_value=wrapped):
            stream = _stream_chat_response(
                COMMUNITY, session, None, None, None, http_request=real_request("req-cancel")
            )
            task = asyncio.create_task(collect(stream))
            for _ in range(100):
                assert not task.done(), task.exception()
                if wire.requests:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert len(wire.requests) == 1
        assert session.begin_turn() is True, "the turn was released"
