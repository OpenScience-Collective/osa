"""Scripted model runs in the shape each provider adapter yields them.

``StreamingScriptedChatModel`` replays chunks; this says what the chunks of a finished
run look like on each provider path, so a router test can drive the real graph with the
end of a reply as Bedrock, Anthropic or the OpenRouter model leave it:

- langchain-aws sends the stop reason in a ``messageStop`` chunk and the usage in a later
  ``metadata`` chunk.
- langchain-anthropic sends both in the ``message_delta`` chunk.
- The OpenRouter model (``litellm_chat``) closes the stream with one chunk carrying both.

That each adapter really writes these keys is pinned against the real client stacks in
``tests/test_core/test_model_outcome.py`` and ``tests/test_core/test_litellm_chat.py``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from langchain_core.messages import AIMessageChunk
from langchain_core.tools import tool
from starlette.requests import Request

from src.api.routers.community import AssistantWithMetrics
from src.assistants.community import CommunityAssistant
from src.core.config.community import CommunityConfig
from src.core.services.anthropic_models import BEDROCK_MODELS, DEFAULT_MODEL
from src.core.services.litellm_llm import DEFAULT_MODEL as OPENROUTER_MODEL
from src.core.services.model_outcome import USAGE_ESTIMATED_KEY
from tests.helpers.chat_models import StreamingScriptedChatModel

COMMUNITY = "scriptedreply"
ORIGIN = "https://scripted.example"
QUESTION = "What does Sensory-event mark?"
ANSWER = "Sensory-event marks a stimulus presented to the participant."
USAGE = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}


@tool
def lookup_scriptedreply_docs(query: str) -> str:
    """Look something up. A real, server-executed tool."""
    return f"documentation for {query}"


def community_config() -> CommunityConfig:
    return CommunityConfig(
        id=COMMUNITY,
        name="Scripted Reply",
        description="A community whose replies are scripted",
        cors_origins=[ORIGIN],
    )


def text_chunk(text: str) -> AIMessageChunk:
    return AIMessageChunk(content=[{"type": "text", "text": text, "index": 0}])


_ONLY_OPENROUTER = "only the OpenRouter model marks usage as estimated"


def _bedrock_end(stop: str, usage: bool, estimated: bool) -> list[AIMessageChunk]:
    if estimated:
        raise ValueError(_ONLY_OPENROUTER)
    chunks = [
        AIMessageChunk(
            content="",
            response_metadata={"stopReason": stop, "model_provider": "bedrock_converse"},
        )
    ]
    if usage:
        chunks.append(
            AIMessageChunk(
                content="",
                response_metadata={
                    "metrics": {"latencyMs": [1]},
                    "model_provider": "bedrock_converse",
                },
                usage_metadata=USAGE,
            )
        )
    return chunks


def _anthropic_end(stop: str, usage: bool, estimated: bool) -> list[AIMessageChunk]:
    if estimated:
        raise ValueError(_ONLY_OPENROUTER)
    return [
        AIMessageChunk(
            content=[],
            response_metadata={
                "stop_reason": stop,
                "stop_sequence": None,
                "model_provider": "anthropic",
            },
            usage_metadata=USAGE if usage else None,
        )
    ]


def _litellm_end(stop: str, usage: bool, estimated: bool) -> list[AIMessageChunk]:
    metadata: dict[str, object] = {"finish_reason": stop}
    if estimated:
        metadata[USAGE_ESTIMATED_KEY] = True
    return [
        AIMessageChunk(
            content="",
            response_metadata=metadata,
            usage_metadata=USAGE if usage else None,
        )
    ]


@dataclass(frozen=True)
class Provider:
    """A provider path: its model, the stop reasons it names, and how a run ends."""

    name: str
    model: str
    cut_off: str
    finished: str
    end: Callable[[str, bool, bool], list[AIMessageChunk]]


PROVIDERS = [
    Provider("bedrock", sorted(BEDROCK_MODELS)[0], "max_tokens", "end_turn", _bedrock_end),
    Provider("anthropic", DEFAULT_MODEL, "max_tokens", "end_turn", _anthropic_end),
    Provider("openrouter", OPENROUTER_MODEL, "length", "stop", _litellm_end),
]


def scripted_reply(
    provider: Provider,
    text: str,
    *,
    cut_off: bool = False,
    stop: str | None = None,
    usage: bool = True,
    estimated: bool = False,
) -> list[AIMessageChunk]:
    """One model run: the text (none for ``""``), then how it ended.

    Args:
        provider: Whose adapter the chunks imitate.
        text: What the model wrote, nine characters to a chunk.
        cut_off: End on the provider's output-limit stop reason, else on a normal one.
        stop: End on this stop reason instead (``refusal``, ``guardrail_intervened``, ...),
            written under the key this provider's adapter uses.
        usage: Include the usage the provider reports at the end of a run.
        estimated: Mark the usage as LiteLLM's estimate (the OpenRouter path only).
    """
    body = [text_chunk(piece) for piece in (text[i : i + 9] for i in range(0, len(text), 9))]
    reason = stop or (provider.cut_off if cut_off else provider.finished)
    return [*body, *provider.end(reason, usage, estimated)]


def assistant_for(provider: Provider, script: list[list[AIMessageChunk]]) -> AssistantWithMetrics:
    """The real assistant and graph over a model that replays ``script``."""
    assistant = CommunityAssistant(
        model=StreamingScriptedChatModel(chunk_script=script),
        config=community_config(),
        preload_docs=False,
        additional_tools=[lookup_scriptedreply_docs],
    )
    return AssistantWithMetrics(assistant=assistant, model=provider.model, key_source="platform")


def real_request(request_id: str = "req-scripted") -> Request:
    """A real Starlette request carrying the id the metrics middleware would assign."""
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.request_id = request_id
    return request


async def collect(agen: AsyncIterator[str]) -> list[dict]:
    """The events of a server-sent-events generator, parsed."""
    events = []
    async for line in agen:
        assert line.startswith("data: ")
        events.append(json.loads(line[len("data: ") :]))
    return events
