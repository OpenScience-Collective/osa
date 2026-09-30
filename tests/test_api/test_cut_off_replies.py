"""A reply the model stopped at its output limit, told to the reader (release review, finding 1).

When a model spends its output budget (``bedrock_max_output_tokens`` counts its reasoning)
the stream ends normally with whatever fitted, which on a reasoning model can be nothing.
Nothing raises and nothing was logged: the server answered 200 with an empty ``done``, the
widget dropped the empty bubble, and the reader saw no answer and no error. A reply cut
off in mid-sentence was shown as complete.

What is real here: the community config, ``CommunityAssistant``, the compiled LangGraph
graph and its tool node, ``astream_events`` and ``ainvoke``, langchain-core's merge of the
chunks into the message the graph routes on, the router's SSE assembly for ``/chat`` and
``/ask``, the real HTTP endpoints with the metrics middleware for the requests that are
not streamed, the session store and the metrics database.

What stands in: the chat model (``StreamingScriptedChatModel``), which yields chunks in
the shape each provider adapter yields them (``tests/helpers/provider_replies.py``), and
the router's ``create_community_assistant``, which would build a real client from settings
this suite has no keys for, as in ``test_tool_call_streaming.py``. The last class drives
each provider's real client stack instead, with only the network under it staged.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from src.api.routers.community import (
    AssistantWithMetrics,
    ChatSession,
    _get_session_store,
    _stream_ask_response,
    _stream_chat_response,
    create_community_router,
)
from src.api.tool_results import PendingClientCall
from src.api.turn_outcome import (
    CONTEXT_FULL_CUT_OFF_MESSAGE,
    CONTEXT_FULL_NO_ANSWER_MESSAGE,
    CUT_OFF_MESSAGE,
    DECLINED_MESSAGE,
    EMPTY_MESSAGE,
    MALFORMED_MESSAGE,
    NO_ANSWER_MESSAGE,
)
from src.assistants.community import CommunityAssistant
from src.core.config.community import FULL_OUTPUT_TOOL_NAME
from src.core.services.anthropic_models import BEDROCK_MODELS, DEFAULT_MODEL
from src.core.services.litellm_llm import DEFAULT_MODEL as OPENROUTER_MODEL
from src.core.services.model_outcome import (
    CONTEXT_WINDOW_STOP_REASON,
    DECLINED_STOP_REASONS,
    MALFORMED_STOP_REASONS,
)
from src.metrics.db import init_metrics_db, metrics_connection
from src.metrics.middleware import MetricsMiddleware
from tests.helpers.provider_replies import (
    ANSWER,
    COMMUNITY,
    ORIGIN,
    PROVIDERS,
    QUESTION,
    USAGE,
    Provider,
    assistant_for,
    collect,
    community_config,
    real_request,
    scripted_reply,
)
from tests.test_api.test_tool_call_streaming import _anthropic_call

provider_param = pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.name)


@pytest.fixture(autouse=True)
def metrics_db(tmp_path, monkeypatch):
    """A real metrics database, so a streamed request's row can be read back."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    init_metrics_db()


def _rows() -> list[dict]:
    with metrics_connection() as conn:
        conn.row_factory = lambda cursor, row: dict(
            zip([c[0] for c in cursor.description], row, strict=True)
        )
        return conn.execute("SELECT * FROM request_log ORDER BY timestamp").fetchall()


async def _chat(
    provider: Provider,
    script: list[list[AIMessageChunk]],
    *,
    browser_runs_answered: int = 0,
    code_runs_answered: int = 0,
) -> tuple[list[dict], ChatSession]:
    session = ChatSession("sess-cutoff", COMMUNITY)
    session.add_user_message(QUESTION)
    with patch(
        "src.api.routers.community.create_community_assistant",
        return_value=assistant_for(provider, script),
    ):
        events = await collect(
            _stream_chat_response(
                COMMUNITY,
                session,
                None,
                None,
                None,
                http_request=real_request("req-cutoff"),
                browser_runs_answered=browser_runs_answered,
                code_runs_answered=code_runs_answered,
            )
        )
    return events, session


