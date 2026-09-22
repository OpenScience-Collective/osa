"""Tests for citation SSE events in the streaming ask/chat generators.

Drives _stream_ask_response()/_stream_chat_response() directly as async
generators, with create_community_assistant() patched to return a fake
assistant whose build_graph().astream_events() yields hand-built
LangGraph-shaped events. This is patching the network/model boundary (no
real LangGraph or Anthropic call happens in this test module), not the
citation-handling business logic under test: the SSE event constructions,
the CitationTracker wiring, and the full_response accumulation are all
real code paths, exercised end to end from a streamed chunk to the emitted
SSE lines.
"""

import json
from typing import Any
from unittest.mock import patch

import pytest

from src.agents.content import CitationAssembler, ContentBlock
from src.api.routers.community import (
    AssistantWithMetrics,
    ChatSession,
    _build_citation_sse_events,
    _stream_ask_response,
    _stream_chat_response,
)


class _FakeChunk:
    """Stand-in for a langchain-anthropic AIMessageChunk: only .content matters here."""

    def __init__(self, content: Any) -> None:
        self.content = content


class _FakeGraph:
    """Stand-in for a compiled LangGraph graph: replays canned astream_events()."""

    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def astream_events(self, _state, **_kwargs):
        for event in self._events:
            yield event


class _FakeAssistant:
    def __init__(self, events: list[dict]) -> None:
        self._graph = _FakeGraph(events)

    def build_graph(self):
        return self._graph


def _text_event(text: str = "", citations: list[dict] | None = None, index: int = 0) -> dict:
    block: dict[str, Any] = {"type": "text", "index": index}
    if text:
        block["text"] = text
    if citations:
        block["citations"] = citations
    return {
        "event": "on_chat_model_stream",
        "data": {"chunk": _FakeChunk([block])},
    }


def _model_end_event() -> dict:
    return {"event": "on_chat_model_end", "data": {}}


async def _collect_sse_events(agen) -> list[dict]:
    events = []
    async for line in agen:
        assert line.startswith("data: ")
        events.append(json.loads(line[len("data: ") :]))
    return events


CITED_DOC_URL = "https://www.hedtags.org/hed-resources/HedAnnotationQuickstart.html"


def test_citation_sse_announces_metadata_before_inline_marker() -> None:
    assembler = CitationAssembler()
    events = _build_citation_sse_events(
        assembler,
        ContentBlock(
            "text",
            "The cited claim.",
            [{"source": CITED_DOC_URL, "title": "HED", "cited_text": "claim"}],
        ),
        block_index=0,
    )

    assert [event["event"] for event in events] == ["citation", "content"]
    assert events[1]["content"] == "[1]"


def _events_for_one_citation() -> list[dict]:
    return [
        _text_event("Tags go in events.tsv."),
        _text_event(
            citations=[
                {
                    "source": CITED_DOC_URL,
                    "title": "HED Annotation Quickstart",
                    "cited_text": "events.tsv",
                }
            ]
        ),
        _text_event(" More context after the citation."),
    ]


