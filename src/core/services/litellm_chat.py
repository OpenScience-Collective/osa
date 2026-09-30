"""The chat model behind OpenRouter: prompt caching, tagged citations, usage details.

``create_openrouter_llm`` (``litellm_llm.py``) returns a ``TaggedCitationChatLiteLLM``.
It is a ``ChatLiteLLM`` and nothing else: ``bind_tools`` and the streaming entry
points stay native, so the LangGraph agent and ``astream_events`` see an ordinary
chat model. (It replaces a wrapper that held a ``ChatLiteLLM`` and forwarded to it;
a wrapper had to be re-applied around every ``bind_tools`` result, and could not see
the reply it needed to rewrite.) Three things are added, all at the model boundary:

- **Prompt-cache breakpoints.** The system prompt and the last message of the
  conversation carry ``cache_control``, so a tool loop re-reads the conversation
  so far at the cache rate. Only Anthropic's models take the markers (OpenAI's cache
  a repeated prefix on their own), so only they are sent them.
- **Tagged citations.** Tool results carry ``search_result`` blocks, which the
  OpenAI-style chat API cannot express. They are rewritten as text tagged
  ``[src:N]``, and the tags the model writes come back as the ``citations`` the
  Anthropic path produces (see ``tagged_citations``).
- **Usage details.** LiteLLM reports prompt and completion totals only; cache
  reads and writes and reasoning tokens are in the raw usage and are carried onto
  ``usage_metadata`` where the metrics read them.

Two more things ride the same way, because ``langchain-litellm`` drops both from a
streamed reply: the ``finish_reason`` (``length`` is a reply cut off, see
``model_outcome``) lands in ``response_metadata``, where a complete reply already has
it, and a note that the usage is LiteLLM's own estimate, not the provider's, lands there
too (``USAGE_ESTIMATED_KEY``).
"""

import json
import logging
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_litellm import ChatLiteLLM

from src.core.services.model_outcome import USAGE_ESTIMATED_KEY
from src.core.services.tagged_citations import (
    ChunkRetagger,
    prepare_messages,
    rewrite_content,
)

logger = logging.getLogger(__name__)

#: Where the usage details ride from the LiteLLM stream to ``usage_metadata``:
#: ``provider_specific_fields`` is the one field LiteLLM copies from a raw chunk onto
#: the message chunk it builds.
_USAGE_DETAILS_KEY = "osa_usage_details"
#: The stream's ``finish_reason``, carried the same way to ``response_metadata``.
_FINISH_REASON_KEY = "osa_finish_reason"
#: Set when the usage is LiteLLM's estimate; carried to ``response_metadata`` too.
_USAGE_ESTIMATED_KEY = "osa_usage_estimated"
_CARRIED_KEYS = (_USAGE_DETAILS_KEY, _FINISH_REASON_KEY, _USAGE_ESTIMATED_KEY)

_CACHE_MARKER = {"type": "ephemeral"}


def _text_of(content: str | list[Any], separator: str = "") -> str:
    """The text of a message's content: a string as it is, a block list as its text blocks.

    OpenAI-style chat takes a string (some providers take nothing else) for a tool
    result or an earlier assistant turn. A block list here comes from the tagged
    rewrite or from a turn another model wrote, whose reasoning and tool-use blocks
    are not part of what a reader was shown.

    Args:
        content: A message's content.
        separator: Put between text blocks. A tool result's blocks are separate
            documents, each ending with its own reminder and the next opening with its
            own ``[src:N]`` header, so they are separated by a blank line; an assistant
            turn's blocks are segments of one reply that carry their own whitespace.
    """
    if isinstance(content, str):
        return content
    return separator.join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _require_content(message: BaseMessage, index: int) -> None:
    if not hasattr(message, "content"):
        raise ValueError(
            f"Invalid message at index {index}: missing 'content' attribute. "
            f"Message type: {type(message).__name__}. All messages must have a 'content' attribute."
        )
    if message.content is None and not (isinstance(message, AIMessage) and message.tool_calls):
        raise ValueError(
            f"{type(message).__name__} at index {index} has None content. "
            "All messages must have non-None content."
        )