async def _ask(provider: Provider, script: list[list[AIMessageChunk]]) -> list[dict]:
    with patch(
        "src.api.routers.community.create_community_assistant",
        return_value=assistant_for(provider, script),
    ):
        return await collect(
            _stream_ask_response(
                COMMUNITY, QUESTION, None, None, None, http_request=real_request("req-cutoff")
            )
        )


def _names(events: list[dict]) -> list[str]:
    return [event["event"] for event in events]


def _cut_off_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "cut off at its output limit" in r.getMessage()]


def _assert_logged(records: list[logging.LogRecord], provider: Provider, stop: str) -> None:
    """One warning, naming the community, the model, the request and the stop reason."""
    assert len(records) == 1, [r.getMessage() for r in records]
    record = records[0]
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    for expected in (COMMUNITY, provider.model, "req-cutoff", stop):
        assert expected in message, f"{expected!r} missing from {message!r}"


# ---------------------------------------------------------------------------
# The streamed chat endpoint
# ---------------------------------------------------------------------------


@provider_param
class TestStreamedChat:
    async def test_a_reply_with_no_text_is_an_error_not_an_empty_done(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        events, session = await _chat(provider, [scripted_reply(provider, "", cut_off=True)])

        assert _names(events)[-1] == "error"
        assert "done" not in _names(events)
        assert events[-1]["message"] == NO_ANSWER_MESSAGE
        _assert_logged(_cut_off_records(caplog), provider, provider.cut_off)
        assert [type(m).__name__ for m in session.messages] == ["HumanMessage"], (
            "nothing is stored for a reply that was not written"
        )

    async def test_a_metrics_row_counts_it_as_an_error_and_keeps_its_cost(
        self, provider: Provider
    ) -> None:
        await _chat(provider, [scripted_reply(provider, "", cut_off=True)])

        (row,) = _rows()
        assert row["status_code"] == 502
        assert "output limit" in row["error_message"]
        assert row["input_tokens"] == USAGE["input_tokens"]
        assert row["model"] == provider.model

    async def test_a_reply_cut_off_in_a_sentence_is_a_warning_before_done(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        events, session = await _chat(
            provider, [scripted_reply(provider, ANSWER[:30], cut_off=True)]
        )

        names = _names(events)
        assert "error" not in names
        assert names[-1] == "done"
        warning = next(e for e in events if e["event"] == "warning")
        assert warning["message"] == CUT_OFF_MESSAGE
        assert names.index("warning") < names.index("done")
        done = events[-1]
        assert done["content"] == ANSWER[:30]
        assert done["model"] == provider.model
        _assert_logged(_cut_off_records(caplog), provider, provider.cut_off)
        assert session.messages[-1].content == ANSWER[:30]
        (row,) = _rows()
        assert row["status_code"] == 200

    async def test_a_reply_that_finished_says_nothing(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        events, _ = await _chat(provider, [scripted_reply(provider, ANSWER)])

        assert _names(events)[-1] == "done"
        assert not {"error", "warning"} & set(_names(events))
        assert events[-1]["content"] == ANSWER
        assert _cut_off_records(caplog) == []

    async def test_only_the_last_model_run_decides(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A tool loop runs the model more than once; the answer is the last run's."""
        caplog.set_level(logging.WARNING)
        lookup = _anthropic_call("lookup_scriptedreply_docs", "toolu_01cutoff", {"query": "tags"})

        finished_last, _ = await _chat(
            provider,
            [
                [*lookup, *provider.end(provider.cut_off, True, False)],
                scripted_reply(provider, ANSWER),
            ],
        )
        cut_last, _ = await _chat(
            provider,
            [
                [*lookup, *provider.end(provider.finished, True, False)],
                scripted_reply(provider, ANSWER[:20], cut_off=True),
            ],
        )

        assert not {"error", "warning"} & set(_names(finished_last))
        assert "warning" in _names(cut_last)
        assert len(_cut_off_records(caplog)) == 1

    async def test_a_reply_that_already_ran_code_is_not_an_empty_one(
        self, provider: Provider
    ) -> None:
        """The widget keeps a reply that ran code even with no text, so a later run that
        is cut off before it writes anything is a warning on that reply, not an error."""
        events, _ = await _chat(
            provider,
            [scripted_reply(provider, "", cut_off=True)],
            browser_runs_answered=1,
            code_runs_answered=1,
        )

        assert "error" not in _names(events)
        assert [e["message"] for e in events if e["event"] == "warning"] == [CUT_OFF_MESSAGE]
        assert _names(events)[-1] == "done"

    async def test_a_browser_run_that_is_not_code_does_not_make_it_one(
        self, provider: Provider
    ) -> None:
        """A run that only read output back leaves nothing on the reply the widget keeps
        (see ``TestOnlyCodeTheWidgetKeepsCounts`` for the same through the endpoint)."""
        events, _ = await _chat(
            provider,
            [scripted_reply(provider, "", cut_off=True)],
            browser_runs_answered=1,
            code_runs_answered=0,
        )

        assert _names(events)[-1] == "error" and "done" not in _names(events)
        assert events[-1]["message"] == NO_ANSWER_MESSAGE


# ---------------------------------------------------------------------------
# The streamed ask endpoint
# ---------------------------------------------------------------------------


@provider_param
class TestStreamedAsk:
    async def test_a_reply_with_no_text_is_an_error_not_an_empty_done(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        events = await _ask(provider, [scripted_reply(provider, "", cut_off=True)])

        assert _names(events)[-1] == "error"
        assert "done" not in _names(events)
        assert events[-1]["message"] == NO_ANSWER_MESSAGE
        _assert_logged(_cut_off_records(caplog), provider, provider.cut_off)
        assert _rows()[0]["status_code"] == 502

    async def test_a_reply_cut_off_in_a_sentence_is_a_warning_before_done(
        self, provider: Provider
    ) -> None:
        events = await _ask(provider, [scripted_reply(provider, ANSWER[:30], cut_off=True)])

        names = _names(events)
        assert names[-1] == "done" and "error" not in names
        assert next(e for e in events if e["event"] == "warning")["message"] == CUT_OFF_MESSAGE
        assert names.index("warning") < names.index("done")
        assert events[-1]["content"] == ANSWER[:30]

    async def test_a_reply_that_finished_says_nothing(self, provider: Provider) -> None:
        events = await _ask(provider, [scripted_reply(provider, ANSWER)])

        assert not {"error", "warning"} & set(_names(events))


# ---------------------------------------------------------------------------
# A reply with nothing in it, whatever the stop reason (release review, follow-up 3)
# ---------------------------------------------------------------------------

#: Each stop reason the registry of reasons names, with what the reader is told. Read from
#: the registry, so a reason added there is covered here.
EMPTY_STOPS = [
    *((reason, DECLINED_MESSAGE) for reason in sorted(DECLINED_STOP_REASONS)),
    *((reason, MALFORMED_MESSAGE) for reason in sorted(MALFORMED_STOP_REASONS)),
]


def _empty_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "Model reply was empty" in r.getMessage()]


@provider_param
class TestAnEmptyReplyWhateverTheStopReason:
    """A reply with no text, no code and no parked call used to end in an empty ``done``
    unless it had hit the output limit: a refusal, a filter or a guardrail left the reader
    with no answer and no word, because the widget drops the empty bubble."""

    @pytest.mark.parametrize(("stop", "message"), EMPTY_STOPS)
    async def test_chat_says_what_the_model_did(
        self, provider: Provider, stop: str, message: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        events, session = await _chat(provider, [scripted_reply(provider, "", stop=stop)])

        assert _names(events)[-1] == "error" and "done" not in _names(events)
        assert events[-1]["message"] == message
        (record,) = _empty_records(caplog)
        assert record.levelno == logging.WARNING
        text = record.getMessage()
        for expected in (COMMUNITY, provider.model, "req-cutoff", stop):
            assert expected in text, f"{expected!r} missing from {text!r}"
        assert [type(m).__name__ for m in session.messages] == ["HumanMessage"]
        (row,) = _rows()
        assert row["status_code"] == 502 and stop in row["error_message"]
        assert row["input_tokens"] == USAGE["input_tokens"]

    @pytest.mark.parametrize(("stop", "message"), EMPTY_STOPS)
    async def test_ask_says_what_the_model_did(
        self, provider: Provider, stop: str, message: str
    ) -> None:
        events = await _ask(provider, [scripted_reply(provider, "", stop=stop)])

        assert _names(events)[-1] == "error" and "done" not in _names(events)
        assert events[-1]["message"] == message
        assert _rows()[0]["status_code"] == 502

    async def test_a_reply_that_simply_ended_with_nothing_is_an_error_too(
        self, provider: Provider
    ) -> None:
        events, _ = await _chat(provider, [scripted_reply(provider, "")])

        assert _names(events)[-1] == "error" and "done" not in _names(events)
        assert events[-1]["message"] == EMPTY_MESSAGE
        assert _rows()[0]["status_code"] == 502

    async def test_text_of_only_whitespace_is_nothing(self, provider: Provider) -> None:
        events, _ = await _chat(provider, [scripted_reply(provider, "\n\n ")])

        assert _names(events)[-1] == "error"
        assert events[-1]["message"] == EMPTY_MESSAGE

    @pytest.mark.parametrize(("stop", "message"), EMPTY_STOPS)
    async def test_text_before_a_refusal_is_left_alone(
        self, provider: Provider, stop: str, message: str
    ) -> None:
        """There is something to show, so it is shown; this is about a reply of nothing."""
        events, _ = await _chat(provider, [scripted_reply(provider, ANSWER, stop=stop)])

        assert _names(events)[-1] == "done"
        assert not {"error", "warning"} & set(_names(events))
        assert message not in [e.get("message") for e in events]


# ---------------------------------------------------------------------------
# A conversation that filled the context window (release review, follow-up 4)
# ---------------------------------------------------------------------------


@provider_param
class TestAFullContextWindow:
    """Trying again, or asking it to continue, fails again when the context is full, so
    the reader is sent to a new conversation."""

    async def test_no_text_is_an_error_that_says_to_start_over(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        events, _ = await _chat(
            provider, [scripted_reply(provider, "", stop=CONTEXT_WINDOW_STOP_REASON)]
        )

        assert _names(events)[-1] == "error"
        assert events[-1]["message"] == CONTEXT_FULL_NO_ANSWER_MESSAGE
        (record,) = [r for r in caplog.records if "context window" in r.getMessage()]
        assert CONTEXT_WINDOW_STOP_REASON in record.getMessage()
        (row,) = _rows()
        assert row["status_code"] == 502 and "context window" in row["error_message"]

    async def test_text_that_stopped_short_is_a_warning_that_says_to_start_over(
        self, provider: Provider
    ) -> None:
        events, _ = await _chat(
            provider, [scripted_reply(provider, ANSWER[:30], stop=CONTEXT_WINDOW_STOP_REASON)]
        )

        assert _names(events)[-1] == "done"
        assert [e["message"] for e in events if e["event"] == "warning"] == [
            CONTEXT_FULL_CUT_OFF_MESSAGE
        ]

    async def test_ask_says_the_same(self, provider: Provider) -> None:
        events = await _ask(
            provider, [scripted_reply(provider, "", stop=CONTEXT_WINDOW_STOP_REASON)]
        )

        assert events[-1]["message"] == CONTEXT_FULL_NO_ANSWER_MESSAGE


# ---------------------------------------------------------------------------
# The requests that are not streamed, through the real endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from src.api.config import get_settings
    from src.assistants.registry import registry

    # What is under test is the reply, not the API key check (see test_tool_call_streaming).
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


def _serve(monkeypatch, provider: Provider, script: list[list[AIMessageChunk]]) -> None:
    monkeypatch.setattr(
        "src.api.routers.community.create_community_assistant",
        lambda *_a, **_k: assistant_for(provider, script),
    )


def _post_ask(client: TestClient):
    return client.post(
        f"/{COMMUNITY}/ask",
        headers={"Origin": ORIGIN},
        json={"question": QUESTION, "stream": False},
    )


def _post_chat(client: TestClient, session_id: str = "sess-plain-chat"):
    return client.post(
        f"/{COMMUNITY}/chat",
        headers={"Origin": ORIGIN},
        json={"message": QUESTION, "session_id": session_id, "stream": False},
    )


@provider_param
class TestAskWithoutStreaming:
    def test_a_reply_with_no_text_is_a_502_naming_why(
        self, provider: Provider, client: TestClient, monkeypatch, caplog
    ) -> None:
        caplog.set_level(logging.WARNING)
        _serve(monkeypatch, provider, [scripted_reply(provider, "", cut_off=True)])

        response = _post_ask(client)

        assert response.status_code == 502
        assert response.json()["detail"] == NO_ANSWER_MESSAGE
        records = _cut_off_records(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        assert COMMUNITY in message and provider.model in message and provider.cut_off in message
        (row,) = _rows()
        assert row["status_code"] == 502
        assert row["request_id"] in message
        assert "output limit" in row["error_message"]
        assert row["input_tokens"] == USAGE["input_tokens"]

    @pytest.mark.parametrize(("stop", "message"), EMPTY_STOPS)
    def test_a_reply_the_model_declined_is_a_502_naming_what_it_did(
        self, provider: Provider, client: TestClient, monkeypatch, stop: str, message: str
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, "", stop=stop)])

        response = _post_ask(client)

        assert response.status_code == 502
        assert response.json()["detail"] == message

    def test_a_reply_that_ended_with_nothing_is_a_502(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, "")])

        response = _post_ask(client)

        assert response.status_code == 502
        assert response.json()["detail"] == EMPTY_MESSAGE

    def test_a_reply_cut_off_in_a_sentence_carries_a_warning(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, ANSWER[:30], cut_off=True)])

        response = _post_ask(client)

        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == ANSWER[:30]
        assert body["warnings"] == [CUT_OFF_MESSAGE]
        assert body["model"] == provider.model

    def test_a_reply_that_finished_carries_none(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, ANSWER)])

        body = _post_ask(client).json()

        assert body["answer"] == ANSWER
        assert body["warnings"] == []


