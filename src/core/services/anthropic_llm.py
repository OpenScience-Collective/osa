"""Anthropic Claude LLM integration for the Claude Platform on AWS.

This module targets the Claude Platform on AWS: an Anthropic-operated
Messages API billed through AWS Marketplace, NOT Amazon Bedrock. Server-mode
requests read three environment variables here through
:class:`~src.api.config.Settings`:

    ANTHROPIC_API_KEY       long-lived key from AWS Console -> Claude Platform
    ANTHROPIC_BASE_URL      the AWS-hosted Messages API endpoint
    ANTHROPIC_WORKSPACE_ID  workspace the key is authorized on

Only ``ANTHROPIC_API_KEY`` is strictly required. ``ANTHROPIC_BASE_URL`` and
``ANTHROPIC_WORKSPACE_ID`` are each optional on their own (omitting both
falls back to the first-party client default), but a base URL without a
workspace id is rejected at construction time: the AWS endpoint requires the
``anthropic-workspace-id`` header, so that combination would otherwise
construct fine and only fail later with an opaque 4xx from AWS.

A caller-supplied (BYOK) Anthropic key is not authorized on that AWS
workspace, so BYOK requests go to the first-party API (api.anthropic.com)
instead; see :func:`create_anthropic_llm` for how the two modes differ.

This started as Phase 1 of the Anthropic provider layer (issue #361), adding
the provider without changing request routing. Phases 2-4 have since wired
it in: ``create_anthropic_llm`` is called from
``src.api.routers.community`` and ``src.knowledge.faq_summarizer``, and it
is the default path for platform/community-funded requests.
"""

import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import ConfigDict, field_validator

from src.api.config import Settings, get_settings
from src.core.services.anthropic_endpoints import FIRST_PARTY_BASE_URL

# The model tables live in a langchain-free module so config validation can
# reach them on a CLI-only install (see anthropic_models.py). DEFAULT_MODEL,
# MODEL_ALIASES and OFFERED_MODELS are re-exported here because server-side
# callers have imported them from this module all along.
from src.core.services.anthropic_models import (
    BEDROCK_MODEL_PROVIDER,
    BEDROCK_MODELS,  # noqa: F401
    DEFAULT_MODEL,  # noqa: F401
    MODEL_ALIASES,  # noqa: F401
    OFFERED_MODELS,  # noqa: F401
    SAMPLING_MODELS,
    THINKING_OFF,
    effective_reasoning_effort,
    is_bedrock_model,
    normalize_model,
)

logger = logging.getLogger(__name__)

# Thinking policy. Every offered Claude model thinks adaptively, steered by an effort
# level, and takes no token budget (``thinking.type "enabled"`` with ``budget_tokens`` is a
# 400 on the Claude 5 generation). They differ in how up-front thinking is turned off, and
# the API is strict about it (a mismatch is a 400 at request time, not a graceful
# fallback): Sonnet 5.5 takes {"type": "between_tools"} and rejects "disabled", Haiku 5.5
# takes {"type": "disabled"}. THINKING_OFF (anthropic_models.py) has each model's value.
# Sonnet 5.5 does no extended thinking under its value, though the short progress notes it
# writes between tool calls still arrive as `thinking` blocks. The API allows an off value
# only at effort "high" or below and with no other field inside `thinking`. OSA always
# sends an effort with it (the community's level, else "high"; its `none` sends "low").


# Prompt-cache lifetimes. A 5-minute entry costs 1.25x the input price to
# write, a 1-hour entry 2x; both read back at 0.1x. Back-to-back requests
# break even on the 5-minute entry after two calls; traffic spaced further
# apart never reads a 5-minute entry back and pays the write premium every
# time, which is when "1h" is worth the higher write cost.
CACHE_TTLS = ("5m", "1h")
DEFAULT_CACHE_TTL = "5m"


def default_thinking(model: str | None = None) -> dict[str, Any]:
    """Return the default thinking configuration for a model: its default reasoning level.

    Args:
        model: Model identifier (normalized internally); the default model
            when None.

    Returns:
        Adaptive thinking: the only "on" mode of the Claude models offered, which decide
        how much to think themselves. To turn thinking off, pass ``thinking=None`` to
        :func:`create_anthropic_llm`, which sends the model's own off value
        (``THINKING_OFF``); an omitted key means adaptive thinking.

    Raises:
        ValueError: If the model is not a Claude model with adaptive thinking.
    """
    resolved_model = normalize_model(model)
    if resolved_model not in THINKING_OFF:
        raise ValueError(f"{resolved_model} is not a Claude model with adaptive thinking")
    return {"type": "adaptive"}


