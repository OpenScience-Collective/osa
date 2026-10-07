"""A stream that fails says what failed, tries a failure that is worth it once more, and
tells the reader the truth about it (release review, finding 3; issue #578).

A throttle, a read timeout and a request the provider refused as invalid (a 400 for a
reasoning field the model does not take) are different failures. A refusal fails the same
way every time, so the reader is told so; a model that failed in a way that can clear is
reported as unavailable, with a model to try; and the log says which failure it
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
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any, Literal, NoReturn
from unittest.mock import patch

import botocore.endpoint
import httpx
import httpx2
import pytest
from botocore.eventstream import EventStreamError
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
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
from src.core.services.stream_retry import RetryState
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
#: itself: there is no automatic switch to another model, so they are told which to try and
#: asked to make the change (Claude Haiku 4.5, or Sonnet 5.5 when Haiku is the one that failed).
UNAVAILABLE_TEXT = "The current model ({model}) is not available right now. Try {suggested}, or choose another model."


def unavailable_text(model: str, suggested: str = "Claude Haiku 4.5") -> str:
    return UNAVAILABLE_TEXT.format(model=model, suggested=suggested)


BEDROCK_UNAVAILABLE = unavailable_text(BEDROCK_MODEL)

#: What the event carries for a client that can send the question again with the model.
HAIKU = {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5"}

paths = pytest.mark.parametrize("path", ["ask", "chat"])

#: Longer than any turn here takes even on a loaded machine, and short enough to fail a run
#: that retries forever.
STREAM_DEADLINE_SECONDS = 60

#: How many 50 ms polls a test gives the first try to fail and the wait to begin (30 s).
POLLS_FOR_THE_WAIT = 600


@pytest.fixture(autouse=True)
def _no_wait_before_a_retry(monkeypatch):
    """The second try waits so a failure can clear; a test has nothing to wait for."""
    monkeypatch.setattr(stream_retry, "RETRY_DELAY_SECONDS", 0.0)


class _NoBackoff:
    """``time`` as botocore sees it, with ``sleep`` skipped: its standard retry mode waits
    between the tries of a refused request, and those waits make up most of this
    file's run time (they run for seconds), and are not the behavior under test. The requests still happen, so
    the count of them is still the count of botocore's tries."""

    @staticmethod
    def sleep(_seconds: float) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@pytest.fixture(autouse=True)
def _no_botocore_backoff(monkeypatch):
    monkeypatch.setattr(botocore.endpoint, "time", _NoBackoff())


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
            stream = _stream_ask_response(
                COMMUNITY, QUESTION, None, None, None, http_request=request
            )
        else:
            session = ChatSession("sess-failure", COMMUNITY)
            session.add_user_message(QUESTION)
            stream = _stream_chat_response(
                COMMUNITY, session, None, None, None, http_request=request
            )
        # A retry that never stops (a regression in the guards) fails here instead of
        # hanging the suite: no turn in this file takes anything near this long.
        return await asyncio.wait_for(collect(stream), STREAM_DEADLINE_SECONDS)


def _failure_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "src.api.routers.community"
        and (
            "Model call failed" in r.getMessage()
            or "Unexpected streaming error" in r.getMessage()
            or "A run used all its steps" in r.getMessage()
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
    events: list[dict],
    records: list[logging.LogRecord],
    detail: str,
    *,
    level: int = logging.WARNING,
    traceback: bool = False,
) -> None:
    """The reader is told their model is unavailable and to choose another; the log says
    what happened: at WARNING for a throttle, at ERROR for an outage (a service that is
    unavailable, a lost connection, a read that timed out), and with the traceback too for
    one a retry would have covered and that reached the reader anyway."""
    assert events[-1]["event"] == "error", events
    assert events[-1]["message"] == BEDROCK_UNAVAILABLE
    # The widget shows the message in a banner and the reader takes it in at a glance.
    assert len(events[-1]["message"]) <= 120, events[-1]["message"]
    assert events[-1]["retryable"] is True
    assert events[-1]["suggested_model"] == HAIKU, "the message names a model, so the event does"
    assert events[-1]["error_id"]
    assert events[-1]["request_id"] == "req-failure", "the reader's report finds its row"
    assert len(records) == 1, [r.getMessage() for r in records]
    record = records[0]
    assert record.levelno == level
    message = record.getMessage()
    for expected in (COMMUNITY, BEDROCK_MODEL, "req-failure", detail, "retryable=yes"):
        assert expected in message, f"{expected!r} missing from {message!r}"
    assert bool(record.exc_info) is traceback
    assert record.error_id == events[-1]["error_id"]
    assert record.retryable is True


