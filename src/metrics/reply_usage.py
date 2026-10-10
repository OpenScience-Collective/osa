"""What a reply used and cost, as the reader is told (issue #582).

The streams and the request metrics already count every request's tokens, and
``estimate_cost`` prices them. This module is the part of that figure the reader is shown:
on the ``done`` event, on a ``tool_request`` event for the run it ends, and on the
responses to requests that are not streamed.

Only requests answered by a model OSA offers (first-party Claude and the Bedrock models)
carry usage. A request served through OpenRouter is left out: its model id is the
OpenRouter slug, which is not in ``OFFERED_MODELS``, and the cost of its cache tokens
would rest on the Anthropic-derived multipliers in ``src.metrics.cost``, which are only an
approximation there (and LiteLLM can estimate its token counts). Neither is something to
put in front of a reader as a cost.

The cost is the dashboard's, not an invoice: cache writes are priced at the five-minute
rate whatever lifetime a deployment sets, and the tokens of a model call that failed and
was tried again are not counted, since a failed call reports none.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.services.anthropic_models import OFFERED_MODELS
from src.metrics.cost import MODEL_PRICING, estimate_cost

#: The description of ``usage`` on the non-streaming responses, shared so the two cannot
#: drift apart.
USAGE_FIELD_DESCRIPTION = (
    "Tokens this reply used, how many of them were cached, and an estimate of the cost in "
    "US dollars. Null when the provider reported no usage, for requests served through "
    "OpenRouter whatever the model, and when OSA could not build it (it is logged). "
    "The streamed endpoint sends the same shape on its `done` event, one object per run "
    "of a reply."
)


class ReplyUsage(BaseModel):
    """Tokens a reply used and what they cost, as an estimate.

    ``input_tokens`` includes the cached tokens counted below it, unlike the ``usage``
    object of Anthropic's own API, whose input count leaves them out. On a stream's
    ``done`` and ``tool_request`` events this is one run's usage, and a client adds up the
    runs of a reply. Build it with ``reply_usage``, which prices it.
    """

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(
        ..., ge=0, description="Input tokens, including the cached ones counted below"
    )
    output_tokens: int = Field(..., ge=0, description="Output tokens, reasoning included")
    cache_read_tokens: int = Field(
        ..., ge=0, description="Of the input tokens, how many were served from the prompt cache"
    )
    cache_creation_tokens: int = Field(
        ..., ge=0, description="Of the input tokens, how many wrote a new prompt-cache entry"
    )
    estimated_cost: float | None = Field(
        ...,
        ge=0,
        allow_inf_nan=False,
        description=(
            "Estimated cost in US dollars from OSA's price table, or null when the model "
            "has no price there. An estimate, not an invoice."
        ),
    )
    partial: bool = Field(
        ...,
        description=(
            "True when a model run of this reply reported no tokens, so these figures leave "
            "it out and are a lower bound"
        ),
    )

    @model_validator(mode="after")
    def _cached_tokens_are_part_of_the_input(self) -> "ReplyUsage":
        """Cached tokens are a subset of the input: counts that say otherwise come from an
        adapter that counts the way Anthropic's own API does, and would be priced wrongly."""
        if self.cache_read_tokens + self.cache_creation_tokens > self.input_tokens:
            raise ValueError("cached tokens exceed the input tokens they are part of")
        return self


def reply_usage(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    *,
    partial: bool = False,
    longest_prompt_tokens: int | None = None,
) -> ReplyUsage | None:
    """The usage to tell the reader, or None when there is none to tell.

    Args:
        model: The model that answered.
        input_tokens: Input tokens, including the cache tokens below.
        output_tokens: Output tokens.
        cache_read_tokens: Of the input tokens, those read from the prompt cache.
        cache_creation_tokens: Of the input tokens, those that wrote a cache entry.
        partial: Whether a model run of the reply reported no tokens, which these counts
            then leave out.
        longest_prompt_tokens: The input of the largest single model call, which decides
            whether a model priced by prompt length is in its long tier (see
            ``estimate_cost``). A reply of several calls must pass it.

    Returns:
        None for a request that is not answered by a model OSA offers (see the module
        docstring) and for one whose provider reported no tokens, which is not the same as
        a free one.

    Raises:
        pydantic.ValidationError: For counts that cannot be usage: negative, or cached
            tokens above the input.
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
                longest_prompt_tokens=longest_prompt_tokens,
            )
            if model in MODEL_PRICING
            else None
        ),
        partial=partial,
    )