def _assistant_dict(message: AIMessage, index: int) -> dict[str, Any]:
    result: dict[str, Any] = {"role": "assistant", "content": _text_of(message.content or "")}
    if message.tool_calls:
        result["tool_calls"] = []
        for position, call in enumerate(message.tool_calls):
            if "name" not in call or "args" not in call:
                raise ValueError(
                    f"Malformed tool_call at index {position} in AIMessage at index {index}: "
                    f"missing 'name' or 'args'. Got keys: {list(call.keys())}"
                )
            args = call["args"]
            result["tool_calls"].append(
                {
                    "id": call.get("id", call.get("name", "")),
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                    },
                }
            )
    return result


def to_message_dicts(messages: Sequence[BaseMessage], cache: bool = True) -> list[dict[str, Any]]:
    """Convert LangChain messages to the dicts LiteLLM sends, with cache breakpoints.

    Args:
        messages: The conversation.
        cache: Mark the system messages and the last message with ``cache_control``.

    Returns:
        One dict per message. Content is always text: block lists are reduced to
        their text blocks (see ``_text_of``).

    Raises:
        TypeError: If ``messages`` is not a list.
        ValueError: If a message has no content, a tool message has no
            ``tool_call_id``, or a tool call has no name or arguments.
    """
    if messages is None:
        raise ValueError("Messages list cannot be None")
    if not isinstance(messages, list):
        raise TypeError(f"Expected list of messages, got {type(messages).__name__}")

    result: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        _require_content(message, index)
        if isinstance(message, SystemMessage):
            text = _text_of(message.content, "\n\n")
            content: Any = [{"type": "text", "text": text, "cache_control": _CACHE_MARKER}]
            result.append({"role": "system", "content": content if cache else text})
        elif isinstance(message, AIMessage):
            result.append(_assistant_dict(message, index))
        elif isinstance(message, ToolMessage):
            if not message.tool_call_id:
                raise ValueError(
                    f"ToolMessage at index {index} has no tool_call_id. "
                    "ToolMessages must reference a tool call."
                )
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": _text_of(message.content, "\n\n"),
                }
            )
        else:
            # HumanMessage, and any other type, is shown to the model as the user.
            if not isinstance(message, HumanMessage):
                logger.debug(
                    "Unknown message type %s at index %d, treating as user message",
                    type(message).__name__,
                    index,
                )
            result.append({"role": "user", "content": _text_of(message.content)})

    if cache:
        _mark_last_message(result)
    return result


def _mark_last_message(messages: list[dict[str, Any]]) -> None:
    """Put a cache breakpoint on the last message that has text to carry it.

    A second breakpoint at the end of the conversation lets a tool loop re-read
    everything so far at the cache rate; without it only the system prompt is
    cached and the growing conversation is billed in full on every iteration.
    Anthropic allows four breakpoints per request; the system prompt uses one.
    """
    if len(messages) < 2:
        return

    for index in range(len(messages) - 1, 0, -1):
        message = messages[index]
        content = message.get("content")

        if isinstance(content, list) and any(
            isinstance(block, dict) and "cache_control" in block for block in content
        ):
            return

        if isinstance(content, str) and content:
            message["content"] = [{"type": "text", "text": content, "cache_control": _CACHE_MARKER}]
            return

        # An assistant turn that only called tools has no text to mark: look earlier.
        if message.get("role") == "assistant" and not content and message.get("tool_calls"):
            continue
        break

    logger.debug("No suitable message found for the trailing cache breakpoint")


def _field(container: Any, name: str) -> Any:
    """``container[name]`` for a dict, ``container.name`` for the objects LiteLLM builds."""
    if container is None:
        return None
    if isinstance(container, Mapping):
        return container.get(name)
    return getattr(container, name, None)


def usage_details(usage: Any) -> dict[str, dict[str, int]]:
    """Pick the cache and reasoning counts out of a raw OpenAI-style ``usage``.

    Args:
        usage: ``usage`` as LiteLLM returns it: a dict on a streamed chunk, a
            ``Usage`` object on a complete response. OpenRouter's fields, normalized.

    Returns:
        ``{"input": {"cache_read": n, "cache_creation": n}, "output": {"reasoning": n}}``
        with only the counts the provider reported and that are not zero.
    """
    prompt = _field(usage, "prompt_tokens_details")
    reads = _field(prompt, "cached_tokens") or 0
    writes = (
        _field(prompt, "cache_write_tokens") or _field(usage, "cache_creation_input_tokens") or 0
    )
    reasoning = _field(_field(usage, "completion_tokens_details"), "reasoning_tokens") or 0
    details: dict[str, dict[str, int]] = {}
    if reads or writes:
        details["input"] = {
            key: int(count)
            for key, count in (("cache_read", reads), ("cache_creation", writes))
            if count
        }
    if reasoning:
        details["output"] = {"reasoning": int(reasoning)}
    return details


