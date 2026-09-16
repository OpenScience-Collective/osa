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

This is Phase 1 of the Anthropic provider layer (issue #361): it adds the
provider without changing request routing. ``src/api/config.py``'s
``default_model`` / ``default_model_provider`` are untouched here; wiring
this module into the agents and routers is Phase 2.
"""

import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from pydantic import field_validator

from src.api.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Default (and cheapest) offered model.
DEFAULT_MODEL = "claude-haiku-4-5"

# Models offered to callers (widget dropdown, CLI, community config.yaml).
OFFERED_MODELS: dict[str, str] = {
    "claude-haiku-4-5": "Claude Haiku 4.5 (default)",
    "claude-sonnet-5": "Claude Sonnet 5",
}

# Legacy OpenRouter-style identifiers that exist in saved widget settings,
# CLI configs, and community config.yaml files, normalized to first-party ids.
MODEL_ALIASES: dict[str, str] = {
    "anthropic/claude-haiku-4.5": "claude-haiku-4-5",
    "anthropic/claude-haiku-4-5": "claude-haiku-4-5",
    "claude-haiku-4.5": "claude-haiku-4-5",
    "anthropic/claude-sonnet-5": "claude-sonnet-5",
    "anthropic/claude-sonnet-4.6": "claude-sonnet-5",
    "anthropic/claude-sonnet-4.5": "claude-sonnet-5",
    "claude-sonnet-4.5": "claude-sonnet-5",
}

# Thinking policy. The two offered model generations accept different, mutually
# exclusive `thinking` shapes and the API is strict about it (a mismatch is a
# 400 at request time, not a graceful fallback):
#   - claude-sonnet-5 has no budget-style thinking; it only accepts
#     {"type": "adaptive"} or {"type": "disabled"}.
#   - claude-haiku-4-5 has no adaptive mode; it needs an explicit
#     {"type": "enabled", "budget_tokens": N} to think at all.
_ADAPTIVE_THINKING_MODELS = {"claude-sonnet-5"}

# Smallest thinking budget the API accepts on budget-style (Haiku) models.
MIN_THINKING_BUDGET_TOKENS = 1024

# Fallback default budget when neither a caller nor settings supplies one.
# Mirrors Settings.anthropic_thinking_budget_tokens's own default so the two
# stay in sync without importing Settings just for a literal.
DEFAULT_THINKING_BUDGET_TOKENS = 2048

# Models that still accept sampling parameters. Claude 5-generation models
# (claude-sonnet-5) reject `temperature` with a 400 because the only value
# they accept is 1, the implicit default when the field is simply omitted.
_SAMPLING_MODELS = {"claude-haiku-4-5"}

# Prompt-cache lifetimes. A 5-minute entry costs 1.25x the input price to
# write, a 1-hour entry 2x; both read back at 0.1x. Back-to-back requests
# break even on the 5-minute entry after two calls; traffic spaced further
# apart never reads a 5-minute entry back and pays the write premium every
# time, which is when "1h" is worth the higher write cost.
CACHE_TTLS = ("5m", "1h")
DEFAULT_CACHE_TTL = "5m"


def normalize_model(model: str | None) -> str:
    """Normalize a requested model id to an offered Anthropic model.

    Args:
        model: Requested model identifier (first-party id, legacy
            OpenRouter-style id, or None for the default).

    Returns:
        A first-party Anthropic model id present in ``OFFERED_MODELS``.

    Raises:
        ValueError: If the model is not an offered Anthropic model.
    """
    if not model:
        return DEFAULT_MODEL
    resolved = MODEL_ALIASES.get(model, model)
    if resolved not in OFFERED_MODELS:
        offered = ", ".join(sorted(OFFERED_MODELS))
        raise ValueError(f"Model '{model}' is not available. Offered models: {offered}")
    return resolved


def default_thinking(model: str | None = None, budget: int | None = None) -> dict[str, Any] | None:
    """Return the default thinking configuration for a model.

    Args:
        model: Model identifier (normalized internally); the default model
            when None.
        budget: Thinking budget in tokens for budget-style (Haiku) models.
            Falls back to ``DEFAULT_THINKING_BUDGET_TOKENS`` when None. A
            budget of 0 or negative disables thinking for any model by
            returning None, which omits the ``thinking`` key from the
            request. Note that omission is only "off" for budget-style
            models: on adaptive-default models (claude-sonnet-5) an omitted
            key means the API's own default, which is adaptive thinking
            turned on. To actually disable thinking on those models, pass
            ``thinking=None`` to :func:`create_anthropic_llm` instead, which
            sends ``{"type": "disabled"}`` explicitly.

    Returns:
        A thinking configuration dict for the resolved model, or None when
        thinking should be disabled.
    """
    resolved_model = normalize_model(model)
    resolved_budget = DEFAULT_THINKING_BUDGET_TOKENS if budget is None else budget
    if resolved_budget <= 0:
        return None
    if resolved_model in _ADAPTIVE_THINKING_MODELS:
        # Adaptive is the only "on" mode on this generation; the model
        # decides how much to think, so the budget value does not apply.
        return {"type": "adaptive"}
    return {"type": "enabled", "budget_tokens": resolved_budget}


def _validate_thinking(thinking: dict[str, Any], model: str, max_tokens: int) -> None:
    """Check a thinking configuration against what the model accepts.

    The API enforces different shapes per model generation, and a mismatch
    is a 400 at request time: claude-sonnet-5 rejects thinking.type
    "enabled" ("Use thinking.type adaptive"), while claude-haiku-4-5 has no
    adaptive mode and needs an explicit token budget.

    Args:
        thinking: Thinking configuration to check.
        model: Resolved first-party model id.
        max_tokens: Maximum tokens for the request.

    Raises:
        ValueError: If the configuration is not valid for this model.
    """
    kind = thinking.get("type")

    if model in _ADAPTIVE_THINKING_MODELS:
        if kind not in ("adaptive", "disabled"):
            raise ValueError(
                f"{model} accepts thinking type 'adaptive' or 'disabled', not {kind!r}; "
                "budget_tokens was removed on this model generation"
            )
        return

    if kind == "disabled":
        # Accepted and redundant on this model, but it lets a caller express
        # "off" the same way for every model instead of special-casing.
        return

    if kind != "enabled":
        raise ValueError(
            f"{model} has no adaptive thinking mode; enable it with "
            '{"type": "enabled", "budget_tokens": N}, disable it with '
            f'{{"type": "disabled"}}, or leave thinking unset, not {kind!r}'
        )

    budget = thinking.get("budget_tokens")
    if not isinstance(budget, int) or budget < MIN_THINKING_BUDGET_TOKENS:
        raise ValueError(
            f"budget_tokens must be an integer of at least "
            f"{MIN_THINKING_BUDGET_TOKENS}, got {budget!r}"
        )
    if budget >= max_tokens:
        raise ValueError(
            f"budget_tokens ({budget}) must be below max_tokens ({max_tokens}); "
            "thinking tokens are drawn from the same budget as the response"
        )


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
) -> BaseChatModel:
    """Create a Claude LLM instance for the Claude Platform on AWS.

    Args:
        model: Model identifier (default: claude-haiku-4-5). Accepts legacy
            OpenRouter-style ids via ``MODEL_ALIASES``.
        api_key: BYOK Anthropic API key. When provided, requests go to the
            first-party API (api.anthropic.com) and no workspace header is
            sent. When None (server mode), the key, base URL, and workspace
            id are read from ``settings``.
        temperature: Sampling temperature. Only forwarded for models that
            still accept sampling params (claude-haiku-4-5) and only when
            thinking is off; ignored otherwise.
        max_tokens: Maximum tokens to generate. Defaults to
            ``settings.anthropic_max_output_tokens``.
        thinking: Explicit extended-thinking configuration. Leave unset to
            get the per-model default from :func:`default_thinking` (on
            adaptive-default models such as claude-sonnet-5, that default is
            adaptive thinking turned on); pass ``None`` explicitly to
            disable thinking entirely, which sends ``{"type": "disabled"}``
            on adaptive-default models and omits the ``thinking`` key on
            budget-style models (e.g. claude-haiku-4-5), where an omitted
            key already means no thinking; pass a dict to fully control it.
            The accepted shape depends on the model (see
            :func:`_validate_thinking`).
        enable_caching: Return a :class:`CachingChatAnthropic` that applies
            prompt-cache breakpoints (default True).
        cache_ttl: Prompt-cache lifetime, "5m" or "1h". Defaults to
            ``settings.anthropic_cache_ttl``.
        timeout: Per-request timeout in seconds.
        settings: Settings instance to read server-mode credentials and
            defaults from. Defaults to ``get_settings()``.

    Returns:
        A :class:`CachingChatAnthropic` (default) or plain ``ChatAnthropic``
        instance configured for the Claude Messages API.

    Raises:
        ValueError: If the model is not offered, the cache TTL is not
            supported, or the thinking configuration is not valid for the
            model.
        RuntimeError: If server mode is used without ANTHROPIC_API_KEY set,
            or if ANTHROPIC_BASE_URL is set without ANTHROPIC_WORKSPACE_ID.
    """
    resolved_settings = settings or get_settings()
    resolved_model = normalize_model(model)

    resolved_max_tokens = (
        max_tokens if max_tokens is not None else resolved_settings.anthropic_max_output_tokens
    )

    resolved_ttl = cache_ttl or resolved_settings.anthropic_cache_ttl
    if resolved_ttl not in CACHE_TTLS:
        raise ValueError(
            f"Unsupported prompt cache TTL {resolved_ttl!r}. Supported: {', '.join(CACHE_TTLS)}"
        )

    if isinstance(thinking, _Default):
        resolved_thinking = default_thinking(
            resolved_model, resolved_settings.anthropic_thinking_budget_tokens
        )
    elif thinking is None:
        # An omitted `thinking` key is not "off" on adaptive-default models:
        # the API's own default there is adaptive thinking turned on. Send
        # an explicit disable so a caller's `None` actually means no
        # thinking. Budget-style models already treat an omitted key as
        # off, so leave the key omitted there instead of adding it.
        resolved_thinking = (
            {"type": "disabled"} if resolved_model in _ADAPTIVE_THINKING_MODELS else None
        )
    else:
        resolved_thinking = thinking

    if resolved_thinking is not None:
        _validate_thinking(resolved_thinking, resolved_model, resolved_max_tokens)

    thinking_on = resolved_thinking is not None and resolved_thinking.get("type") != "disabled"

    kwargs: dict[str, Any] = {}
    if api_key:
        # BYOK: first-party endpoint, no workspace header. The base URL must
        # be pinned explicitly here: ChatAnthropic otherwise falls back to
        # the process-wide ANTHROPIC_BASE_URL env var, which in server mode
        # points at the AWS endpoint that rejects a first-party key sent
        # without the workspace header.
        kwargs["api_key"] = api_key
        kwargs["base_url"] = "https://api.anthropic.com"
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

    # claude-sonnet-5 rejects any non-default temperature/top_p/top_k with a
    # 400 unconditionally, whether or not thinking is on, which is why it is
    # not in _SAMPLING_MODELS. claude-haiku-4-5 does accept temperature, but
    # not while extended thinking is on, so it is only forwarded for models
    # in _SAMPLING_MODELS, and only when thinking is off.
    if resolved_model in _SAMPLING_MODELS and not thinking_on and temperature is not None:
        kwargs["temperature"] = temperature

    if resolved_thinking is not None:
        kwargs["thinking"] = resolved_thinking

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

    Why a subclass and not a wrapper: a ``BaseChatModel`` wrapper (the shape
    the old LiteLLM integration used, see ``litellm_llm.CachingLLMWrapper``)
    has to reimplement ``invoke``/``ainvoke``/``stream``/``astream``/
    ``_generate``/``_agenerate``/``bind_tools`` to forward to the wrapped
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

        ``ChatAnthropic._get_request_payload`` already applies a caller-
        supplied ``cache_control`` kwarg to the last eligible message block
        (see langchain_anthropic.chat_models); defaulting it here means the
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
