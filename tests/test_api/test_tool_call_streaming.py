"""The `tool_call` SSE event (issue #538), driven through the real graph.

`tool_start` fires once a tool starts executing, after the model has finished writing
the call; for a long code call that is many seconds with no event at all. `tool_call`
says which tool the model is writing a call to as soon as its name streams, so the
widget can say "Writing code..." or "Searching datasets..." instead of just waiting.

What is real here: the community config, `CommunityAssistant`, the compiled LangGraph
graph and its tool node, `astream_events`, langchain-core's streaming path (which turns
each chunk into an `on_chat_model_stream` event and merges the chunks into the message
the graph routes on), the router's SSE assembly for `/chat`, `/chat/resume` and `/ask`,
and the session store.

What stands in: the chat model (`StreamingScriptedChatModel`), which yields chunks in
the shapes the three provider adapters OSA uses yield them (Anthropic, Bedrock and
OpenAI-style through LiteLLM; the builders are in `tests/helpers/tool_call_streaming.py`),
and the router's `create_community_assistant`, which would build a real LLM client from
settings this suite has no keys for. `tests/test_api/test_client_tool_streaming.py` sets the same
boundary, for the same reasons.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk

from src.api.routers.community import (
    _TOOL_CALL_NAME_MAX_CHARS,
    ChatSession,
    _stream_ask_response,
    _stream_chat_response,
    _tool_call_sse_events,
)
from tests.helpers.tool_call_streaming import (
    ALLOWED_ORIGIN,
    ANSWER,
    CODE_CALL_ID,
    COMMUNITY,
    DOCS_CALL_ID,
    SEARCH_ARGS,
    SEARCH_CALL_ID,
    SECRET_QUERY,
    _answer,
    _anthropic_call,
    _assistant,
    _bedrock_call,
    _openai_calls,
    _parked_session,
    _pieces,
    _text,
    resume_client,  # noqa: F401
)


async def _collect(agen) -> list[dict]:
    events = []
    async for line in agen:
        assert line.startswith("data: ")
        events.append(json.loads(line[len("data: ") :]))
    return events


async def _chat(script: list[list[AIMessageChunk]]) -> list[dict]:
    session = ChatSession("sess-toolcall", COMMUNITY)
    session.add_user_message("Which datasets are about attention?")
    with patch(
        "src.api.routers.community.create_community_assistant", return_value=_assistant(script)
    ):
        return await _collect(
            _stream_chat_response(
                COMMUNITY, session, None, None, None, declared_client_tools={"execute_code"}
            )
        )


async def _ask(script: list[list[AIMessageChunk]]) -> list[dict]:
    with patch(
        "src.api.routers.community.create_community_assistant", return_value=_assistant(script)
    ):
        return await _collect(
            _stream_ask_response(COMMUNITY, "Which datasets are about attention?", None, None, None)
        )


def _names(events: list[dict]) -> list[str]:
    return [event["event"] for event in events]


def _tool_calls(events: list[dict]) -> list[dict]:
    return [event for event in events if event["event"] == "tool_call"]


def _without_tool_calls(events: list[dict]) -> list[str]:
    return [name for name in _names(events) if name != "tool_call"]


class TestOneEventPerCall:
    @pytest.mark.asyncio
    async def test_anthropic_chunks_announce_the_call_once_before_it_runs(self) -> None:
        events = await _chat(
            [_anthropic_call("nemar_search_datasets", SEARCH_CALL_ID, SEARCH_ARGS), _answer()]
        )

        assert _tool_calls(events) == [{"event": "tool_call", "name": "nemar_search_datasets"}]
        names = _names(events)
        assert names.index("tool_call") < names.index("tool_start"), (
            "tool_call has to come while the model writes the call, before it runs"
        )
        assert next(e for e in events if e["event"] == "tool_start")["name"] == (
            "nemar_search_datasets"
        )

    @pytest.mark.asyncio
    async def test_bedrock_chunks_announce_the_call_once(self) -> None:
        events = await _chat(
            [
                [
                    _text("Let me search. "),
                    *_bedrock_call("nemar_search_datasets", SEARCH_CALL_ID, SEARCH_ARGS),
                ],
                _answer(),
            ]
        )

        assert _tool_calls(events) == [{"event": "tool_call", "name": "nemar_search_datasets"}]
        names = _names(events)
        assert names.index("content") < names.index("tool_call") < names.index("tool_start")

    @pytest.mark.asyncio
    async def test_openai_style_parallel_calls_are_announced_once_each_in_order(self) -> None:
        for repeat_name in (False, True):
            events = await _chat(
                [
                    _openai_calls(
                        [
                            ("nemar_search_datasets", SEARCH_CALL_ID, SEARCH_ARGS),
                            ("retrieve_toolcallstream_docs", DOCS_CALL_ID, {"url": "https://x"}),
                        ],
                        repeat_name=repeat_name,
                    ),
                    _answer(),
                ]
            )

            assert [e["name"] for e in _tool_calls(events)] == [
                "nemar_search_datasets",
                "retrieve_toolcallstream_docs",
            ], f"repeat_name={repeat_name}"
            if not repeat_name:
                # With the name repeated, langchain-core's chunk merge concatenates it
                # and the call itself fails to resolve; only the announcement, which is
                # this module's concern, is asserted for that shape.
                assert _names(events).count("tool_start") == 2

    @pytest.mark.asyncio
    async def test_each_model_run_announces_its_own_calls(self) -> None:
        """A turn's model runs number their blocks from the same index, so a call in run
        two at index 1 is a new call, not the one run one already announced."""
        events = await _chat(
            [
                _anthropic_call("nemar_search_datasets", SEARCH_CALL_ID, SEARCH_ARGS),
                _anthropic_call("nemar_search_datasets", "toolu_01second", {"query": "memory"}),
                _answer(),
            ]
        )

        assert [e["name"] for e in _tool_calls(events)] == ["nemar_search_datasets"] * 2
        names = _names(events)
        starts = [i for i, name in enumerate(names) if name == "tool_start"]
        calls = [i for i, name in enumerate(names) if name == "tool_call"]
        assert calls[0] < starts[0] < calls[1] < starts[1]

    @pytest.mark.asyncio
    async def test_the_event_carries_the_name_and_nothing_of_the_arguments(self) -> None:
        events = await _chat(
            [_anthropic_call("nemar_search_datasets", SEARCH_CALL_ID, SEARCH_ARGS), _answer()]
        )

        for event in _tool_calls(events):
            assert set(event) == {"event", "name"}
            assert SECRET_QUERY not in json.dumps(event)
        # The arguments still reach the tool, which is where they belong.
        start = next(e for e in events if e["event"] == "tool_start")
        assert start["input"] == SEARCH_ARGS


class TestBrowserCalls:
    @pytest.mark.asyncio
    async def test_a_code_call_is_announced_before_its_tool_request(self) -> None:
        """The case the event exists for: the model writes code for many seconds, and
        the run ends on `tool_request` without any `tool_start`."""
        events = await _chat(
            [
                _anthropic_call(
                    "execute_code",
                    CODE_CALL_ID,
                    {"code": "import numpy as np\n" * 20, "description": "d"},
                )
            ]
        )

        assert _tool_calls(events) == [{"event": "tool_call", "name": "execute_code"}]
        names = _names(events)
        assert "tool_start" not in names
        assert names.index("tool_call") < names.index("tool_request")
        assert names[-1] == "tool_request"


def _resume(client: TestClient, monkeypatch, script: list[list[AIMessageChunk]]) -> list[dict]:
    _parked_session()
    monkeypatch.setattr(
        "src.api.routers.community.create_community_assistant",
        lambda *_a, **_k: _assistant(script),
    )
    response = client.post(
        f"/{COMMUNITY}/chat/resume",
        # An allowed origin authorizes the request, as the widget's own does, whether or
        # not the environment requires an API key (a local .env can turn that on).
        headers={"Origin": ALLOWED_ORIGIN},
        json={
            "session_id": "sess-parked-toolcall",
            "result": {"call_id": CODE_CALL_ID, "status": "ok", "summary": "peak 10.2 Hz"},
            "client_tools": ["execute_code"],
        },
    )
    assert response.status_code == 200
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


class TestTheResumeStream:
    def test_a_search_after_the_browser_run_is_announced(
        self,
        resume_client,  # noqa: F811
        monkeypatch,
    ) -> None:
        events = _resume(
            resume_client,
            monkeypatch,
            [_bedrock_call("nemar_search_datasets", SEARCH_CALL_ID, SEARCH_ARGS), _answer()],
        )

        assert _tool_calls(events) == [{"event": "tool_call", "name": "nemar_search_datasets"}]
        names = _names(events)
        assert names.index("tool_call") < names.index("tool_start") < names.index("done")

    def test_a_second_browser_call_is_announced_before_it_parks(
        self,
        resume_client,  # noqa: F811
        monkeypatch,
    ) -> None:
        events = _resume(
            resume_client,
            monkeypatch,
            [_openai_calls([("execute_code", "call_second", {"code": "y", "description": "d"})])],
        )

        assert _tool_calls(events) == [{"event": "tool_call", "name": "execute_code"}]
        names = _names(events)
        assert names.index("tool_call") < names.index("tool_request")


class TestTheAskStream:
    @pytest.mark.asyncio
    async def test_ask_announces_a_call_before_it_runs(self) -> None:
        events = await _ask(
            [_anthropic_call("nemar_search_datasets", SEARCH_CALL_ID, SEARCH_ARGS), _answer()]
        )

        assert _tool_calls(events) == [{"event": "tool_call", "name": "nemar_search_datasets"}]
        names = _names(events)
        assert names.index("tool_call") < names.index("tool_start") < names.index("done")


class TestNothingElseChanges:
    @pytest.mark.asyncio
    async def test_the_other_events_and_the_answer_are_what_they_were(self) -> None:
        """Every event but `tool_call` is exactly the sequence a stream without it
        would carry, in order, and the final answer is the model's text."""
        events = await _chat(
            [_anthropic_call("nemar_search_datasets", SEARCH_CALL_ID, SEARCH_ARGS), _answer()]
        )

        answer_chunks = len(_pieces(ANSWER, 11))
        assert _without_tool_calls(events) == [
            "session",
            "tool_start",
            "tool_end",
            *["content"] * answer_chunks,
            "done",
        ]
        done = next(e for e in events if e["event"] == "done")
        assert done["content"] == ANSWER
        assert done["citations"] == []
        assert "".join(e["content"] for e in events if e["event"] == "content") == ANSWER

    @pytest.mark.asyncio
    async def test_a_reply_that_calls_nothing_has_no_tool_call(self) -> None:
        events = await _chat([_answer()])

        assert _tool_calls(events) == []
        assert _names(events) == ["session", *["content"] * len(_pieces(ANSWER, 11)), "done"]