class TestStreamAskResponseCitations:
    """Citation SSE events in _stream_ask_response."""

    @pytest.mark.asyncio
    async def test_citation_before_text_is_emitted_after_text(self) -> None:
        events_in = [
            _text_event(
                citations=[
                    {
                        "source": CITED_DOC_URL,
                        "title": "HED Annotation Quickstart",
                        "cited_text": "claim",
                    }
                ]
            ),
            _text_event("The cited claim."),
        ]
        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant(events_in),
            model="claude-haiku-4-5",
            key_source="platform",
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            events = await _collect_sse_events(
                _stream_ask_response("hed", "A question", None, None, None)
            )

        content_strings = [e["content"] for e in events if e["event"] == "content"]
        assert content_strings == ["The cited claim.", "[1]"]

    @pytest.mark.asyncio
    async def test_done_content_moves_mid_sentence_marker_to_sentence_end(self) -> None:
        events_in = [
            _text_event("Infomax is implemented in ru"),
            _text_event(
                citations=[
                    {
                        "source": "https://doc.example/runica",
                        "title": "RUNICA",
                        "cited_text": "runica.m",
                    }
                ]
            ),
            _text_event("nica.m, a MATLAB version."),
        ]
        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant(events_in),
            model="claude-haiku-4-5",
            key_source="platform",
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            events = await _collect_sse_events(
                _stream_ask_response("hed", "A question", None, None, None)
            )

        done_events = [e for e in events if e["event"] == "done"]
        assert done_events[0]["content"] == (
            "Infomax is implemented in runica.m, a MATLAB version.[1]"
        )

    @pytest.mark.asyncio
    async def test_block_indices_reset_between_model_runs(self) -> None:
        events_in = [
            _text_event("Tool result visible.", index=0),
            _model_end_event(),
            _text_event(
                citations=[
                    {
                        "source": CITED_DOC_URL,
                        "title": "HED Annotation Quickstart",
                        "cited_text": "final claim",
                    }
                ],
                index=0,
            ),
            _text_event("Final claim.", index=0),
            _model_end_event(),
        ]
        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant(events_in),
            model="claude-haiku-4-5",
            key_source="platform",
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            events = await _collect_sse_events(
                _stream_ask_response("hed", "A question", None, None, None)
            )

        content_strings = [e["content"] for e in events if e["event"] == "content"]
        assert content_strings == ["Tool result visible.", "Final claim.", "[1]"]

    @pytest.mark.asyncio
    async def test_citation_event_and_inline_marker_emitted(self) -> None:
        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant(_events_for_one_citation()),
            model="claude-haiku-4-5",
            key_source="platform",
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            events = await _collect_sse_events(
                _stream_ask_response("hed", "How do I annotate an event?", None, None, None)
            )

        content_events = [e for e in events if e["event"] == "content"]
        citation_events = [e for e in events if e["event"] == "citation"]
        done_events = [e for e in events if e["event"] == "done"]

        # The marker text must appear as its own content chunk, positioned
        # between the two surrounding text chunks (i.e. right after the
        # claim it supports, not before it and not batched at the end).
        content_strings = [e["content"] for e in content_events]
        assert content_strings == [
            "Tags go in events.tsv.",
            "[1]",
            " More context after the citation.",
        ]

        assert len(citation_events) == 1
        assert citation_events[0]["marker"] == 1
        assert citation_events[0]["source"] == CITED_DOC_URL
        assert citation_events[0]["title"] == "HED Annotation Quickstart"
        assert citation_events[0]["cited_text"] == "events.tsv"

        assert len(done_events) == 1
        assert done_events[0]["citations"] == [
            {
                "marker": 1,
                "source": CITED_DOC_URL,
                "title": "HED Annotation Quickstart",
                "cited_text": "events.tsv",
            }
        ]

    @pytest.mark.asyncio
    async def test_no_citations_yields_empty_list_on_done(self) -> None:
        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant([_text_event("Just an answer, nothing cited.")]),
            model="claude-haiku-4-5",
            key_source="platform",
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            events = await _collect_sse_events(
                _stream_ask_response("hed", "A question", None, None, None)
            )

        citation_events = [e for e in events if e["event"] == "citation"]
        done_events = [e for e in events if e["event"] == "done"]
        assert citation_events == []
        assert done_events[0]["content"] == "Just an answer, nothing cited."
        assert done_events[0]["citations"] == []

    @pytest.mark.asyncio
    async def test_repeated_source_emits_citation_event_only_once(self) -> None:
        events_in = [
            _text_event("First claim."),
            _text_event(citations=[{"source": CITED_DOC_URL, "title": "Doc", "cited_text": "a"}]),
            _text_event(" Second claim."),
            _text_event(citations=[{"source": CITED_DOC_URL, "title": "Doc", "cited_text": "b"}]),
        ]
        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant(events_in), model="claude-haiku-4-5", key_source="platform"
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            events = await _collect_sse_events(
                _stream_ask_response("hed", "A question", None, None, None)
            )

        citation_events = [e for e in events if e["event"] == "citation"]
        content_strings = [e["content"] for e in events if e["event"] == "content"]
        assert len(citation_events) == 1  # only the first occurrence is "new"
        assert content_strings == ["First claim.", "[1]", " Second claim.", "[1]"]