def _assert_permanent(events: list[dict], records: list[logging.LogRecord], detail: str) -> None:
    """The reader is not told to retry; the log says so, at ERROR with the traceback."""
    assert events[-1]["event"] == "error", events
    message = events[-1]["message"]
    assert "trying again will not help" in message
    assert "try again" not in message.lower().replace("trying again", "")
    # The widget shows the message in a banner: short enough to read at a glance,
    # and the id is a field of its own (and in the log), not part of the text.
    assert len(message) <= 120, message
    assert events[-1]["error_id"] and events[-1]["error_id"] not in message
    assert events[-1]["request_id"] == "req-failure"
    assert events[-1]["retryable"] is False
    assert "suggested_model" not in events[-1], "the message says trying again will not help"
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

        _assert_retryable(events, _failure_records(caplog), "ReadTimeoutError", level=logging.ERROR)

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
        assert wire.requests[0].body == wire.requests[1].body, "the same request, sent again"
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

        events = await _run(path, llm, BEDROCK_MODEL)

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
        assert record.levelno == logging.ERROR and not record.exc_info, (
            "an outage: ERROR, and its line names the class, so it carries no traceback"
        )

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
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info, (
            "it was worth a retry and reached the reader anyway: an error, with its traceback"
        )

    @paths
    async def test_a_second_failure_of_another_kind_is_an_error_with_its_traceback(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A throttle is a warning when it comes first; one that comes after a retry is the
        end of the cheap remedy, so it is an error with its traceback."""
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **CUT_SHORT, then=[_service_error_in_the_stream("throttlingException")])

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "error", events
        assert len(wire.requests) == 2
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info
        assert "(after one retry)" in record.getMessage()

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
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.WARNING, "a throttle is the account's, not an outage"
        assert not record.exc_info

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

    @paths
    async def test_a_connection_that_could_not_be_made_is_an_outage(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        Wire(llm, raises=EndpointConnectionError(endpoint_url="https://bedrock-runtime.example"))

        events = await _run(path, llm, BEDROCK_MODEL)

        _assert_retryable(
            events, _failure_records(caplog), "EndpointConnectionError", level=logging.ERROR
        )


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

        assert events[-1]["message"] == unavailable_text(DEFAULT_MODEL, "Claude Sonnet 5.5")
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


RATE_LIMITED_KEY_TEXT = "The provider is rate limiting your API key. Wait a moment and try again."


class TestAThrottleOnTheCallersOwnKey:
    """The model is fine, and another model on the same key would be limited the same way,
    so the reader is told about their key, not asked to choose a model."""

    @paths
    async def test_it_says_the_key_is_rate_limited(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        Wire(llm, **refusal("ThrottlingException", "Too many requests"))

        events = await _run(path, llm, BEDROCK_MODEL, key_source="byok")

        assert events[-1]["message"] == RATE_LIMITED_KEY_TEXT
        assert events[-1]["retryable"] is True
        assert "suggested_model" not in events[-1], "another model may be limited on the same key"

    @paths
    @pytest.mark.parametrize("key_source", ["platform", "community"])
    async def test_the_operators_own_key_throttled_still_says_the_model_is_unavailable(
        self, path: str, key_source: Literal["community", "platform"]
    ) -> None:
        """A community's key is the operator's, not the reader's: nothing to tell them about it."""
        llm = _bedrock_llm()
        Wire(llm, **refusal("ThrottlingException", "Too many requests"))

        events = await _run(path, llm, BEDROCK_MODEL, key_source=key_source)

        assert events[-1]["message"] == BEDROCK_UNAVAILABLE

    @paths
    async def test_another_failure_on_the_callers_key_is_not_called_a_throttle(
        self, path: str
    ) -> None:
        """A key is rate limited when the provider says so, not whenever a call on it fails."""
        llm = _bedrock_llm()
        Wire(llm, **_service_error_in_the_stream("serviceUnavailableException"))

        events = await _run(path, llm, BEDROCK_MODEL, key_source="byok")

        assert events[-1]["message"] == BEDROCK_UNAVAILABLE


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


def _stream_error() -> EventStreamError:
    """The service's exception event inside an open stream: a failure that can clear by
    itself and came after the response began, the kind that is tried again."""
    return EventStreamError(
        {"Error": {"Code": "modelStreamErrorException", "Message": "boom"}}, "ConverseStream"
    )


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
        assert "suggested_model" not in events[-1]
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info
        text = record.getMessage()
        assert "Unexpected streaming error" in text
        assert "retryable=unknown" in text and "RuntimeError" in text and "some-model" in text

    @paths
    async def test_an_error_that_cannot_say_what_it_is_still_gets_its_line_and_its_event(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A ``__str__`` that raises would drop the log line (and leave the reader's error
        id matching nothing), so the line carries a placeholder for the text."""

        class Unreadable(RuntimeError):
            def __str__(self) -> str:
                raise RuntimeError("no text for you")

        caplog.set_level(logging.WARNING)

        events = await _run(path, _failing(Unreadable()), "some-model")

        assert events[-1]["event"] == "error", events
        (record,) = _failure_records(caplog)
        assert "<Unreadable: text unreadable>" in record.getMessage()
        assert record.error_id == events[-1]["error_id"]

    @paths
    async def test_a_value_error_that_cannot_say_what_it_is_still_reaches_the_reader(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The branches for a ValueError of ours (an invalid request, a session limit) put its
        text in the log line and in the event; one that cannot print would cut the stream."""

        class UnreadableValueError(ValueError):
            def __str__(self) -> str:
                raise RuntimeError("no text for you")

        caplog.set_level(logging.WARNING)

        events = await _run(path, _failing(UnreadableValueError()), "some-model")

        placeholder = "<UnreadableValueError: text unreadable>"
        assert events[-1]["event"] == "error", events
        assert placeholder in events[-1]["message"]
        (record,) = [
            r
            for r in caplog.records
            if "Invalid input in streaming" in r.getMessage()
            or "Session limit error" in r.getMessage()
        ]
        assert placeholder in record.getMessage()

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


class TestARunThatUsesAllItsSteps:
    """A model that keeps calling a tool (a check, fix, check loop it does not converge in)
    ends in langgraph's ``GraphRecursionError`` at the step limit. The reader is told that,
    not that the service is unavailable, and which model to try."""

    @staticmethod
    def _looping_model() -> StreamingScriptedChatModel:
        from tests.test_api.test_tool_call_streaming import _anthropic_call

        return StreamingScriptedChatModel(
            chunk_script=[_anthropic_call("lookup", "toolu_01lookup", {"query": "x"})]
        )

    @staticmethod
    def _lookup_tool(runs: list[str]):
        @tool
        def lookup(query: str) -> str:
            """Look something up."""
            runs.append(query)
            return "found"

        return lookup

    @paths
    @pytest.mark.parametrize(
        ("model", "suggested"),
        [
            ("openai.gpt-oss-120b", HAIKU),
            ("claude-haiku-4-5", {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5"}),
        ],
    )
    async def test_the_reader_is_told_and_given_a_model(
        self,
        path: str,
        model: str,
        suggested: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING)
        runs: list[str] = []

        events = await _run(path, self._looping_model(), model, [self._lookup_tool(runs)])

        assert len(runs) > 5, "the tool ran again and again until the step limit"
        assert events[-1]["event"] == "error", events
        assert events[-1]["message"] == (
            f"The current model ({model}) used all its steps without finishing. "
            f"Try {suggested['label']}, or ask for a smaller part of the task."
        )
        assert events[-1]["suggested_model"] == suggested
        assert events[-1]["retryable"] is True
        assert events[-1]["request_id"] == "req-failure"
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.WARNING and not record.exc_info, (
            "the model's behavior, not an outage: nothing for an operator to chase"
        )
        assert record.failure_kind == "step_limit"
        assert record.getMessage().startswith("A run used all its steps (ID: "), (
            "named for what it is"
        )
        assert record.error_id == events[-1]["error_id"]
        (row,) = [r for r in _rows() if r["error_message"]]
        assert "GraphRecursionError" in row["error_message"]


class TestARunThatUsesAllItsStepsOnAnEndpointThatIsNotStreamed:
    """The same run, on ``/ask`` and ``/chat`` with ``stream: false``: the caller is told what
    happened and which model to try, and the operator gets a warning, not a paged error."""

    @pytest.mark.parametrize(
        ("path", "payload"),
        [
            ("ask", {"question": QUESTION, "stream": False}),
            ("chat", {"message": QUESTION, "stream": False}),
        ],
    )
    def test_it_says_so_and_names_a_model(
        self,
        client: TestClient,
        monkeypatch,
        caplog: pytest.LogCaptureFixture,
        path: str,
        payload: dict[str, Any],
    ) -> None:
        caplog.set_level(logging.WARNING)
        model = TestARunThatUsesAllItsSteps._looping_model()
        runs: list[str] = []
        assistant = CommunityAssistant(
            model=model,
            config=community_config(),
            preload_docs=False,
            additional_tools=[TestARunThatUsesAllItsSteps._lookup_tool(runs)],
        )
        wrapped = AssistantWithMetrics(
            assistant=assistant, model="openai.gpt-oss-120b", key_source="platform"
        )
        monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant", lambda *_a, **_k: wrapped
        )

        response = client.post(f"/{COMMUNITY}/{path}", headers={"Origin": ORIGIN}, json=payload)

        assert len(runs) > 5, "the tool ran again and again until the step limit"
        assert response.status_code == 500
        assert response.json()["detail"] == (
            "The current model (openai.gpt-oss-120b) used all its steps without finishing. "
            "Try Claude Haiku 4.5, or ask for a smaller part of the task."
        )
        (record,) = [r for r in caplog.records if "used all its steps" in r.getMessage()]
        assert record.levelno == logging.WARNING and not record.exc_info
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "nothing to page"


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
        # Text had reached the reader, so there was no retry to make: an outage, at ERROR
        # with the traceback.
        _assert_retryable(events, records, "ConnectionError", level=logging.ERROR, traceback=True)
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
            error=_stream_error(),
        )

        events = await _run(path, model, "some-model", [lookup])

        assert [e["event"] for e in events].count("tool_start") == 1
        assert runs == ["x"], "the tool ran once"
        assert events[-1]["event"] == "error", events
        assert events[-1]["retryable"] is True
        assert model.calls == 2, "the failed call was not tried again"
        assert _retry_records(caplog) == []
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and record.exc_info, (
            "a retry would have covered it had no tool run, so it is an error with a traceback"
        )


class TestTheUnavailableModelMessage:
    def test_it_names_the_model_and_the_one_to_try(self) -> None:
        assert _model_unavailable(BEDROCK_MODEL) == BEDROCK_UNAVAILABLE

    def test_with_no_other_model_left_to_suggest_the_messages_still_read(self, monkeypatch) -> None:
        from src.api.routers.community import _step_limit_reached
        from src.core.services import anthropic_models

        monkeypatch.setattr(anthropic_models, "SUGGESTED_MODELS", ("claude-haiku-4-5",))

        assert _model_unavailable("claude-haiku-4-5") == (
            "The current model (claude-haiku-4-5) is not available right now. "
            "Please choose another model."
        )
        assert _step_limit_reached("claude-haiku-4-5") == (
            "The current model (claude-haiku-4-5) used all its steps without finishing. "
            "Try another model, or ask for a smaller part of the task."
        )

    @pytest.mark.parametrize(
        ("failed", "suggested"),
        [
            ("openai.gpt-6-luna", "Claude Haiku 4.5"),
            ("openai.gpt-oss-120b", "Claude Haiku 4.5"),
            ("qwen.qwen3-next-80b-a3b", "Claude Haiku 4.5"),
            ("openai/gpt-6-luna", "Claude Haiku 4.5"),
            ("claude-sonnet-5-5", "Claude Haiku 4.5"),
            ("claude-haiku-4-5", "Claude Sonnet 5.5"),
            ("anthropic/claude-haiku-4.5", "Claude Sonnet 5.5"),
            ("anthropic/claude-haiku-4.5:nitro", "Claude Sonnet 5.5"),
            ("openai/gpt-oss-120b:nitro", "Claude Haiku 4.5"),
        ],
    )
    def test_it_never_suggests_the_model_that_failed(self, failed: str, suggested: str) -> None:
        assert _model_unavailable(failed).endswith(f"Try {suggested}, or choose another model.")

    def test_it_still_reads_when_the_model_is_not_known(self) -> None:
        assert _model_unavailable(None) == (
            "The current model is not available right now. Try Claude Haiku 4.5, or choose another model."
        )


class _EventsThatFailAsTheyClose:
    """The shape of a graph's event stream when the model failed in the background: closing
    it raises that failure. langchain's does this by awaiting the task it ran the model in.
    The real graph does it too, but only when the failure lands while the reader is between
    events, which depends on thread scheduling; this stand-in makes that moment certain, so
    the test cannot pass or fail by timing."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.runs = 0

    async def astream_events(self, _state: Any, **_kwargs: Any):
        self.runs += 1
        try:
            yield {"event": "on_chain_start", "data": {}}
            await asyncio.sleep(3600)
        finally:
            raise self.error


class TestTheReaderClosesTheStream:
    async def test_a_failure_that_surfaces_as_it_closes_is_not_tried_again(self) -> None:
        """The reader has gone: a retry would make a billable call nobody is listening to,
        and the generator would then ignore its own closing."""
        graph = _EventsThatFailAsTheyClose(_stream_error())
        events = stream_retry.astream_events_with_retry(
            graph,  # ty: ignore[invalid-argument-type]
            {},
            {},
            community_id=COMMUNITY,
            model="some-model",
            endpoint="/x/ask",
            request_id="req-close",
            retry_state=RetryState(),
        )

        await events.__anext__()
        with pytest.raises(EventStreamError):
            await events.aclose()

        assert graph.runs == 1, "no second call was made"

    async def test_the_failure_that_surfaced_is_logged_with_the_requests_context(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Nothing else says what failed for a reader who left: the router's handler does not
        run, and without this line the operator gets an asyncio "never retrieved" traceback."""
        caplog.set_level(logging.WARNING)
        events = stream_retry.astream_events_with_retry(
            _EventsThatFailAsTheyClose(_stream_error()),  # ty: ignore[invalid-argument-type]
            {},
            {},
            community_id=COMMUNITY,
            model="some-model",
            endpoint="/x/chat",
            request_id="req-close",
            session_id="sess-close",
            retry_state=RetryState(),
        )

        await events.__anext__()
        with pytest.raises(EventStreamError):
            await events.aclose()

        (record,) = [r for r in caplog.records if "as the reader left" in r.getMessage()]
        assert record.levelno == logging.WARNING
        for expected in (
            COMMUNITY,
            "some-model",
            "/x/chat",
            "req-close",
            "sess-close",
            "unavailable",
        ):
            assert expected in record.getMessage(), record.getMessage()

    async def test_a_failure_that_takes_the_place_of_a_cancellation_is_not_tried_again(
        self,
    ) -> None:
        """A task canceled while the model's stream is open can end in the stream's own
        failure instead of the ``CancelledError``; the helper then sees an ordinary model
        failure, and a retry would run a billable call from a task that was told to stop."""
        graph = _EventsThatFailAsTheyClose(_stream_error())

        async def consume() -> None:
            async for _ in stream_retry.astream_events_with_retry(
                graph,  # ty: ignore[invalid-argument-type]
                {},
                {},
                community_id=COMMUNITY,
                model="some-model",
                endpoint="/x/ask",
                request_id="req-cancel-failure",
                retry_state=RetryState(),
            ):
                pass

        task = asyncio.create_task(consume())
        for _ in range(POLLS_FOR_THE_WAIT):
            if graph.runs:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(EventStreamError):
            await asyncio.wait_for(task, 10)

        assert graph.runs == 1, "no second call was made"


class TestTheReaderLeavesDuringTheWait:
    async def test_canceling_during_the_delay_stops_cleanly_with_one_call(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A disconnect while the second try waits is the reader leaving: no second call
        starts, the cancellation propagates, and the log says why the retry never ran."""
        monkeypatch.setattr(stream_retry, "RETRY_DELAY_SECONDS", 30.0)
        caplog.set_level(logging.INFO)
        model = _failing(_stream_error())
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
                session_id="sess-cancel",
                retry_state=RetryState(),
            )
            async for _ in events:
                pass

        task = asyncio.create_task(consume())
        for _ in range(POLLS_FOR_THE_WAIT):
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
        assert len(left) == 1
        for expected in ("req-cancel", "sess-cancel", "some-model", "unavailable failure"):
            assert expected in left[0].getMessage(), left[0].getMessage()


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
    async def test_openrouter_unavailable_before_the_response_is_not_tried_again(
        self, path: str, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A 5xx answer arrives before any response. OpenRouter's client makes one request
        and the helper retries only a failure that came after the response began, so the
        reader is asked to choose another model."""
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

        assert events[-1]["message"] == unavailable_text(OPENROUTER_MODEL)
        assert requests == 1
        assert _retry_records(caplog) == []
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR and not record.exc_info, (
            "an outage before the response is an outage, and its line names the class"
        )

    @paths
    async def test_a_bedrock_error_answer_before_the_response_is_not_tried_again_here(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """botocore retried it with backoff already; the stream helper adds nothing."""
        caplog.set_level(logging.WARNING)
        llm = _bedrock_llm()
        wire = Wire(llm, **refusal("ServiceUnavailableException", "down"))

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "error"
        assert events[-1]["message"] == BEDROCK_UNAVAILABLE
        assert _retry_records(caplog) == []
        (record,) = _failure_records(caplog)
        assert record.levelno == logging.ERROR, "an outage before the response is an outage"
        assert not record.exc_info, "and its line names the class and code"
        attempts = llm.client.meta.config.retries["total_max_attempts"]
        assert attempts > 1, "botocore retries a refused request: the claim this test rests on"
        assert len(wire.requests) == attempts, "botocore's own tries, and the helper adds none"

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

        assert events[-1]["message"] == unavailable_text(OPENROUTER_MODEL)
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
        # The failed try's opening chunk carried input tokens; none of them are counted.
        usage = events[-1]["usage"]
        (row,) = _rows()
        assert usage["partial"] is False
        assert (row["input_tokens"], row["estimated_cost"]) == (
            usage["input_tokens"],
            usage["estimated_cost"],
        )

    @paths
    async def test_a_connection_lost_inside_an_anthropic_stream_is_answered_by_the_second_try(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The SDK's own httpx lets a network error out of an open stream raw, not as an
        ``anthropic`` exception; it is the same stream cut short as Bedrock's."""
        caplog.set_level(logging.WARNING)
        calls: list[int] = []

        class DiesAfterTheOpening(httpx2.AsyncByteStream, httpx2.SyncByteStream):
            def __init__(self, opening: bytes) -> None:
                self.opening = opening

            def __iter__(self) -> Iterator[bytes]:
                yield self.opening
                raise httpx2.RemoteProtocolError("peer closed connection without a complete body")

            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield self.opening
                raise httpx2.RemoteProtocolError("peer closed connection without a complete body")

        def handler(_request):
            calls.append(1)
            if len(calls) == 1:
                opening = message_stream([]).decode().split("\n\n")[0] + "\n\n"
                return httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream", "x-should-retry": "false"},
                    stream=DiesAfterTheOpening(opening.encode()),
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
        assert record.__dict__["failure_kind"] == "connection"


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
        model = _FailingChatModel(chunk_script=[[chunk]], error=_stream_error())

        events = await _run(path, model, "some-model")

        assert [e["event"] for e in events] == [
            *(["session"] if path == "chat" else []),
            "tool_call",
            "error",
        ]
        assert model.calls == 1
        assert _retry_records(caplog) == []

    @paths
    async def test_the_same_failure_with_no_output_is_tried_again(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The control for the tests here: without a chunk the reader was shown, the very
        same failure is retried, so they pass because of the output and not the error."""
        caplog.set_level(logging.WARNING)
        model = _failing(_stream_error())

        events = await _run(path, model, "some-model")

        assert events[-1]["event"] == "error"
        assert model.calls == 2
        assert len(_retry_records(caplog)) == 1

    @paths
    async def test_reasoning_alone(self, path: str, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        chunk = AIMessageChunk(content=[{"type": "thinking", "thinking": "hmm", "index": 0}])
        model = _FailingChatModel(chunk_script=[[chunk]], error=_stream_error())

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
                    raise _stream_error()
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
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The chat stream's ``finally`` releases the session's turn however the generator
        ends; with a wait in the middle, a disconnect there must still release it."""
        monkeypatch.setattr(stream_retry, "RETRY_DELAY_SECONDS", 30.0)
        caplog.set_level(logging.INFO)
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
            for _ in range(POLLS_FOR_THE_WAIT):
                assert not task.done(), task.exception()
                if _retry_records(caplog):
                    break
                await asyncio.sleep(0.05)
            assert _retry_records(caplog), "the first try failed and the wait began"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert len(wire.requests) == 1
        assert session.begin_turn() is True, "the turn was released"
        (left,) = [r for r in caplog.records if "The reader left" in r.getMessage()]
        assert "sess-cancel" in left.getMessage(), (
            "it was the wait that the cancellation landed in, and the line names the session"
        )


class _FailsOnCalls(StreamingScriptedChatModel):
    """Raises ``error`` on the calls whose index (from 0, counting failures too) is in
    ``fail_on``; the other calls replay the script in order."""

    error: Exception
    fail_on: set[int]
    attempts: int = 0

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        index = self.attempts
        self.attempts += 1
        if index in self.fail_on:
            raise self.error
        yield from super()._stream(messages, stop, run_manager, **kwargs)


async def _drain(model: Any, retry_state: RetryState) -> list[Any]:
    """Every event the retry helper yields for one turn over ``model``, through the real
    graph."""
    assistant = CommunityAssistant(model=model, config=community_config(), preload_docs=False)
    state = {"messages": [HumanMessage(content=QUESTION)], "retrieved_docs": [], "tool_calls": []}
    return [
        event
        async for event in stream_retry.astream_events_with_retry(
            assistant.build_graph(),
            state,
            {},
            community_id=COMMUNITY,
            model="some-model",
            endpoint="/x/ask",
            request_id="req-state",
            retry_state=retry_state,
        )
    ]


class TestWhatTheHelperReports:
    """``RetryState.failed_after_retry`` says one thing: the failure raised came from the
    second try before it produced any output. The helper writes it and resets it, so a
    state that is reused cannot disable a retry or claim a success."""

    async def test_a_state_left_over_from_another_stream_does_not_disable_the_retry(self) -> None:
        stale = RetryState(failed_after_retry=True)
        model = _FailsOnCalls(
            chunk_script=[[text_chunk("Hi")]],
            error=_stream_error(),
            fail_on={0},
        )

        events = await _drain(model, stale)

        assert any(e["event"] == "on_chat_model_end" for e in events), "the second try answered"
        assert model.attempts == 2
        assert stale.failed_after_retry is False

    async def test_a_state_left_over_does_not_make_a_clean_stream_claim_a_retry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        stale = RetryState(failed_after_retry=True)
        model = _FailsOnCalls(
            chunk_script=[[text_chunk("Hi")]],
            error=_stream_error(),
            fail_on=set(),
        )

        await _drain(model, stale)

        assert stale.failed_after_retry is False
        assert _retry_logs(caplog, "The second try") == []

    async def test_a_retried_failure_that_cannot_say_what_it_is_is_still_retried_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The "Retrying" line is logged before the second try; a ``__str__`` that raises
        would drop it and, under a handler that raises, end the stream instead of retrying."""

        class UnreadableStreamError(EventStreamError):
            def __str__(self) -> str:
                raise RuntimeError("no text for you")

        caplog.set_level(logging.WARNING)
        model = _FailsOnCalls(
            chunk_script=[[text_chunk("Hi")]],
            error=UnreadableStreamError(
                {"Error": {"Code": "modelStreamErrorException", "Message": "boom"}},
                "ConverseStream",
            ),
            fail_on={0},
        )

        events = await _drain(model, RetryState())

        assert any(e["event"] == "on_chat_model_end" for e in events), "the second try answered"
        (record,) = _retry_records(caplog)
        assert "<UnreadableStreamError: text unreadable>" in record.getMessage()

    async def test_a_failure_of_the_second_try_is_reported_as_one(self) -> None:
        state = RetryState()
        model = _FailsOnCalls(
            chunk_script=[[]],
            error=_stream_error(),
            fail_on={0, 1, 2},
        )

        with pytest.raises(EventStreamError):
            await _drain(model, state)

        assert model.attempts == 2
        assert state.failed_after_retry is True

    async def test_a_failure_that_was_not_retried_is_not_reported_as_one(self) -> None:
        state = RetryState()
        model = _FailsOnCalls(
            chunk_script=[[]],
            error=ClientError(
                {
                    "Error": {"Code": "ValidationException", "Message": "no"},
                    "ResponseMetadata": {"HTTPStatusCode": 400},
                },
                "ConverseStream",
            ),
            fail_on={0, 1},
        )

        with pytest.raises(ClientError):
            await _drain(model, state)

        assert model.attempts == 1
        assert state.failed_after_retry is False

    @paths
    async def test_a_later_failure_in_a_run_that_was_retried_is_not_labeled_a_retry_failure(
        self, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The first call failed fast and the retry worked: a tool ran, and then the next
        model call failed. That call was never tried twice, and the log must not say so."""
        from tests.test_api.test_tool_call_streaming import _anthropic_call

        caplog.set_level(logging.WARNING)
        runs: list[str] = []

        @tool
        def lookup(query: str) -> str:
            """Look something up."""
            runs.append(query)
            return "found"

        model = _FailsOnCalls(
            chunk_script=[_anthropic_call("lookup", "toolu_01lookup", {"query": "x"})],
            error=_stream_error(),
            fail_on={0, 2},
        )

        events = await _run(path, model, "some-model", [lookup])

        assert runs == ["x"], "the tool ran once"
        assert len(_retry_records(caplog)) == 1, "the first call was tried again"
        assert events[-1]["event"] == "error"
        (record,) = _failure_records(caplog)
        # It was worth a retry and reached the reader (a tool had run, so there was none to
        # make): an outage, at ERROR. But that call was never tried twice, and says so.
        assert record.levelno == logging.ERROR and record.exc_info
        assert "after one retry" not in record.getMessage()
        (row,) = _rows()
        assert row["error_message"] == "EventStreamError modelStreamErrorException"


class TestTheUsageOfAReplyThatWasTriedAgain:
    @paths
    async def test_it_is_the_second_tries_alone_with_the_cache_fields_the_adapter_reports(
        self, path: str
    ) -> None:
        """Through the real Bedrock stream parser: ``langchain-aws`` reports the input as
        the true total (fresh, read and written), and the event agrees with the request's
        row in the metrics."""
        counts = {
            "inputTokens": 20,
            "outputTokens": 7,
            "totalTokens": 27,
            "cacheReadInputTokens": 8,
            "cacheWriteInputTokens": 2,
        }
        llm = _bedrock_llm()
        Wire(
            llm,
            **CUT_SHORT,
            then=[
                {
                    "body": converse_stream(["Hello", " there"], usage=counts),
                    "content_type": EVENT_STREAM,
                }
            ],
        )

        events = await _run(path, llm, BEDROCK_MODEL)

        assert events[-1]["event"] == "done"
        usage = events[-1]["usage"]
        assert (usage["input_tokens"], usage["output_tokens"]) == (30, 7)
        assert (usage["cache_read_tokens"], usage["cache_creation_tokens"]) == (8, 2)
        assert usage["partial"] is False, "a failed try is not a run that reported nothing"
        (row,) = _rows()
        assert row["input_tokens"] == usage["input_tokens"]
        assert row["estimated_cost"] == usage["estimated_cost"]
