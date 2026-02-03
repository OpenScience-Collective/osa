"""Cost estimation for LLM requests.

Model pricing table with per-token costs (USD per million tokens).
Pricing is from OpenRouter as of 2025-07; update regularly.
"""

import logging

logger = logging.getLogger(__name__)

# Pricing: USD per 1M tokens (input, output)
# Source: https://openrouter.ai/models
# Last updated: 2025-07
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Qwen models
    "qwen/qwen3-235b-a22b-2507": (0.14, 0.34),
    "qwen/qwen3-30b-a3b-2507": (0.07, 0.15),
    # OpenAI models
    "openai/gpt-4o": (2.50, 10.00),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-oss-120b": (0.00, 0.00),  # Free tier
    "openai/o1": (15.00, 60.00),
    "openai/o3-mini": (1.10, 4.40),
    # Anthropic models
    "anthropic/claude-opus-4": (15.00, 75.00),
    "anthropic/claude-sonnet-4": (3.00, 15.00),
    "anthropic/claude-haiku-4.5": (0.80, 4.00),
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
    # Google models
    "google/gemini-2.5-pro-preview": (1.25, 10.00),
    "google/gemini-2.5-flash-preview": (0.15, 0.60),
    # DeepSeek models
    "deepseek/deepseek-chat-v3": (0.14, 0.28),
    "deepseek/deepseek-r1": (0.55, 2.19),
    # Meta models
    "meta-llama/llama-4-maverick": (0.16, 0.40),
}

# Fallback rate for models not in the pricing table
_FALLBACK_INPUT_RATE = 1.00  # USD per 1M tokens
_FALLBACK_OUTPUT_RATE = 3.00  # USD per 1M tokens


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
        input_rate, output_rate = MODEL_PRICING[model]
    else:
        if model:
            logger.warning("No pricing data for model %s, using fallback rates", model)
        input_rate, output_rate = _FALLBACK_INPUT_RATE, _FALLBACK_OUTPUT_RATE

    cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return round(cost, 6)