class _ProviderUsage:
    """The usage the provider itself reported, read from beneath LiteLLM's stream wrapper.

    LiteLLM's wrapper builds its own final usage chunk and emits it as soon as it sees
    the finish, before it has read the rest of the provider's stream: OpenRouter
    documents its usage as a chunk that repeats the ``finish_reason`` after the real
    one. What the wrapper reports is then a local token estimate, with no cache or
    reasoning counts (``prompt_tokens_details: None``). The provider's own chunk does
    pass through the handler under the wrapper (``completion_stream``), so a tap there
    sees it, though only after the wrapper's has gone by; the wrapper's usage is held
    back and the real one is sent when the stream ends.

    Attributes:
        usage: The provider's usage, once seen.
        estimate: The wrapper's own usage, kept for a provider that reports none.
        finish_reason: Why the provider said the reply stopped, from the first chunk
            that says so (OpenRouter repeats it on the usage chunk).
    """

    def __init__(self) -> None:
        self.usage: dict[str, Any] | None = None
        self.estimate: dict[str, Any] | None = None
        self.finish_reason: str | None = None

    def note(self, item: Any) -> None:
        usage = _field(item, "usage")
        if usage is None:
            return
        as_dict = usage if isinstance(usage, dict) else usage.model_dump()
        if as_dict.get("prompt_tokens") or as_dict.get("completion_tokens"):
            self.usage = as_dict

    @property
    def final(self) -> dict[str, Any] | None:
        """The usage to report: the provider's, else the wrapper's estimate."""
        return self.usage or self.estimate


class _TappedStream:
    """A stream that lets ``_ProviderUsage`` see every item on its way through.

    LiteLLM's handlers set up their iteration state in ``__iter__`` / ``__aiter__``
    (an async one fails in ``__anext__`` if it was never asked), so those are passed
    on to the stream inside and its answer is what ``__next__`` / ``__anext__`` use.
    """

    def __init__(self, inner: Any, tap: _ProviderUsage) -> None:
        self._inner = inner
        self._iter: Any = None
        self._aiter: Any = None
        self._tap = tap

    def __iter__(self) -> "_TappedStream":
        self._iter = iter(self._inner)
        return self

    def __next__(self) -> Any:
        if self._iter is None:
            self._iter = iter(self._inner)
        item = next(self._iter)
        self._tap.note(item)
        return item

    def __aiter__(self) -> "_TappedStream":
        self._aiter = self._inner.__aiter__()
        return self

    async def __anext__(self) -> Any:
        if self._aiter is None:
            self._aiter = self._inner.__aiter__()
        item = await self._aiter.__anext__()
        self._tap.note(item)
        return item

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _tap_provider_usage(response: Any) -> _ProviderUsage:
    """Start watching a LiteLLM stream for the provider's own usage."""
    tap = _ProviderUsage()
    inner = getattr(response, "completion_stream", None)
    if inner is None:
        # LiteLLM changed its wrapper: fall back to what it reports. The live tests
        # (tests/test_integration/test_openrouter_citations.py) show it.
        logger.warning("LiteLLM's stream has no completion_stream; usage details unavailable")
        return tap
    response.completion_stream = _TappedStream(inner, tap)
    return tap


def _carry_usage_details(raw: Any, tap: _ProviderUsage) -> Any:
    """Prepare one outgoing stream chunk: the wrapper's own usage is held back (see
    ``_ProviderUsage``), and the reason the reply stopped is noted for the closing chunk,
    since ``ChatLiteLLM`` reads neither from a streamed chunk's choice."""
    data = raw if isinstance(raw, dict) else raw.model_dump()
    usage = data.get("usage")
    if usage:
        tap.estimate = usage
        data["usage"] = None
    choices = data.get("choices") or []
    reason = _field(choices[0], "finish_reason") if choices else None
    if reason and tap.finish_reason is None:
        tap.finish_reason = str(reason)
    return data


