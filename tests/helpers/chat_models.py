"""A chat model that replays a script and records what was bound to it.

None of langchain-core's own fakes work for the browser-execution tests.
`FakeListChatModel`, `FakeMessagesListChatModel` and `GenericFakeChatModel` all raise
`NotImplementedError` from `bind_tools`, which `BaseAgent` catches and then runs the
model with no tools bound at all. That silently removes the thing under test: whether a
client-executed tool reaches the model's tool surface.

This stands in for the LLM and nothing else. Routing, the graph, the node, the session
store and the SSE assembly are all real in the tests that use it, which is the division
`.rules/testing_guidelines.md` draws: a model stand-in is acceptable precisely where the
model's own behavior is not what is being measured.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.chat_models import generate_from_stream
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class ScriptedChatModel(BaseChatModel):
    """Returns `responses` in order, one per call, and remembers bound tools.

    The last response repeats once the script runs out, so a test that only cares about
    the first turn does not have to pad the script to cover retries.
    """

    responses: list[BaseMessage]
    calls: int = 0
    bound_tool_names: list[str] = []
    seen_message_lists: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **_kwargs: Any) -> BaseChatModel:
        """Record the tool surface and stay usable.

        Returning `self` rather than a wrapper is what makes `calls` and
        `seen_message_lists` observable from the test: a bound copy would collect them
        somewhere the test cannot reach.
        """
        self.bound_tool_names = [getattr(tool, "name", str(tool)) for tool in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        # Both are unused and both must stay: BaseChatModel calls `_generate` with them
        # by keyword, so renaming them to the underscore form breaks the call.
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **_kwargs: Any,
    ) -> ChatResult:
        # Recorded so a test can assert what run 2 actually sent, which is the only way
        # to see that a tool result and its images reached the model rather than being
        # dropped between the endpoint and the graph.
        self.seen_message_lists.append(list(messages))

        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        message = self.responses[index]
        return ChatResult(generations=[ChatGeneration(message=message)])


def tool_call_response(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    """An assistant message that calls one tool, shaped as a provider returns it."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def multi_tool_call_response(calls: Sequence[tuple[str, dict[str, Any], str]]) -> AIMessage:
    """An assistant message calling several tools in one batch."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": call_id, "type": "tool_call"}
            for name, args, call_id in calls
        ],
    )


class StreamingScriptedChatModel(ScriptedChatModel):
    """A `ScriptedChatModel` that streams: each scripted reply is a list of chunks.

    The chunks are `AIMessageChunk`s shaped as a provider adapter yields them
    (`tool_call_chunks` with a name on the first chunk of a call, say), and they are
    handed to langchain-core unchanged. So under `astream_events` each one becomes a
    real `on_chat_model_stream` event, and langchain-core's own `generate_from_stream`
    merges them into the message the graph routes on, as it does for a real provider.
    Nothing about the stream's events is built by the test.
    """

    responses: list[BaseMessage] = []
    chunk_script: list[list[AIMessageChunk]]

    def _next_script(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        self.seen_message_lists.append(list(messages))
        index = min(self.calls, len(self.chunk_script) - 1)
        self.calls += 1
        # Copied, because langchain-core writes ids and metadata onto the chunks it is
        # handed, and the last reply repeats once the script runs out.
        return [chunk.model_copy(deep=True) for chunk in self.chunk_script[index]]

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **_kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for chunk in self._next_script(messages):
            yield ChatGenerationChunk(message=chunk)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **_kwargs: Any,
    ) -> ChatResult:
        chunks = [ChatGenerationChunk(message=chunk) for chunk in self._next_script(messages)]
        return generate_from_stream(iter(chunks))
