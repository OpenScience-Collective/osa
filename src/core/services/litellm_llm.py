"""LiteLLM integration for OpenRouter with prompt caching support.

This module provides LLM access through LiteLLM, which natively supports
Anthropic's prompt caching via the cache_control parameter. This reduces
costs by up to 90% for repeated prompts with large static content.

Default model: GPT-OSS-120B via Cerebras (fast inference with reliable tool calling)

Usage:
    from src.core.services.litellm_llm import create_openrouter_llm

    # Create LLM with default model via Cerebras
    llm = create_openrouter_llm(
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )

    # Or specify a different model
    llm = create_openrouter_llm(
        model="anthropic/claude-haiku-4.5",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        enable_caching=True,  # For Anthropic prompt caching
    )

    # Use with LangChain messages
    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query),
    ])
"""

import logging
import os
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.core.services.anthropic_models import effective_reasoning_effort

logger = logging.getLogger(__name__)

# Default OpenRouter model/provider for callers that name neither. This is
# the factory's own fallback, not a platform policy: since Phase 2 the
# platform default is an Anthropic model, and a request funded by an
# OpenRouter key resolves it through OPENROUTER_MODEL_IDS below.
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_PROVIDER = "Cerebras"

# OpenRouter slugs for the models OSA offers. OpenRouter serves the same
# Claude models under creator/model-name slugs, so a request funded by an
# OpenRouter key (BYOK, or a community's own funded key) still runs the
# community's chosen model rather than switching to a different model family
# just because of which key paid for it. Bare first-party ids such as
# "claude-haiku-4-5" are not valid OpenRouter slugs, hence the mapping.
# tests/test_core/test_litellm_llm.py asserts these keys stay in step with
# anthropic_llm.OFFERED_MODELS so adding a model cannot silently skip this.
OPENROUTER_MODEL_IDS: dict[str, str] = {
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    "claude-sonnet-5-5": "anthropic/claude-sonnet-5.5",
    # The Bedrock-served models, for a caller who brings an OpenRouter key.
    "openai.gpt-6-luna": "openai/gpt-6-luna",
    "openai.gpt-oss-120b": "openai/gpt-oss-120b",
    "qwen.qwen3-next-80b-a3b": "qwen/qwen3-next-80b-a3b-instruct",
}


def openrouter_model_id(slug: str | None) -> str | None:
    """The offered model id an OpenRouter slug stands for, or None when OSA does not know it.

    Exactly the reverse of ``OPENROUTER_MODEL_IDS``. The ``anthropic/claude-*`` aliases
    ``normalize_model`` resolves are deliberately not followed: they name older models
    (Claude Sonnet 4.5, say) that OpenRouter runs as themselves, so the offered model's
    reasoning levels say nothing about them. A slug that is not an offered model's is a
    caller's own choice, about which nothing is assumed, including whether it reasons.
    """
    if not slug:
        return None
    for model_id, known_slug in OPENROUTER_MODEL_IDS.items():
        if known_slug == slug:
            return model_id
    return None


def to_openrouter_model(model: str | None) -> str | None:
    """Return the OpenRouter slug for a model id, or None if not mappable.

    Args:
        model: A first-party Anthropic id, an OpenRouter slug, or None.

    Returns:
        The OpenRouter slug for an offered first-party id, the input
        unchanged when it already looks like an OpenRouter slug, or None
        when there is nothing usable to route.
    """
    if not model:
        return None
    if model in OPENROUTER_MODEL_IDS:
        return OPENROUTER_MODEL_IDS[model]
    if "/" in model:
        return model
    return None


