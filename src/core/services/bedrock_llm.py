"""Amazon Bedrock chat models: GPT-6 Luna, Qwen3 Next 80B A3B, and gpt-oss-120b.

These three are the non-Anthropic models OSA offers (``BEDROCK_MODELS`` in
``anthropic_models.py``). All three are called through the Bedrock Converse API,
one transport for every model, with a Bedrock API key (a bearer token) that the
platform pays for. Claude does not come through here: it stays on the Claude
Platform on AWS (``anthropic_llm.py``), which is a different service with a
different endpoint and billing.

What the Converse API does and does not give these models, measured against the
live service, decides the shape of this module:

- **Tool calls and streaming** work on all three, through ``ChatBedrockConverse``.
- **Native citations do not**: the service answers a ``searchResult`` block with
  "This model doesn't support the searchResult field". ``TaggedCitationChatBedrock``
  supplies the same numbered citations by convention (see ``tagged_citations``).
- **Prompt caching** is automatic on GPT-6 Luna: a repeated prompt prefix is read
  back from cache and the response reports the tokens read and written, which
  ``ChatBedrockConverse`` maps to ``usage_metadata.input_token_details`` exactly as
  the Anthropic path does. Explicit cache points are rejected by all three models,
  and gpt-oss-120b and Qwen3 Next do no caching at all, so there is nothing to
  send: keeping the system prompt and tool list byte-stable, as the rest of OSA
  already does, is what earns Luna its cache reads.
- **Sampling** parameters are rejected by Luna and accepted by the other two.
"""

import logging
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

import boto3
import botocore.session
from botocore.config import Config as BotocoreConfig
from botocore.tokens import FrozenAuthToken, TokenProviderChain
from langchain_aws import ChatBedrockConverse
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables.config import run_in_executor

from src.api.config import Settings, get_settings
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    accepts_temperature,
    normalize_model,
)
from src.core.services.tagged_citations import (
    MarkerStream,
    SourceRegistry,
    pieces_to_blocks,
    prepare_messages,
    rewrite_content,
)

logger = logging.getLogger(__name__)

#: Seconds allowed for a connection to Bedrock to open.
CONNECT_TIMEOUT = 10.0

#: Threads that wait on Bedrock's streams. ``langchain-aws`` has no async client, so
#: an async stream waits for each chunk on a worker thread, and a reasoning model can
#: think for half a minute before its first one. On the event loop's default executor
#: (cpu count + 4 threads, at most 32) that is a ceiling of a handful of concurrent
#: streams on a small host, and it starves every other user of the default executor.
#: Threads are made on demand, so the bound costs nothing until it is used.
STREAM_WORKERS = 64
_STREAM_EXECUTOR = ThreadPoolExecutor(
    max_workers=STREAM_WORKERS, thread_name_prefix="bedrock-stream"
)


#: Block types that carry a model's reasoning. None is sent back: GPT-6 Luna rejects
#: ``reasoningContent.reasoningText.text`` in an assistant message with a 400, and
#: gpt-oss-120b and Qwen3 Next reject its ``signature`` (all tested against the live
#: service). Every turn of a tool loop replays the history, so keeping the reasoning
#: would fail the second model call of any conversation that used a tool. Claude's
#: ``thinking`` blocks are dropped too; a reply that switched models mid-chat may
#: carry them.
_REASONING_BLOCK_TYPES = frozenset(
    {"reasoning_content", "reasoning", "thinking", "redacted_thinking"}
)