def _closing_chunk(tap: _ProviderUsage) -> dict[str, Any] | None:
    """The final chunk of a stream: what the stream learned that ``ChatLiteLLM`` would
    drop, and nothing else. None when it learned nothing.

    That is the usage with its cache and reasoning counts, the reason the reply stopped,
    and whether the usage is LiteLLM's estimate because the provider sent none. It needs
    one (empty) choice: ``ChatLiteLLM`` skips a chunk with no choices, and its usage with
    it.
    """
    usage = tap.final
    if usage is None and tap.finish_reason is None:
        return None
    chunk: dict[str, Any] = {
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}],
    }
    carried: dict[str, Any] = {}
    if usage is not None:
        chunk["usage"] = usage
        details = usage_details(usage)
        if details:
            carried[_USAGE_DETAILS_KEY] = details
        if tap.usage is None:
            carried[_USAGE_ESTIMATED_KEY] = True
    if tap.finish_reason is not None:
        carried[_FINISH_REASON_KEY] = tap.finish_reason
    if carried:
        chunk["provider_specific_fields"] = carried
    return chunk


def _apply_usage_details(message: AIMessage | AIMessageChunk, details: dict[str, Any]) -> None:
    """Put usage details on a message's ``usage_metadata`` (which must already be set)."""
    usage = message.usage_metadata
    if usage is None:
        return
    if "input" in details:
        usage["input_token_details"] = {**usage.get("input_token_details", {}), **details["input"]}
    if "output" in details:
        usage["output_token_details"] = {
            **usage.get("output_token_details", {}),
            **details["output"],
        }


def _take_carried_fields(chunk: ChatGenerationChunk) -> None:
    """Move what the closing chunk carried out of ``provider_specific_fields``: the usage
    details to ``usage_metadata``, the finish reason and the estimate note to
    ``response_metadata``."""
    message = chunk.message
    fields = message.additional_kwargs.get("provider_specific_fields")
    if not isinstance(fields, dict) or not any(key in fields for key in _CARRIED_KEYS):
        return
    carried = {key: fields.pop(key) for key in _CARRIED_KEYS if key in fields}
    if not fields:
        del message.additional_kwargs["provider_specific_fields"]
    if not isinstance(message, AIMessageChunk):
        return
    if _USAGE_DETAILS_KEY in carried:
        _apply_usage_details(message, carried[_USAGE_DETAILS_KEY])
    if _FINISH_REASON_KEY in carried:
        message.response_metadata["finish_reason"] = carried[_FINISH_REASON_KEY]
    if carried.get(_USAGE_ESTIMATED_KEY):
        message.response_metadata[USAGE_ESTIMATED_KEY] = True


def takes_cache_markers(model: str) -> bool:
    """Whether OpenRouter passes ``cache_control`` breakpoints on to this model.

    Only Anthropic's models act on them. LiteLLM drops the marker for any other slug
    but keeps the message reshaped into a one-part list, which is a compatibility risk
    with nothing gained, so other models are sent plain strings. (OpenAI models cache
    a repeated prefix on their own, without a marker.)
    """
    return model.removeprefix("openrouter/").startswith("anthropic/")