def create_openrouter_llm(
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    provider: str | None = DEFAULT_PROVIDER,
    user_id: str | None = None,
    enable_caching: bool | None = None,
    reasoning_effort: str | None = None,
) -> BaseChatModel:
    """Create an OpenRouter LLM instance with prompt caching and tagged citations.

    Uses LiteLLM for native support of Anthropic's prompt caching feature.
    When caching is enabled, the system prompt and the last message carry
    cache_control markers for 90% cost reduction on cache hits. Tool results
    that carry ``search_result`` blocks are shown to the model as tagged text
    and the tags it writes come back as citations (see
    ``src.core.services.tagged_citations``).

    Provider Selection:
        - Anthropic models (anthropic/*) automatically use provider="Anthropic"
          for best performance, regardless of the provider parameter
        - Other models use the specified provider or default routing

    Args:
        model: Model identifier (e.g., "openai/gpt-oss-120b", "anthropic/claude-haiku-4.5")
        api_key: OpenRouter API key (defaults to OPENROUTER_API_KEY env var)
        temperature: Sampling temperature (0.0-1.0)
        max_tokens: Maximum tokens to generate
        provider: Specific provider to use (e.g., "Cerebras", "DeepInfra/FP8").
                 Ignored for Anthropic models, which always use "Anthropic" provider.
        user_id: User identifier for cache optimization (sticky routing)
        enable_caching: Enable prompt caching. If None (default), it is enabled. Only
            Anthropic's models are sent the ``cache_control`` markers; other models
            get plain messages (see ``litellm_chat.takes_cache_markers``).
        reasoning_effort: The community's level from the neutral scale, or None for
            ``DEFAULT_REASONING_EFFORT`` (high). Sent as OpenRouter's ``reasoning: {"effort": level}``
            body field, only for an offered model that has levels, at a level it
            accepts on OpenRouter (Claude Sonnet is never above ``high``, and has no
            ``none`` there, where its reasoning is mandatory); a slug OSA knows nothing
            about, including the older Claude slugs that ``normalize_model`` aliases, is
            sent nothing (and a debug line says so).

    Returns:
        A ``TaggedCitationChatLiteLLM`` configured for OpenRouter
    """
    # LiteLLM uses openrouter/ prefix for OpenRouter models
    litellm_model = f"openrouter/{model}"

    # Build model_kwargs for OpenRouter-specific options
    model_kwargs: dict[str, Any] = {
        # OpenRouter app identification headers
        "extra_headers": {
            "HTTP-Referer": "https://osc.earth/osa",
            "X-Title": "Open Science Assistant",
        },
    }

    # Auto-select Anthropic provider for Anthropic models (better performance)
    # Override any default provider if this is an Anthropic model
    if model.startswith("anthropic/"):
        effective_provider = "Anthropic"
        logger.debug("Auto-selected Anthropic provider for model %s (better performance)", model)
    else:
        effective_provider = provider

    # Provider routing (e.g., {"order": ["DeepInfra/FP8"]})
    # Use "order" not "only" - OpenRouter requires exact routing field name
    if effective_provider:
        model_kwargs["provider"] = {"order": [effective_provider]}

    # User ID for sticky cache routing
    if user_id:
        model_kwargs["user"] = user_id

    # The reasoning level (issue #545). OpenRouter's unified body field, which LiteLLM
    # passes through untouched like `provider` above. LiteLLM's own `reasoning_effort`
    # parameter is not used: it is the top-level OpenAI name (no "max"), and LiteLLM
    # refuses it outright for a model its map does not list as reasoning, which would
    # fail every request for a slug it does not know.
    reasoning_level = effective_reasoning_effort(
        openrouter_model_id(model), reasoning_effort, "openrouter"
    )
    if reasoning_level is not None:
        model_kwargs["reasoning"] = {"effort": reasoning_level}
    elif reasoning_effort is not None:
        logger.debug(
            "reasoning_effort=%r not sent to OpenRouter model %r: it is not an offered "
            "model with reasoning levels",
            reasoning_effort,
            model,
        )

    # Falls back to the env var (documented above) rather than requiring
    # every caller to read it themselves, but a request with neither fails
    # loud here instead of constructing successfully and only surfacing an
    # opaque auth error on the first real call -- matching
    # create_anthropic_llm's server-mode key check.
    resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not resolved_api_key:
        raise RuntimeError(
            "No OpenRouter API key available: pass api_key explicitly or set OPENROUTER_API_KEY"
        )

    # Streaming is required for on_chat_model_stream events in LangGraph. Imported here
    # because langchain_litellm takes about a second to import and only OpenRouter
    # requests need it.
    from src.core.services.litellm_chat import TaggedCitationChatLiteLLM

    return TaggedCitationChatLiteLLM(
        model=litellm_model,
        api_key=resolved_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        model_kwargs=model_kwargs,
        streaming=True,
        # Requested by default; the model only sends markers to models that take them.
        prompt_caching=True if enable_caching is None else enable_caching,
    )


# Reference list of known Anthropic Claude models supporting prompt caching
# This is informational only - the is_cacheable_model() function uses a permissive
# heuristic (any "anthropic/claude-*" model) rather than this restrictive list.
# Caching is enabled by default for all models; OpenRouter/LiteLLM handle
# unsupported models gracefully by ignoring cache_control parameters.
CACHEABLE_MODELS = {
    "claude-opus-4.6": "anthropic/claude-opus-4.6",
    "claude-sonnet-4.6": "anthropic/claude-sonnet-4.6",
    "claude-opus-4.5": "anthropic/claude-opus-4.5",
    "claude-sonnet-4.5": "anthropic/claude-sonnet-4.5",
    "claude-haiku-4.5": "anthropic/claude-haiku-4.5",
}


def is_cacheable_model(model: str) -> bool:
    """Check if a model identifier suggests Anthropic prompt caching support.

    Uses a heuristic check: returns True for model identifiers in the known
    cacheable models list, or any identifier starting with "anthropic/claude-".

    Note: This is optimistic and may return True for models that don't actually
    support caching. The LiteLLM/OpenRouter layer handles unsupported models
    gracefully by ignoring cache_control parameters.

    Args:
        model: Model identifier (e.g., "anthropic/claude-haiku-4.5")

    Returns:
        True if the model likely supports cache_control based on its identifier
    """
    # Check exact match in aliases
    if model in CACHEABLE_MODELS:
        return True
    # Check if it's an Anthropic Claude model (permissive heuristic)
    return model.startswith("anthropic/claude-")
