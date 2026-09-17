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

from src.api.routers.community import (
    AssistantWithMetrics,
    ChatSession,
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


def _text_event(text: str = "", citations: list[dict] | None = None) -> dict:
    block: dict[str, Any] = {"type": "text", "index": 0}
    if text:
        block["text"] = text
    if citations:
        block["citations"] = citations
    return {
        "event": "on_chat_model_stream",
        "data": {"chunk": _FakeChunk([block])},
    }


async def _collect_sse_events(agen) -> list[dict]:
    events = []
    async for line in agen:
        assert line.startswith("data: ")
        events.append(json.loads(line[len("data: ") :]))
    return events


CITED_DOC_URL = "https://www.hedtags.org/hed-resources/HedAnnotationQuickstart.html"


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
        assert done_events[0]["citations"][0]["source"] == CITED_DOC_URL

        # The marker text is part of what gets saved to session history, so
        # a reloaded conversation still shows exactly what the user saw.
        assistant_message = session.messages[-1]
        assert assistant_message.content == (
            "Tags go in events.tsv.[1] More context after the citation."
        )
