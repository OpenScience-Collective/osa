"""Cost estimation for LLM requests.

Model pricing table with per-token costs (USD per million tokens).
Pricing is from OpenRouter; models added incrementally so individual
prices may have different verification dates.

Also defines cost protection thresholds for blocking expensive models
on platform/community keys (not BYOK).
"""

import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)


class ModelRate(NamedTuple):
    """Per-token pricing for a model (USD per 1M tokens)."""

    input_per_1m: float
    output_per_1m: float


# Source: https://openrouter.ai/api/v1/models
# Last verified: 2026-03
MODEL_PRICING: dict[str, ModelRate] = {
    # Anthropic models
    "anthropic/claude-opus-4.6": ModelRate(5.00, 25.00),
    "anthropic/claude-opus-4.5": ModelRate(5.00, 25.00),
    "anthropic/claude-opus-4.1": ModelRate(15.00, 75.00),
    "anthropic/claude-opus-4": ModelRate(15.00, 75.00),
    "anthropic/claude-sonnet-4.6": ModelRate(3.00, 15.00),
    "anthropic/claude-sonnet-4.5": ModelRate(3.00, 15.00),
    "anthropic/claude-sonnet-4": ModelRate(3.00, 15.00),
    "anthropic/claude-haiku-4.5": ModelRate(1.00, 5.00),
    "anthropic/claude-3.7-sonnet": ModelRate(3.00, 15.00),
    "anthropic/claude-3.5-sonnet": ModelRate(6.00, 30.00),
    "anthropic/claude-3.5-haiku": ModelRate(0.80, 4.00),
    # OpenAI models
    "openai/gpt-5.2": ModelRate(1.75, 14.00),
    "openai/gpt-5.2-chat": ModelRate(1.75, 14.00),
    "openai/gpt-5.1": ModelRate(1.25, 10.00),
    "openai/gpt-5": ModelRate(1.25, 10.00),
    "openai/gpt-5-chat": ModelRate(1.25, 10.00),
    "openai/gpt-5-mini": ModelRate(0.25, 2.00),
    "openai/gpt-5-nano": ModelRate(0.05, 0.40),
    "openai/gpt-5-pro": ModelRate(15.00, 120.00),
    "openai/gpt-4.1": ModelRate(2.00, 8.00),
    "openai/gpt-4.1-mini": ModelRate(0.40, 1.60),
    "openai/gpt-4.1-nano": ModelRate(0.10, 0.40),
    "openai/gpt-4o": ModelRate(2.50, 10.00),
    "openai/gpt-4o-mini": ModelRate(0.15, 0.60),
    "openai/o4-mini": ModelRate(1.10, 4.40),
    "openai/o3": ModelRate(2.00, 8.00),
    "openai/o3-mini": ModelRate(1.10, 4.40),
    "openai/o3-pro": ModelRate(20.00, 80.00),
    "openai/o1": ModelRate(15.00, 60.00),
    "openai/gpt-oss-120b": ModelRate(0.04, 0.19),
    # Google models
    "google/gemini-3.1-pro-preview": ModelRate(2.00, 12.00),
    "google/gemini-3-pro-preview": ModelRate(2.00, 12.00),
    "google/gemini-3-flash-preview": ModelRate(0.50, 3.00),
    "google/gemini-2.5-pro": ModelRate(1.25, 10.00),
    "google/gemini-2.5-pro-preview": ModelRate(1.25, 10.00),
    "google/gemini-2.5-flash": ModelRate(0.30, 2.50),
    "google/gemini-2.5-flash-lite": ModelRate(0.10, 0.40),
    # DeepSeek models
    "deepseek/deepseek-v3.2": ModelRate(0.25, 0.40),
    "deepseek/deepseek-chat-v3.1": ModelRate(0.15, 0.75),
    "deepseek/deepseek-chat": ModelRate(0.32, 0.89),
    "deepseek/deepseek-r1": ModelRate(0.70, 2.50),
    "deepseek/deepseek-r1-0528": ModelRate(0.45, 2.15),
    # Qwen models
    "qwen/qwen3.5-397b-a17b": ModelRate(0.39, 2.34),
    "qwen/qwen3-235b-a22b-2507": ModelRate(0.07, 0.10),
    "qwen/qwen3-235b-a22b": ModelRate(0.45, 1.82),
    "qwen/qwen3-30b-a3b-2507": ModelRate(0.09, 0.30),
    "qwen/qwen3-coder": ModelRate(0.22, 1.00),
    "qwen/qwen3-max": ModelRate(1.20, 6.00),
    # Meta models
    "meta-llama/llama-4-maverick": ModelRate(0.15, 0.60),
    "meta-llama/llama-4-scout": ModelRate(0.08, 0.30),
    "meta-llama/llama-3.3-70b-instruct": ModelRate(0.10, 0.32),
}

# Validate all pricing entries at import time to catch typos
for _model_name, _rate in MODEL_PRICING.items():
    if _rate.input_per_1m < 0 or _rate.output_per_1m < 0:
        raise ValueError(
            f"Negative rate for model {_model_name}: "
            f"input={_rate.input_per_1m}, output={_rate.output_per_1m}"
        )

# Fallback rate for models not in the pricing table
_FALLBACK_RATE = ModelRate(input_per_1m=1.00, output_per_1m=3.00)

# Cost protection thresholds (USD per 1M input tokens)
# Applied only when using platform/community keys (not BYOK)
COST_WARN_THRESHOLD = 5.0  # Log warning for models above this
COST_BLOCK_THRESHOLD = 15.0  # Block requests for models above this


def estimate_cost(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate the USD cost for a request.

    Args:
        model: Model name in OpenRouter format (e.g., "qwen/qwen3-235b-a22b-2507").
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        Estimated cost in USD, rounded to 6 decimal places.
    """
    if model and model in MODEL_PRICING:
        rate = MODEL_PRICING[model]
    else:
        if model:
            logger.warning("No pricing data for model %s, using fallback rates", model)
        rate = _FALLBACK_RATE

    cost = (input_tokens * rate.input_per_1m + output_tokens * rate.output_per_1m) / 1_000_000
    return round(cost, 6)