class TestStreamChatResponseCitations:
    """Citation SSE events and session persistence in _stream_chat_response."""

    @pytest.mark.asyncio
    async def test_marker_persisted_into_session_history(self) -> None:
        session = ChatSession(session_id="test-session", community_id="hed")
        session.add_user_message("How do I annotate an event?")

        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant(_events_for_one_citation()),
            model="claude-haiku-4-5",
            key_source="platform",
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            events = await _collect_sse_events(
                _stream_chat_response("hed", session, None, None, None)
            )

        done_events = [e for e in events if e["event"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["content"] == (
            "Tags go in events.tsv.[1] More context after the citation."
        )
        assert done_events[0]["citations"][0]["source"] == CITED_DOC_URL

        # The marker text is part of what gets saved to session history, so
        # a reloaded conversation still shows exactly what the user saw.
        assistant_message = session.messages[-1]
        assert assistant_message.content == (
            "Tags go in events.tsv.[1] More context after the citation."
        )

    @pytest.mark.asyncio
    async def test_citation_before_text_is_emitted_after_text(self) -> None:
        events_in = [
            _text_event(
                citations=[
                    {
                        "source": CITED_DOC_URL,
                        "title": "HED Annotation Quickstart",
                        "cited_text": "claim",
                    }
                ]
            ),
            _text_event("The cited claim."),
        ]
        session = ChatSession(session_id="test-session-2", community_id="hed")
        session.add_user_message("A question")
        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant(events_in),
            model="claude-haiku-4-5",
            key_source="platform",
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            events = await _collect_sse_events(
                _stream_chat_response("hed", session, None, None, None)
            )

        content_strings = [e["content"] for e in events if e["event"] == "content"]
        assert content_strings == ["The cited claim.", "[1]"]

    @pytest.mark.asyncio
    async def test_session_history_uses_sentence_end_marker_position(self) -> None:
        events_in = [
            _text_event("Infomax is implemented in ru"),
            _text_event(
                citations=[
                    {
                        "source": "https://doc.example/runica",
                        "title": "RUNICA",
                        "cited_text": "runica.m",
                    }
                ]
            ),
            _text_event("nica.m, a MATLAB version."),
        ]
        session = ChatSession(session_id="test-session-3", community_id="hed")
        session.add_user_message("A question")
        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant(events_in),
            model="claude-haiku-4-5",
            key_source="platform",
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            await _collect_sse_events(_stream_chat_response("hed", session, None, None, None))

        assert session.messages[-1].content == (
            "Infomax is implemented in runica.m, a MATLAB version.[1]"
        )


SOURCE_A = "https://doc.example/alpha"
SOURCE_B = "https://doc.example/welch"
BROWSER_CALL_ID = "toolu_01cccccccccccccccccccccc"


def _cite(source: str) -> dict:
    return _text_event(citations=[{"source": source, "title": source, "cited_text": "x"}])


def _parked_run_end() -> list[dict]:
    """The two events a run that parks a browser call ends with.

    Replayed rather than produced by a real graph, as in the rest of this module:
    what is under test is how `_stream_chat_response` assembles `tool_request`, and
    `tests/test_api/test_client_tool_streaming.py` covers the real graph emitting
    exactly this shape.
    """
    from langchain_core.messages import AIMessage

    from src.agents.base import CLIENT_TOOLS_NODE

    call = {
        "name": "execute_code",
        "args": {"code": "x", "description": "d"},
        "id": BROWSER_CALL_ID,
    }
    return [
        {"event": "on_chain_end", "name": CLIENT_TOOLS_NODE, "parent_ids": ["root"], "data": {}},
        {
            "event": "on_chain_end",
            "name": "LangGraph",
            "parent_ids": [],
            "data": {
                "output": {
                    "messages": [AIMessage(content="", tool_calls=[call])],
                    "pending_client_call": {
                        "call_id": BROWSER_CALL_ID,
                        "tool": "execute_code",
                        "args": call["args"],
                        "requires_permission": True,
                    },
                }
            },
        },
    ]


class TestCitationsAcrossABrowserTurn:
    """A browser turn is two runs that the reader sees as ONE reply.

    Each run used to number its sources from [1], so a reply whose first half cited
    one source and whose second half cited another showed two different [1]s.
    """

    async def _stream(self, session: ChatSession, events_in: list[dict], **kwargs) -> list[dict]:
        fake_awm = AssistantWithMetrics(
            assistant=_FakeAssistant(events_in), model="claude-haiku-4-5", key_source="platform"
        )
        with patch("src.api.routers.community.create_community_assistant", return_value=fake_awm):
            return await _collect_sse_events(
                _stream_chat_response("hed", session, None, None, None, **kwargs)
            )

    @pytest.mark.asyncio
    async def test_tool_request_carries_run_ones_text_and_citations(self) -> None:
        session = ChatSession(session_id="browser-turn-1", community_id="hed")
        session.add_user_message("Plot alpha power.")
        events = await self._stream(
            session,
            [_text_event("Alpha is 8 to 12 Hz."), _cite(SOURCE_A), *_parked_run_end()],
        )

        request = next(e for e in events if e["event"] == "tool_request")
        assert request["content"] == "Alpha is 8 to 12 Hz.[1]"
        assert [c["source"] for c in request["citations"]] == [SOURCE_A]
        assert [c["marker"] for c in request["citations"]] == [1]
        assert not [e for e in events if e["event"] == "done"]
        # Kept with the parked call, because run 2 is a different request.
        assert [m.source for m in session.pending_call.carried_citations] == [SOURCE_A]

    @pytest.mark.asyncio
    async def test_run_two_continues_run_ones_numbering(self) -> None:
        session = ChatSession(session_id="browser-turn-2", community_id="hed")
        session.add_user_message("Plot alpha power.")
        await self._stream(
            session, [_text_event("Alpha is 8 to 12 Hz."), _cite(SOURCE_A), *_parked_run_end()]
        )
        carried = session.claim_pending_call(BROWSER_CALL_ID).carried_citations

        events = await self._stream(
            session,
            [
                _text_event("Welch estimates it."),
                _cite(SOURCE_B),
                _text_event(" As before, alpha peaks."),
                _cite(SOURCE_A),
            ],
            carried_citations=carried,
        )

        announced = [(e["marker"], e["source"]) for e in events if e["event"] == "citation"]
        assert announced == [(2, SOURCE_B)], "only the new source is announced, as [2]"
        done = next(e for e in events if e["event"] == "done")
        assert [(c["marker"], c["source"]) for c in done["citations"]] == [
            (1, SOURCE_A),
            (2, SOURCE_B),
        ]
        assert done["content"] == "Welch estimates it.[2] As before, alpha peaks.[1]"

    @pytest.mark.asyncio
    async def test_a_turn_with_no_browser_call_still_starts_at_one(self) -> None:
        session = ChatSession(session_id="browser-turn-3", community_id="hed")
        session.add_user_message("Explain alpha.")
        events = await self._stream(session, [_text_event("Welch estimates it."), _cite(SOURCE_B)])
        done = next(e for e in events if e["event"] == "done")
        assert [(c["marker"], c["source"]) for c in done["citations"]] == [(1, SOURCE_B)]


class TestCarriedCitationsAreValidated:
    def test_a_gap_in_carried_markers_is_refused(self) -> None:
        from src.agents.content import CitationMark, CitationTracker

        with pytest.raises(ValueError, match="numbered 1..n"):
            CitationTracker([CitationMark(marker=2, source=SOURCE_A, title="", cited_text="")])

    def test_a_repeated_source_is_refused(self) -> None:
        from src.agents.content import CitationMark, CitationTracker

        with pytest.raises(ValueError, match="one per source"):
            CitationTracker(
                [
                    CitationMark(marker=1, source=SOURCE_A, title="", cited_text=""),
                    CitationMark(marker=2, source=SOURCE_A, title="", cited_text=""),
                ]
            )
