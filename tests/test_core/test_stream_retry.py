"""What counts as output a retry would repeat (issue #578).

A second try is safe only until the reader has been shown something, or a tool has run, or
a model call has finished. The events here are the ones langchain-core yields, built from
its own chunk classes, so what the helper reads is what a real stream carries.
"""

from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk, ChatMessageChunk
from langchain_core.messages.tool import tool_call_chunk

from src.core.services.stream_retry import _made_progress


def _stream(chunk: Any) -> dict[str, Any]:
    return {"event": "on_chat_model_stream", "data": {"chunk": chunk}}


class TestWhatIsProgress:
    @pytest.mark.parametrize(
        "chunk",
        [
            AIMessageChunk(content=""),
            AIMessageChunk(content=[]),
        ],
        ids=["empty text", "empty blocks"],
    )
    def test_the_chunk_that_opens_a_stream_is_not(self, chunk: AIMessageChunk) -> None:
        assert _made_progress(_stream(chunk)) is False

    @pytest.mark.parametrize(
        "chunk",
        [
            AIMessageChunk(content="Hello"),
            AIMessageChunk(content=[{"type": "text", "text": "Hello", "index": 0}]),
            AIMessageChunk(
                content=[{"type": "reasoning_content", "reasoning_content": {"text": "hmm"}}]
            ),
            AIMessageChunk(
                content=[],
                tool_call_chunks=[tool_call_chunk(name="lookup", id="toolu_01", args="", index=0)],
            ),
        ],
        ids=["text", "text block", "reasoning", "tool call only"],
    )
    def test_text_reasoning_and_a_tool_call_are(self, chunk: AIMessageChunk) -> None:
        assert _made_progress(_stream(chunk)) is True

    def test_a_chunk_of_a_kind_it_does_not_recognize_is_assumed_to_have_been_shown(self) -> None:
        assert _made_progress(_stream(ChatMessageChunk(content="", role="assistant"))) is True

    def test_a_stream_event_with_no_chunk_is_assumed_to_have_been_shown(self) -> None:
        assert _made_progress({"event": "on_chat_model_stream", "data": {}}) is True

    @pytest.mark.parametrize("kind", ["on_tool_start", "on_tool_end", "on_chat_model_end"])
    def test_a_tool_run_and_a_finished_model_call_are(self, kind: str) -> None:
        assert _made_progress({"event": kind, "data": {}}) is True

    @pytest.mark.parametrize(
        "kind", ["on_chain_start", "on_chain_end", "on_chat_model_start", "on_chain_stream"]
    )
    def test_the_graphs_own_bookkeeping_is_not(self, kind: str) -> None:
        assert _made_progress({"event": kind, "data": {}}) is False