@provider_param
class TestChatWithoutStreaming:
    def test_a_reply_with_no_text_is_a_502_and_leaves_no_empty_turn_in_the_history(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, "", cut_off=True)])

        response = _post_chat(client)

        assert response.status_code == 502
        assert response.json()["detail"] == NO_ANSWER_MESSAGE
        session = _get_session_store(COMMUNITY)["sess-plain-chat"]
        assert [type(m).__name__ for m in session.messages] == ["HumanMessage"]

    def test_a_reply_cut_off_in_a_sentence_carries_a_warning(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, ANSWER[:30], cut_off=True)])

        body = _post_chat(client).json()

        assert body["message"]["content"] == ANSWER[:30]
        assert body["warnings"] == [CUT_OFF_MESSAGE]

    def test_history_is_not_read_as_this_turns_runs(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        """A cut-off reply stays in the history; the turn after it is judged on its own."""
        _serve(monkeypatch, provider, [scripted_reply(provider, ANSWER[:30], cut_off=True)])
        assert _post_chat(client).json()["warnings"] == [CUT_OFF_MESSAGE]
        _serve(monkeypatch, provider, [scripted_reply(provider, ANSWER)])

        body = _post_chat(client).json()

        assert body["warnings"] == []
        assert body["message"]["content"] == ANSWER

    def test_an_empty_reply_that_finished_is_a_502_and_not_stored_as_a_turn(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        """Finding 4: the streamed path never stored an empty assistant message; this
        one did, and the next request replayed it (as "." to Bedrock, as an empty string
        to OpenRouter). Now it is an error whatever the stop reason, as it is streamed,
        so a 200 never carries an answer of nothing."""
        _serve(monkeypatch, provider, [scripted_reply(provider, "")])

        response = _post_chat(client)

        assert response.status_code == 502
        assert response.json()["detail"] == EMPTY_MESSAGE
        session = _get_session_store(COMMUNITY)["sess-plain-chat"]
        assert [type(m).__name__ for m in session.messages] == ["HumanMessage"]

    @pytest.mark.parametrize(("stop", "message"), EMPTY_STOPS)
    def test_a_reply_the_model_declined_is_a_502_naming_what_it_did(
        self, provider: Provider, client: TestClient, monkeypatch, stop: str, message: str
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, "", stop=stop)])

        response = _post_chat(client)

        assert response.status_code == 502
        assert response.json()["detail"] == message
        (row,) = _rows()
        assert row["status_code"] == 502 and stop in row["error_message"]

    def test_a_full_context_window_says_to_start_over(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(
            monkeypatch,
            provider,
            [scripted_reply(provider, "", stop=CONTEXT_WINDOW_STOP_REASON)],
        )

        response = _post_chat(client)

        assert response.status_code == 502
        assert response.json()["detail"] == CONTEXT_FULL_NO_ANSWER_MESSAGE

    def test_text_that_stopped_at_a_full_context_window_carries_that_warning(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(
            monkeypatch,
            provider,
            [scripted_reply(provider, ANSWER[:30], stop=CONTEXT_WINDOW_STOP_REASON)],
        )

        body = _post_chat(client).json()

        assert body["warnings"] == [CONTEXT_FULL_CUT_OFF_MESSAGE]


# ---------------------------------------------------------------------------
# A reply that resumed after a browser run (release review, follow-up 2)
# ---------------------------------------------------------------------------

CALL_ID = "toolu_01resumecutoff"


def _park(tool: str, *, runs_before: int = 0, code_runs_before: int = 0) -> str:
    """A session mid-reply: the assistant asked the browser for ``tool`` and waits."""
    session = ChatSession("sess-resume", COMMUNITY)
    session.add_user_message(QUESTION)
    session.messages.append(
        AIMessage(
            content="",
            tool_calls=[{"name": tool, "args": {}, "id": CALL_ID, "type": "tool_call"}],
        )
    )
    session.set_pending_call(
        PendingClientCall.from_state(
            {"call_id": CALL_ID, "tool": tool, "args": {}, "requires_permission": False},
            runs_before=runs_before,
            code_runs_before=code_runs_before,
        )
    )
    _get_session_store(COMMUNITY)[session.session_id] = session
    return session.session_id


def _resume(client: TestClient, session_id: str) -> list[dict]:
    response = client.post(
        f"/{COMMUNITY}/chat/resume",
        headers={"Origin": ORIGIN},
        json={
            "session_id": session_id,
            "result": {"call_id": CALL_ID, "status": "ok", "summary": "done"},
            "client_tools": [],
        },
    )
    assert response.status_code == 200, response.text
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


@provider_param
class TestOnlyCodeTheWidgetKeepsCounts:
    """The widget keeps a reply that ran code even with no text, and records a run only
    when the tool was not the full-output tool (``FULL_OUTPUT_TOOL_NAME``). So a reply
    whose only browser run read output back has nothing on screen but what the model
    writes: a run 2 that hits the limit with no text would leave a banner and no answer."""

    def test_a_run_that_only_read_output_back_is_not_an_answer(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, "", cut_off=True)])

        events = _resume(client, _park(FULL_OUTPUT_TOOL_NAME))

        assert _names(events)[-1] == "error" and "done" not in _names(events)
        assert events[-1]["message"] == NO_ANSWER_MESSAGE
        # The stream writes its own row last (the middleware's is written when the
        # response starts, before the reply exists).
        assert _rows()[-1]["status_code"] == 502

    def test_a_run_of_code_the_widget_kept_is_one(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, "", cut_off=True)])

        events = _resume(client, _park("execute_code"))

        assert "error" not in _names(events) and _names(events)[-1] == "done"
        assert [e["message"] for e in events if e["event"] == "warning"] == [CUT_OFF_MESSAGE]

    def test_code_run_earlier_in_the_reply_still_counts(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        """Run 1 ran code, run 2 read its output back, run 3 is cut off with no text."""
        _serve(monkeypatch, provider, [scripted_reply(provider, "", cut_off=True)])

        events = _resume(client, _park(FULL_OUTPUT_TOOL_NAME, runs_before=1, code_runs_before=1))

        assert "error" not in _names(events) and _names(events)[-1] == "done"

    def test_reading_output_back_twice_is_still_not_code(
        self, provider: Provider, client: TestClient, monkeypatch
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, "", cut_off=True)])

        events = _resume(client, _park(FULL_OUTPUT_TOOL_NAME, runs_before=1, code_runs_before=0))

        assert _names(events)[-1] == "error"