def _validate_thinking(thinking: dict[str, Any], model: str) -> None:
    """Check a thinking configuration against what the model accepts.

    The API enforces a different off value per model, and a mismatch is a 400 at request
    time: claude-sonnet-5-5 rejects thinking.type "disabled" (it takes "between_tools"),
    and claude-haiku-5-5 has no "between_tools". Neither accepts a token budget.

    Args:
        thinking: Thinking configuration to check.
        model: Resolved first-party model id.

    Raises:
        ValueError: If the configuration is not valid for this model.
    """
    kind = thinking.get("type")
    off = THINKING_OFF.get(model)
    if off is None:
        raise ValueError(f"{model} is not a Claude model with adaptive thinking")

    if kind not in ("adaptive", off["type"]):
        raise ValueError(
            f"{model} accepts thinking type 'adaptive' or {off['type']!r}, not {kind!r}; "
            "budget_tokens was removed on this model generation (pass thinking=None to "
            "create_anthropic_llm to turn thinking off)"
        )
    if kind == off["type"] and set(thinking) != {"type"}:
        raise ValueError(
            f"thinking type {kind!r} takes no other field, got {sorted(set(thinking) - {'type'})}"
        )


def strip_bedrock_turns(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Make the assistant turns a Bedrock model wrote acceptable to Claude.

    A chat can switch models between requests, and the history holds whatever the
    earlier model produced. Sent to Anthropic as it is, a Bedrock turn's
    ``reasoning_content`` block is an unknown block type (a 400), and its
    ``citations`` are indexed against tagged sources rather than Anthropic's own
    search results. Only the text and the tool calls are kept; the citations'
    markers were already turned into ``[n]`` in the visible answer.

    The caller's messages are not changed.
    """
    cleaned: list[BaseMessage] = []
    for message in messages:
        if (
            isinstance(message, AIMessage)
            and isinstance(message.content, list)
            and message.response_metadata.get("model_provider") == BEDROCK_MODEL_PROVIDER
        ):
            kept: list[Any] = []
            for block in message.content:
                if not isinstance(block, dict) or block.get("type") == "tool_use":
                    kept.append(block)
                elif block.get("type") == "text":
                    kept.append({k: v for k, v in block.items() if k != "citations"})
            message = message.model_copy(update={"content": kept})
        cleaned.append(message)
    return cleaned


class _Default:
    """Sentinel distinguishing "use the per-model default" from explicit None.

    ``create_anthropic_llm``'s ``thinking`` parameter needs three states: "I
    did not ask for anything, pick the model's default", "I explicitly want
    thinking off", and "here is a specific configuration". ``None`` can only
    mean one of the first two, so a distinct sentinel is needed for the
    default case.
    """

    def __repr__(self) -> str:
        return "<DEFAULT>"


_DEFAULT: Any = _Default()


def create_anthropic_llm(
    model: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    thinking: dict[str, Any] | None | _Default = _DEFAULT,
    enable_caching: bool = True,
    cache_ttl: str | None = None,
    timeout: float = 60.0,
    settings: Settings | None = None,
    reasoning_effort: str | None = None,
) -> BaseChatModel:
    """Create a Claude LLM instance for the Claude Platform on AWS.

    Args:
        model: Model identifier or class name (default: the ``haiku`` class). Accepts
            the ids a class used to be and OpenRouter-style ids via ``MODEL_ALIASES``.
        api_key: BYOK Anthropic API key. When provided, requests go to the
            first-party API (api.anthropic.com) and no workspace header is
            sent. When None (server mode), the key, base URL, and workspace
            id are read from ``settings``.
        temperature: Sampling temperature. Only forwarded for models that
            still accept sampling params (``SAMPLING_MODELS``) and only when
            thinking is off; ignored otherwise. No offered Claude model takes one.
        max_tokens: Maximum tokens to generate. Defaults to
            ``settings.anthropic_max_output_tokens``.
        thinking: Explicit extended-thinking configuration. Leave unset to
            get adaptive thinking from :func:`default_thinking`; pass ``None``
            explicitly to turn thinking off, which sends the model's own off value
            (``THINKING_OFF``: ``{"type": "between_tools"}`` on Sonnet 5.5,
            ``{"type": "disabled"}`` on Haiku 5.5); pass a dict to fully control it.
            The accepted shape depends on the model (see :func:`_validate_thinking`).
        enable_caching: Return a :class:`CachingChatAnthropic` that applies
            prompt-cache breakpoints (default True).
        cache_ttl: Prompt-cache lifetime, "5m" or "1h". Defaults to
            ``settings.anthropic_cache_ttl``.
        timeout: Per-request timeout in seconds.
        settings: Settings instance to read server-mode credentials and
            defaults from. Defaults to ``get_settings()``.
        reasoning_effort: The community's level from the neutral scale, or None for
            ``DEFAULT_REASONING_EFFORT`` (high). The Claude models are sent a level they
            accept, never above ``high``: ``low``, ``medium`` and ``high`` set
            ``output_config.effort`` to that level, and ``none`` (no level is lower than
            ``low``) sets it to ``low`` and turns up-front thinking off (the model's
            ``THINKING_OFF`` value). An explicit ``thinking`` argument still wins over the
            thinking this implies.

    Returns:
        A :class:`CachingChatAnthropic` (default) or plain ``ChatAnthropic``
        instance configured for the Claude Messages API.

    Raises:
        ValueError: If the model is not offered, is served from Amazon Bedrock
            rather than by Anthropic (see ``create_bedrock_llm``), the cache
            TTL is not supported, or the thinking configuration is not valid
            for the model.
        RuntimeError: If server mode is used without ANTHROPIC_API_KEY set,
            or if ANTHROPIC_BASE_URL is set without ANTHROPIC_WORKSPACE_ID.
    """
    resolved_settings = settings or get_settings()
    resolved_model = normalize_model(model)
    if is_bedrock_model(resolved_model):
        # Sending "openai.gpt-6-luna" to the Messages API would fail as an opaque
        # unknown-model error, far from the caller that picked the wrong factory.
        raise ValueError(
            f"{resolved_model} is served from Amazon Bedrock, not by Anthropic; "
            "build it with create_bedrock_llm"
        )

    resolved_max_tokens = (
        max_tokens if max_tokens is not None else resolved_settings.anthropic_max_output_tokens
    )

    resolved_ttl = cache_ttl or resolved_settings.anthropic_cache_ttl
    if resolved_ttl not in CACHE_TTLS:
        raise ValueError(
            f"Unsupported prompt cache TTL {resolved_ttl!r}. Supported: {', '.join(CACHE_TTLS)}"
        )

    if isinstance(thinking, _Default):
        resolved_thinking = default_thinking(resolved_model)
    elif thinking is None:
        # An omitted `thinking` key is not "off": the API's own default is adaptive
        # thinking turned on. Send the model's own off value so a caller's `None`
        # actually means no extended thinking.
        resolved_thinking = dict(THINKING_OFF[resolved_model])
    else:
        resolved_thinking = thinking

    # The reasoning level (issues #545 and #548). It goes in the typed `output_config`
    # field, not the adapter's `reasoning_effort`, which can force adaptive thinking on
    # with display settings. Thinking stays what the caller or the default made it
    # (adaptive, which is also what an omitted key means), except for `none`: there is no
    # level below `low`, so it is no up-front thinking at the lowest effort, unless the
    # caller chose the thinking.
    effort_level = effective_reasoning_effort(resolved_model, reasoning_effort, "anthropic")
    output_config: dict[str, Any] | None = None
    if effort_level is not None:
        if effort_level == "none":
            output_config = {"effort": "low"}
            if isinstance(thinking, _Default):
                resolved_thinking = dict(THINKING_OFF[resolved_model])
        else:
            output_config = {"effort": effort_level}

    if resolved_thinking is not None:
        _validate_thinking(resolved_thinking, resolved_model)
        if (
            output_config is not None
            and resolved_thinking.get("type") == THINKING_OFF[resolved_model]["type"]
            and output_config["effort"] in ("xhigh", "max")
        ):
            # The API refuses this pairing (a 400: "not supported when thinking is
            # disabled"). The level table caps the Claude models at `high`, so it cannot
            # arise today; this fails at construction, not at the endpoint, if the table
            # is ever widened.
            raise ValueError(
                f"thinking type {resolved_thinking['type']!r} is accepted only at effort "
                f"'high' or below, got {output_config['effort']!r}"
            )

    thinking_on = resolved_thinking is not None and resolved_thinking.get("type") not in (
        "disabled",
        "between_tools",
    )

    kwargs: dict[str, Any] = {}
    if api_key:
        # BYOK: first-party endpoint, no workspace header. The base URL must
        # be pinned explicitly here: ChatAnthropic otherwise falls back to
        # the process-wide ANTHROPIC_BASE_URL env var, which in server mode
        # points at the AWS endpoint that rejects a first-party key sent
        # without the workspace header.
        kwargs["api_key"] = api_key
        kwargs["base_url"] = FIRST_PARTY_BASE_URL
    else:
        server_key = resolved_settings.anthropic_api_key
        if not server_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set (server mode requires it)")
        if resolved_settings.anthropic_base_url and not resolved_settings.anthropic_workspace_id:
            # A half-configured server mode would otherwise construct fine
            # and only fail later with an opaque 4xx from AWS: the Claude
            # Platform on AWS endpoint rejects requests that lack the
            # anthropic-workspace-id header.
            raise RuntimeError(
                "ANTHROPIC_BASE_URL is set but ANTHROPIC_WORKSPACE_ID is not; the "
                "Claude Platform on AWS endpoint rejects requests without the "
                "anthropic-workspace-id header"
            )
        kwargs["api_key"] = server_key
        if resolved_settings.anthropic_base_url:
            kwargs["base_url"] = resolved_settings.anthropic_base_url
        if resolved_settings.anthropic_workspace_id:
            kwargs["default_headers"] = {
                "anthropic-workspace-id": resolved_settings.anthropic_workspace_id
            }

    # The Claude 5 generation (Sonnet 5.5, Haiku 5.5) rejects any non-default
    # temperature/top_p/top_k with a 400 unconditionally, whether or not thinking is on,
    # which is why they are not in SAMPLING_MODELS. A model that does accept temperature
    # still does not while extended thinking is on, so it is only forwarded for models in
    # SAMPLING_MODELS, and only when thinking is off.
    if temperature is not None:
        if resolved_model in SAMPLING_MODELS and not thinking_on:
            kwargs["temperature"] = temperature
        else:
            # Debug rather than warning: this fires per request, and the two
            # cases it covers are both known ahead of time. A community that
            # pairs a temperature with a Claude 5 model in config.yaml is warned
            # once at config load (FAQGenerationConfig.validate_agent_roles),
            # and thinking-plus-temperature is a documented API constraint.
            logger.debug(
                "Dropping temperature=%s for %s: %s",
                temperature,
                resolved_model,
                "extended thinking is on"
                if resolved_model in SAMPLING_MODELS
                else "the model only accepts its default temperature",
            )

    if resolved_thinking is not None:
        kwargs["thinking"] = resolved_thinking
    if output_config is not None:
        kwargs["output_config"] = output_config

    common_kwargs: dict[str, Any] = {
        "model": resolved_model,
        "max_tokens": resolved_max_tokens,
        "timeout": timeout,
        # src/api/routers/community.py streams via graph.astream_events(...),
        # which needs on_chat_model_stream events; those only fire when the
        # underlying model streams.
        "streaming": True,
        **kwargs,
    }

    if enable_caching:
        return CachingChatAnthropic(cache_ttl=resolved_ttl, **common_kwargs)
    return ChatAnthropic(**common_kwargs)


class CachingChatAnthropic(ChatAnthropic):
    """``ChatAnthropic`` subclass that applies prompt-cache breakpoints.

    Why a subclass and not a wrapper: a ``BaseChatModel`` wrapper (the shape the
    old LiteLLM integration used, since replaced by a subclass in ``litellm_chat``
    for the same reasons) has to reimplement ``invoke``/``ainvoke``/``stream``/
    ``astream``/``_generate``/``_agenerate``/``bind_tools`` to forward to the wrapped
    model, and that ``_generate`` breaks once ``bind_tools`` returns a
    ``RunnableBinding`` around the wrapped model instead of another wrapper
    instance (the wrapper's own ``_generate`` would need to special-case a
    ``Runnable`` that is not a ``BaseChatModel``). A subclass sidesteps all
    of that: ``bind_tools`` (inherited from ``ChatAnthropic``) calls
    ``self.bind(...)``, producing a ``RunnableBinding`` around *this*
    instance, so tool binding, streaming, ``astream_events``, and usage
    metadata all stay native. Only one private method needs overriding,
    because every public entry point (``_generate``, ``_agenerate``,
    ``_stream``, ``_astream``) funnels through it.

    This does mean the override depends on the shape of one private method
    of langchain-anthropic (``_get_request_payload``); an upstream change to
    that method's signature or behavior will not raise here, so
    ``tests/test_core/test_anthropic_llm.py`` asserts the payload shape
    directly, to fail loudly on an incompatible upgrade instead of silently
    caching nothing.

    Breakpoint budget: Anthropic allows at most 4 ``cache_control`` markers
    per request. This override adds at most 2 per call (one on the system
    block, one on the trailing message block; the parent stops after the
    first eligible message block it finds), and markers never carry forward
    across turns because ``_format_messages`` rebuilds every block dict from
    scratch on each call. That leaves headroom of at least 2 markers per
    request for Phase 2 to place its own breakpoints.
    """

    model_config = ConfigDict(validate_assignment=True)

    cache_ttl: str = DEFAULT_CACHE_TTL
    """Prompt-cache lifetime for cache_control markers ("5m" or "1h")."""

    @field_validator("cache_ttl")
    @classmethod
    def _check_cache_ttl(cls, value: str) -> str:
        """Enforce the supported TTL set on the class itself.

        ``create_anthropic_llm`` already checks this, but that check is
        bypassed by constructing ``CachingChatAnthropic`` directly, so the
        invariant is duplicated here on the field to travel with the type.
        """
        if value not in CACHE_TTLS:
            raise ValueError(
                f"Unsupported prompt cache TTL {value!r}. Supported: {', '.join(CACHE_TTLS)}"
            )
        return value

    def _cache_control_marker(self) -> dict[str, str]:
        """Build the cache_control dict for this instance's TTL."""
        marker: dict[str, str] = {"type": "ephemeral"}
        if self.cache_ttl != DEFAULT_CACHE_TTL:
            marker["ttl"] = self.cache_ttl
        return marker

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Build the request payload with cache_control breakpoints applied.

        Against the direct Anthropic API this module uses, ``ChatAnthropic.
        _get_request_payload`` forwards a caller-supplied ``cache_control``
        kwarg as a top-level request parameter, and the Anthropic server
        (not langchain) attaches the breakpoint to the last cacheable block
        (see ``_conversation_cache_control_landed`` below for the other
        transport's block-level shape). Defaulting the kwarg here means the
        trailing breakpoint lands there automatically, so the conversation
        prefix (system + history) stays cached across agentic tool-call
        iterations without every caller having to pass it explicitly.

        The parent method does not touch ``payload["system"]`` with that
        kwarg (cache_control is only walked onto ``formatted_messages``), so
        the system prompt's own breakpoint is added here after the parent
        call returns.
        """
        cache_marker = self._cache_control_marker()
        kwargs.setdefault("cache_control", cache_marker)
        input_ = strip_bedrock_turns(self._convert_input(input_).to_messages())
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        if not self._conversation_cache_control_landed(payload):
            # Caching can fail quietly in two ways, and both are permanent,
            # invisible cost leaks rather than errors: on transports that
            # expand the kwarg into block-level markers the parent drops it
            # when no eligible block exists (its own source comment says so),
            # and any future release that stops honoring the kwarg entirely
            # would look identical. Make it visible instead.
            logger.warning(
                "Prompt cache breakpoint did not land for this request, neither "
                "as a top-level cache_control parameter nor on any message "
                "block, so this call will not benefit from conversation prompt "
                "caching."
            )

        system = payload.get("system")
        if isinstance(system, str) and system:
            payload["system"] = [{"type": "text", "text": system, "cache_control": cache_marker}]
        elif isinstance(system, list) and system:
            already_cached = any(
                isinstance(block, dict) and "cache_control" in block for block in system
            )
            if not already_cached:
                if isinstance(system[-1], dict):
                    # Build a new list/dict instead of mutating system[-1] in
                    # place: _format_messages reuses the caller's own dict
                    # objects for list-content system messages, so an
                    # in-place assignment here would mutate the caller's
                    # SystemMessage and leak this instance's TTL into a
                    # later request built from the same message object.
                    payload["system"] = [
                        *system[:-1],
                        {**system[-1], "cache_control": cache_marker},
                    ]
                else:
                    # Defensive: _format_messages always normalizes list
                    # system content to dicts today, but guard against a
                    # future change that stops doing so.
                    logger.warning(
                        "Could not place the system prompt cache breakpoint: "
                        "the last system content block is a %s, not a dict.",
                        type(system[-1]).__name__,
                    )

        return payload

    @staticmethod
    def _conversation_cache_control_landed(payload: dict) -> bool:
        """Check whether the conversation cache breakpoint was requested.

        Two shapes count, because langchain-anthropic places the breakpoint
        differently depending on the transport. Against the direct Anthropic
        API it forwards `cache_control` as a top-level request parameter and
        lets the API attach the breakpoint to the last cacheable block; on
        transports that do not accept that parameter (Bedrock, for example)
        it expands the kwarg into a block-level marker instead.
        """
        if payload.get("cache_control"):
            return True
        for message in payload.get("messages", []):
            content = message.get("content")
            if isinstance(content, list) and any(
                isinstance(block, dict) and "cache_control" in block for block in content
            ):
                return True
        return False