# ---------------------------------------------------------------------------
# The chunk reader on its own: odd shapes are skipped, and it never raises
# ---------------------------------------------------------------------------


class _Chunk:
    """A chunk object carrying only `tool_call_chunks`, as the reader sees one."""

    def __init__(self, tool_call_chunks: Any) -> None:
        self.tool_call_chunks = tool_call_chunks


class _ExplodingChunk:
    @property
    def tool_call_chunks(self) -> Any:
        raise RuntimeError("an adapter bug")


class TestTheChunkReader:
    def test_a_chunk_with_no_calls_announces_nothing(self) -> None:
        announced: set = set()
        for chunk in (
            None,
            {},
            _Chunk(None),
            _Chunk([]),
            _Chunk("not a list"),
            AIMessageChunk("x"),
        ):
            assert _tool_call_sse_events(chunk, "run", announced) == []
        assert announced == set()

    def test_entries_without_a_usable_name_are_skipped(self) -> None:
        announced: set = set()
        chunk = _Chunk(
            [
                "not a dict",
                {"name": None, "index": 0},
                {"name": "", "index": 1},
                {"name": "   ", "index": 2},
                {"name": 42, "index": 3},
                {"name": "real_tool", "index": 4},
            ]
        )

        assert _tool_call_sse_events(chunk, "run", announced) == [
            {"event": "tool_call", "name": "real_tool"}
        ]

    def test_a_call_is_known_by_index_then_id_then_name(self) -> None:
        announced: set = set()
        seen = []
        for chunk in (
            _Chunk([{"name": "by_index", "id": "a", "index": 0}]),
            _Chunk([{"name": "by_index", "id": None, "index": 0}]),  # same call
            _Chunk([{"name": "by_id", "id": "b", "index": None}]),
            _Chunk([{"name": "by_id", "id": "b"}]),  # same call
            _Chunk([{"name": "by_name"}]),
            _Chunk([{"name": "by_name", "id": ""}]),  # same call
        ):
            seen += _tool_call_sse_events(chunk, "run", announced)

        assert [e["name"] for e in seen] == ["by_index", "by_id", "by_name"]

    def test_two_calls_to_one_tool_with_no_index_are_told_apart_by_id(self) -> None:
        """A provider that sends no index still gives each call its own id, so two
        parallel searches are two announcements, not one."""
        announced: set = set()
        chunk = _Chunk(
            [
                {"name": "nemar_search_datasets", "id": "call_a"},
                {"name": "nemar_search_datasets", "id": "call_b"},
            ]
        )

        assert len(_tool_call_sse_events(chunk, "run", announced)) == 2

    def test_the_same_slot_in_another_run_is_another_call(self) -> None:
        announced: set = set()
        chunk = _Chunk([{"name": "tool", "index": 0}])

        assert len(_tool_call_sse_events(chunk, "run-1", announced)) == 1
        assert len(_tool_call_sse_events(chunk, "run-2", announced)) == 1
        assert _tool_call_sse_events(chunk, "run-2", announced) == []

    def test_an_overlong_name_is_clipped(self) -> None:
        events = _tool_call_sse_events(_Chunk([{"name": "x" * 1000, "index": 0}]), "run", set())

        assert events == [{"event": "tool_call", "name": "x" * _TOOL_CALL_NAME_MAX_CHARS}]

    def test_a_chunk_that_raises_is_logged_not_raised(self, caplog) -> None:
        assert _tool_call_sse_events(_ExplodingChunk(), "run", set()) == []
        assert "Could not read the tool calls" in caplog.text