# ---------------------------------------------------------------------------
# The same thing through each provider's real client stack
# ---------------------------------------------------------------------------


async def _chat_with(llm, model: str) -> tuple[list[dict], ChatSession]:
    """Stream a chat turn through the real graph with a real provider client."""
    assistant = CommunityAssistant(model=llm, config=community_config(), preload_docs=False)
    wrapped = AssistantWithMetrics(assistant=assistant, model=model, key_source="platform")
    session = ChatSession("sess-real-client", COMMUNITY)
    session.add_user_message(QUESTION)
    with patch("src.api.routers.community.create_community_assistant", return_value=wrapped):
        events = await collect(
            _stream_chat_response(
                COMMUNITY, session, None, None, None, http_request=real_request("req-cutoff")
            )
        )
    return events, session


class TestThroughTheRealClients:
    """Reasoning that spends the whole output budget ends a stream normally: no error, no
    text. The models are the real ones (``create_*_llm``), the graph and the router too;
    only the network under each client is staged."""

    async def test_bedrock(self, caplog: pytest.LogCaptureFixture) -> None:
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
            body = converse_stream([], reasoning=["hmm "] * 4, stop_reason="max_tokens")
            Wire(llm, body, EVENT_STREAM)

            events, session = await _chat_with(llm, model)
        finally:
            _bedrock_client.cache_clear()

        assert _names(events)[-1] == "error" and "done" not in _names(events)
        assert events[-1]["message"] == NO_ANSWER_MESSAGE
        assert len(_cut_off_records(caplog)) == 1
        assert len(session.messages) == 1

    async def test_anthropic(self, caplog: pytest.LogCaptureFixture) -> None:
        from src.api.config import Settings
        from src.core.services.anthropic_llm import create_anthropic_llm
        from tests.helpers.anthropic_wire import reply_with, served_by

        caplog.set_level(logging.WARNING)
        handler = reply_with([], thinking=["hmm "] * 4, stop_reason="max_tokens")
        with served_by(handler):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-test", settings=Settings(_env_file=None)
            )
            events, session = await _chat_with(llm, DEFAULT_MODEL)

        assert _names(events)[-1] == "error" and "done" not in _names(events)
        assert events[-1]["message"] == NO_ANSWER_MESSAGE
        assert len(_cut_off_records(caplog)) == 1
        assert len(session.messages) == 1

    async def test_anthropic_text_cut_off_in_a_sentence(self) -> None:
        from src.api.config import Settings
        from src.core.services.anthropic_llm import create_anthropic_llm
        from tests.helpers.anthropic_wire import reply_with, served_by

        with served_by(reply_with([ANSWER[:30]], stop_reason="max_tokens")):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-test", settings=Settings(_env_file=None)
            )
            events, _ = await _chat_with(llm, DEFAULT_MODEL)

        assert [e["message"] for e in events if e["event"] == "warning"] == [CUT_OFF_MESSAGE]
        assert events[-1]["event"] == "done" and events[-1]["content"] == ANSWER[:30]

    @pytest.mark.parametrize(("texts", "event"), [((), "error"), ((ANSWER[:30],), "warning")])
    async def test_openrouter(self, monkeypatch, texts: tuple[str, ...], event: str) -> None:
        from src.core.services.litellm_llm import create_openrouter_llm
        from tests.helpers.openrouter import FakeOpenRouter, stream_of

        server = FakeOpenRouter()
        monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
        try:
            server.reply(stream_of(*texts, finish="length"))
            llm = create_openrouter_llm(model=OPENROUTER_MODEL, api_key="sk-or-test")

            events, _ = await _chat_with(llm, OPENROUTER_MODEL)
        finally:
            server.close()

        assert event in _names(events)
        if event == "error":
            assert "done" not in _names(events)
            assert events[-1]["message"] == NO_ANSWER_MESSAGE
        else:
            assert _names(events)[-1] == "done"
            assert events[-1]["content"] == texts[0]
