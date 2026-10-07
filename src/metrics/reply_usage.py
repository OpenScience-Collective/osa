"""What a reply used and cost, as the reader is told.

The streams and the request metrics already count every request's tokens, and
``estimate_cost`` prices them. This is the part of that the reader gets: on the ``done``
event, on a ``tool_request`` event for the run it ends, and on the responses to requests
that are not streamed (issue #582).

Only the models OSA offers itself (first-party Claude and the Bedrock models) report
usage. A request served through OpenRouter has no entry in ``OFFERED_MODELS`` under its
slug and reports none: its cache figures are an approximation, not something to put in
front of a reader as a cost.
"""

from pydantic import BaseModel, ConfigDict, Field

from src.core.services.anthropic_models import OFFERED_MODELS
from src.metrics.cost import MODEL_PRICING, estimate_cost


class ReplyUsage(BaseModel):
    """Tokens a reply used and what they cost, as an estimate."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(
        ..., description="Input tokens, including the cached ones counted below"
    )
    output_tokens: int = Field(..., description="Output tokens, reasoning included")
    cache_read_tokens: int = Field(
        ..., description="Of the input tokens, how many were served from the prompt cache"
    )
    cache_creation_tokens: int = Field(
        ..., description="Of the input tokens, how many wrote a new prompt-cache entry"
    )
    estimated_cost: float | None = Field(
        ...,
        description=(
            "Estimated cost in US dollars from OSA's price table, or null when the model "
            "has no price there. An estimate, not an invoice."
        ),
    )


def reply_usage(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> ReplyUsage | None:
    """The usage to tell the reader, or None when there is none to tell.

    Args:
        model: The model that answered.
        input_tokens: Input tokens, including the cache tokens below.
        output_tokens: Output tokens.
        cache_read_tokens: Of the input tokens, those read from the prompt cache.
        cache_creation_tokens: Of the input tokens, those that wrote a cache entry.

    Returns:
        None for a model OSA does not offer itself (see the module docstring) and for a
        request whose provider reported no tokens, which is not the same as a free one.
    """
    if model not in OFFERED_MODELS or not (input_tokens or output_tokens):
        return None
    return ReplyUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        estimated_cost=(
            estimate_cost(
                model,
                input_tokens,
                output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
            )
            if model in MODEL_PRICING
            else None
        ),
    )