def _without_reasoning(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Drop reasoning blocks from the assistant turns of a conversation.

    The caller's messages are not changed; a turn with no reasoning is passed on as is.
    """
    cleaned: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, AIMessage) and isinstance(message.content, list):
            kept = [
                block
                for block in message.content
                if not (isinstance(block, dict) and block.get("type") in _REASONING_BLOCK_TYPES)
            ]
            if len(kept) != len(message.content):
                message = message.model_copy(update={"content": kept})
        cleaned.append(message)
    return cleaned


class _StaticBearerToken:
    """A botocore token provider that always answers with one Bedrock API key."""

    METHOD = "osa-static-bearer"

    def __init__(self, token: str) -> None:
        self._token = token

    def load_token(self, **kwargs: Any) -> FrozenAuthToken:  # noqa: ARG002 (botocore's interface)
        return FrozenAuthToken(self._token)


@lru_cache(maxsize=16)
def _bedrock_client(
    service_name: str, region: str, api_key: str, connect_timeout: float, read_timeout: float
) -> Any:
    """Build a boto3 client that authenticates with a Bedrock API key and nothing else.

    ``ChatBedrockConverse`` can take the key itself, but it then builds its
    clients through botocore's ambient credential chain first, and on a machine
    whose default AWS profile is an ``aws login`` session that chain raises
    "requires ``botocore[crt]``" before the key is ever used. Bearer auth never
    signs a request, but botocore resolves credentials when it creates the client,
    so this gives the session placeholder credentials to stop the walk, and puts the
    key in a token chain of its own. The session is private to the client: nothing
    is read from, or written to, the process environment.

    Cached per (service, Region, key, timeouts). Building a session and a client
    costs 70 to 100 ms of CPU on the event loop's thread, and a client made per
    request never reuses a connection; boto3 clients are safe to share between
    threads, and ``ChatBedrockConverse`` does not change one it is handed.
    """
    session = botocore.session.get_session()
    session.set_credentials("unused", "unused")
    session.register_component(
        "token_provider", TokenProviderChain(providers=[_StaticBearerToken(api_key)])
    )
    config = BotocoreConfig(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries={"max_attempts": 2, "mode": "standard"},
        auth_scheme_preference="httpBearerAuth",
    )
    return boto3.Session(botocore_session=session).client(
        service_name, region_name=region, config=config
    )


class TaggedCitationChatBedrock(ChatBedrockConverse):
    """``ChatBedrockConverse`` that speaks the tagged-source citation convention.

    A subclass rather than a wrapper, for the reason ``CachingChatAnthropic``
    is one: ``bind_tools`` and the streaming entry points stay native, so the
    LangGraph agent and ``astream_events`` see an ordinary chat model. ``_generate``
    and ``_stream`` are overridden to rewrite tags; ``_astream`` runs ``_stream`` on
    this module's own worker threads (see ``STREAM_WORKERS``).

    Outgoing messages have their ``search_result`` blocks rewritten as tagged text;
    incoming text has its ``[src:N]`` tags cut out and returned as ``citations``
    (see ``tagged_citations``). A reply with no tags passes through untouched.
    """

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate a complete reply, with its tags turned into citations."""
        prepared, registry = prepare_messages(_without_reasoning(messages))
        result = super()._generate(prepared, stop, run_manager, **kwargs)
        for generation in result.generations:
            generation.message.content = rewrite_content(generation.message.content, registry)
        return result

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Stream a reply, releasing text as soon as it cannot be part of a tag."""
        prepared, registry = prepare_messages(_without_reasoning(messages))
        streams: dict[int, MarkerStream] = {}

        # ChatBedrockConverse reports each token to the run manager as it arrives,
        # tags and all. It is not given the run manager, and the tokens are reported
        # here instead, after the tags are cut out.
        for chunk in super()._stream(prepared, stop, None, **kwargs):
            retagged = _retag_chunk(chunk, registry, streams)
            if run_manager:
                run_manager.on_llm_new_token(retagged.message.text, chunk=retagged)
            yield retagged

        # Text held back in case it became a tag, once the reply is over.
        for index, stream in streams.items():
            blocks = pieces_to_blocks(stream.finish(), registry, index)
            if blocks:
                held = ChatGenerationChunk(message=AIMessageChunk(content=blocks))
                if run_manager:
                    run_manager.on_llm_new_token(held.message.text, chunk=held)
                yield held

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Stream on this module's own threads rather than the event loop's default pool.

        The same as the base class's version, which runs ``_stream`` and waits for
        each chunk in the default executor; only the executor differs (see
        ``STREAM_WORKERS``).
        """
        iterator = self._stream(
            messages, stop, run_manager.get_sync() if run_manager else None, **kwargs
        )
        done = object()
        while True:
            item = await run_in_executor(_STREAM_EXECUTOR, next, iterator, done)
            if item is done:
                break
            yield item  # type: ignore[misc]


def _retag_chunk(
    chunk: ChatGenerationChunk,
    registry: SourceRegistry,
    streams: dict[int, MarkerStream],
) -> ChatGenerationChunk:
    """Replace the text in one streamed chunk with tag-free text and citation blocks.

    Every other block (reasoning, tool use) and every other field of the chunk
    (tool-call chunks, usage) is passed through as it came.
    """
    content = chunk.message.content
    if isinstance(content, str):
        content = [{"type": "text", "text": content, "index": 0}] if content else []
    blocks: list[Any] = []
    changed = False
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            index = block.get("index") or 0
            stream = streams.setdefault(index, MarkerStream(registry))
            blocks.extend(pieces_to_blocks(stream.feed(block.get("text", "")), registry, index))
            changed = True
        else:
            blocks.append(block)
    if not changed:
        return chunk
    message = chunk.message.model_copy(update={"content": blocks})
    return chunk.model_copy(update={"message": message})


def create_bedrock_llm(
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float = 120.0,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Create a chat model for one of the Bedrock-served models.

    Args:
        model: An id from ``BEDROCK_MODELS``, or an alias of one.
        temperature: Sampling temperature. Sent only to models that accept it
            (not GPT-6 Luna, which the service rejects with a 400); dropped for
            the others.
        max_tokens: Most tokens to generate, reasoning included. Defaults to
            ``settings.bedrock_max_output_tokens``.
        timeout: Seconds to wait for Bedrock to answer. Reasoning at maximum
            effort can think for a while before the first token, so this is longer
            than the Claude path's.
        settings: Where the Bedrock API key and Region come from. Defaults to
            ``get_settings()``.

    Returns:
        A ``TaggedCitationChatBedrock`` that streams.

    Raises:
        ValueError: If ``model`` is not one of the Bedrock-served models.
        RuntimeError: If no Bedrock API key is configured.
    """
    resolved_settings = settings or get_settings()
    resolved_model = normalize_model(model)
    spec = BEDROCK_MODELS.get(resolved_model)
    if spec is None:
        raise ValueError(
            f"{resolved_model} is not served from Amazon Bedrock; "
            "Claude models are built with create_anthropic_llm"
        )
    if not resolved_settings.bedrock_api_key:
        raise RuntimeError("AWS_BEARER_TOKEN_BEDROCK is not set (Bedrock models require it)")

    region = spec.region or resolved_settings.bedrock_region
    key = resolved_settings.bedrock_api_key

    kwargs: dict[str, Any] = {
        "model": spec.invoke_id,
        "region_name": region,
        "client": _bedrock_client("bedrock-runtime", region, key, CONNECT_TIMEOUT, timeout),
        # Only used to look up application inference profiles, which OSA does not
        # use; passed so the model does not build one through the ambient chain.
        "bedrock_client": _bedrock_client("bedrock", region, key, CONNECT_TIMEOUT, timeout),
        "max_tokens": max_tokens
        if max_tokens is not None
        else resolved_settings.bedrock_max_output_tokens,
        # src/api/routers/community.py streams with graph.astream_events(...), which
        # needs the model to stream.
        "streaming": True,
    }
    if spec.extra_request_fields:
        kwargs["additional_model_request_fields"] = dict(spec.extra_request_fields)
    if temperature is not None:
        if accepts_temperature(resolved_model):
            kwargs["temperature"] = temperature
        else:
            logger.debug(
                "Dropping temperature=%s for %s: the model rejects sampling parameters",
                temperature,
                resolved_model,
            )
    return TaggedCitationChatBedrock(**kwargs)