class TaggedCitationChatLiteLLM(ChatLiteLLM):
    """``ChatLiteLLM`` with prompt-cache breakpoints, tagged citations and usage details.

    Attributes:
        prompt_caching: Send ``cache_control`` breakpoints, to the models that take them
            (see ``takes_cache_markers``).
    """

    prompt_caching: bool = True

    @property
    def _client_params(self) -> dict[str, Any]:
        """Request parameters, with this instance's key on every call and nothing shared.

        ``ChatLiteLLM`` keeps its credentials on the ``litellm`` module (its ``client``),
        which every instance in the process shares, and never sends them with the call.
        Requests running at the same time under different keys (a caller's own key next
        to the platform's) then send whichever key was written last: 20 of 40
        interleaved requests went out under the other key when measured. A key in the
        call arguments takes precedence over the module's, and this writes nothing to
        the module, so a caller's key is not left there for another call to find.

        ``stream`` is decided by the call (``_stream`` and ``_astream`` set it); the
        parent takes it from the ``streaming`` field, which sent ``stream: true`` with a
        call that then read a whole reply.

        Raises:
            ValueError: If the model has no API key. It would otherwise fall back to
                whatever key the process happens to hold.
        """
        if not self.api_key:
            raise ValueError("TaggedCitationChatLiteLLM needs an api_key; none was given")
        params: dict[str, Any] = {
            **self._default_params,
            "model": self.model_name or self.model,
            "force_timeout": self.request_timeout,
            "api_base": self.api_base,
            "api_key": self.api_key,
            "stream": False,
        }
        if self.extra_headers is not None:
            params["extra_headers"] = self.extra_headers
        return params

    def _create_message_dicts(
        self, messages: list[BaseMessage], stop: list[str] | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # The parent builds the request parameters and enforces its `stop` rule; its
        # message conversion is replaced, so it is given nothing to convert.
        _, params = super()._create_message_dicts([], stop)
        cache = self.prompt_caching and takes_cache_markers(self.model_name or self.model)
        return to_message_dicts(messages, cache=cache), params

    def completion_with_retry(
        self, run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> Any:
        response = super().completion_with_retry(run_manager=run_manager, **kwargs)
        if not kwargs.get("stream"):
            return response
        tap = _tap_provider_usage(response)

        def carried() -> Iterator[Any]:
            for raw in response:
                yield _carry_usage_details(raw, tap)
            if (closing := _closing_chunk(tap)) is not None:
                yield closing

        return carried()

    async def acompletion_with_retry(
        self, run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> Any:
        response = await super().acompletion_with_retry(run_manager=run_manager, **kwargs)
        if not kwargs.get("stream"):
            return response
        tap = _tap_provider_usage(response)

        async def carried() -> AsyncIterator[Any]:
            async for raw in response:
                yield _carry_usage_details(raw, tap)
            if (closing := _closing_chunk(tap)) is not None:
                yield closing

        return carried()

    def _create_chat_result(self, response: Mapping[str, Any]) -> ChatResult:
        result = super()._create_chat_result(response)
        details = usage_details(response.get("usage"))
        if details:
            for generation in result.generations:
                if isinstance(generation.message, AIMessage):
                    _apply_usage_details(generation.message, details)
        return result

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        stream: bool | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate a complete reply, with its tags turned into citations."""
        if self.streaming if stream is None else stream:
            # The parent assembles the reply from `self._stream`, which tags.
            return super()._generate(messages, stop, run_manager, stream=True, **kwargs)
        prepared, registry = prepare_messages(messages)
        result = super()._generate(prepared, stop, run_manager, stream=False, **kwargs)
        for generation in result.generations:
            generation.message.content = rewrite_content(generation.message.content, registry)
        return result

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        stream: bool | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async ``_generate``."""
        if self.streaming if stream is None else stream:
            return await super()._agenerate(messages, stop, run_manager, stream=True, **kwargs)
        prepared, registry = prepare_messages(messages)
        result = await super()._agenerate(prepared, stop, run_manager, stream=False, **kwargs)
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
        prepared, registry = prepare_messages(messages)
        retagger = ChunkRetagger(registry)

        # The parent reports each token to the run manager as it arrives, tags and
        # all; withheld here, and reported below with the tags already cut out.
        for chunk in super()._stream(prepared, stop, None, **kwargs):
            _take_carried_fields(chunk)
            retagged = retagger.feed(chunk)
            if run_manager:
                run_manager.on_llm_new_token(retagged.message.text, chunk=retagged)
            yield retagged

        for held in retagger.finish():
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
        """Async ``_stream``."""
        prepared, registry = prepare_messages(messages)
        retagger = ChunkRetagger(registry)

        async for chunk in super()._astream(prepared, stop, None, **kwargs):
            _take_carried_fields(chunk)
            retagged = retagger.feed(chunk)
            if run_manager:
                await run_manager.on_llm_new_token(retagged.message.text, chunk=retagged)
            yield retagged

        for held in retagger.finish():
            if run_manager:
                await run_manager.on_llm_new_token(held.message.text, chunk=held)
            yield held
