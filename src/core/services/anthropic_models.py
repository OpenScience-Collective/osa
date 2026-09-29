"""Offered models, their aliases, and what they accept.

The file keeps its original name from when every offered model was a Claude
model. It now also holds the three non-Anthropic models served from Amazon
Bedrock (``BEDROCK_MODELS``), so that one table answers "what can a request
name" for the widget, the CLI, and community config validation alike.

Deliberately free of third-party imports, for the same reason as
``src/core/services/anthropic_endpoints.py``: ``anthropic_llm.py`` pulls in
langchain-anthropic, which lives in the ``server`` extra, so anything a
CLI-only install has to reach (``src/cli/validate.py``, and through it
``src/core/config/community.py``) cannot import it. Config validation needs
to resolve a model id to decide whether a community's choice is sensible, so
the tables live here and ``anthropic_llm`` imports them.

Server-side callers keep importing these names from ``anthropic_llm``, which
re-exports them: the split is a packaging detail, not something every router
and agent should have to know.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

# Default offered model.
DEFAULT_MODEL = "claude-haiku-4-5"

# What langchain-aws writes to ``response_metadata["model_provider"]`` on a message
# a Bedrock model produced. A conversation can switch models between requests, and
# a model has to be able to tell which turns in the history were written by another
# provider (see ``strip_bedrock_turns`` in anthropic_llm.py).
BEDROCK_MODEL_PROVIDER = "bedrock_converse"

# Instruction added to the system prompt of models that go back to the search
# tools over and over. GPT-6 Luna (measured at maximum effort) and gpt-oss-120b, given
# the same retrieval tools as Claude, kept searching with reworded queries
# until they ran out of output tokens without answering; with this note both
# answered after two searches, and said so when the results did not contain
# the answer. Claude and Qwen3 Next answered without it.
_TOOL_DISCIPLINE = (
    "Search at most twice per question, then answer from what you found. Never repeat "
    "a search you already ran, or run one with nearly the same words. If the results do "
    "not contain the answer, say what they do cover and stop; do not keep searching."
)


@dataclass(frozen=True)
class BedrockModel:
    """A non-Anthropic model served from Amazon Bedrock through the Converse API.

    Attributes:
        label: Name shown in the widget's model menu.
        invoke_id: The id passed to Converse. GPT-6 Luna cannot be called by its
            base id; it needs a cross-Region inference profile, and ``us.`` keeps
            requests in US Regions (the ``global.`` profile is denied by this
            account's service control policy, and would route abroad anyway).
        region: Region to call, or None for ``Settings.bedrock_region``. Qwen3
            Next answers in N. Virginia and Oregon but its Ohio endpoint accepts
            the request and never replies (tested 2026-09-28), so it pins us-east-1.
        extra_request_fields: Sent as ``additionalModelRequestFields``. GPT-6 Luna's
            reasoning effort is set here, to high (maximum made a tool-using turn take
            15 to 50 seconds before its first word).
        caching: "automatic" when the service caches a repeated prompt prefix on
            its own and reports the tokens read and written; "none" otherwise.
            Explicit cache points are rejected by all three models, so no client
            code is needed for the first and none is possible for the second.
        prompt_addendum: Model-specific text appended to the system prompt.
    """

    label: str
    invoke_id: str
    region: str | None = None
    extra_request_fields: dict[str, Any] = field(default_factory=dict)
    caching: Literal["automatic", "none"] = "none"
    prompt_addendum: str = ""


# Every model here is priced at or below Claude Haiku 4.5 (see
# src/metrics/cost.py); tests/test_metrics/test_cost.py enforces that, because
# these are the models a deployment can offer as cheaper than the default.
BEDROCK_MODELS: dict[str, BedrockModel] = {
    "openai.gpt-6-luna": BedrockModel(
        label="OpenAI GPT-6 Luna",
        invoke_id="us.openai.gpt-6-luna",
        extra_request_fields={"reasoning": {"effort": "high"}},
        caching="automatic",
        prompt_addendum=_TOOL_DISCIPLINE,
    ),
    "qwen.qwen3-next-80b-a3b": BedrockModel(
        label="Qwen3 Next 80B A3B",
        invoke_id="qwen.qwen3-next-80b-a3b",
        region="us-east-1",
    ),
    "openai.gpt-oss-120b": BedrockModel(
        label="OpenAI gpt-oss-120b",
        invoke_id="openai.gpt-oss-120b-1:0",
        extra_request_fields={"reasoning_effort": "high"},
        prompt_addendum=_TOOL_DISCIPLINE,
    ),
}

# Models offered to callers (widget dropdown, CLI, community config.yaml).
OFFERED_MODELS: dict[str, str] = {
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    **{model_id: spec.label for model_id, spec in BEDROCK_MODELS.items()},
}

# Legacy OpenRouter-style identifiers that exist in saved widget settings,
# CLI configs, and community config.yaml files, normalized to first-party ids.
MODEL_ALIASES: dict[str, str] = {
    "anthropic/claude-haiku-4.5": "claude-haiku-4-5",
    "anthropic/claude-haiku-4-5": "claude-haiku-4-5",
    "claude-haiku-4.5": "claude-haiku-4-5",
    # Sonnet 5.5 replaced Sonnet 5 at the same price, so a saved setting or
    # community config that still names an earlier Sonnet runs on it.
    "claude-sonnet-5": "claude-sonnet-5-5",
    "claude-sonnet-5.5": "claude-sonnet-5-5",
    "anthropic/claude-sonnet-5.5": "claude-sonnet-5-5",
    "anthropic/claude-sonnet-5": "claude-sonnet-5-5",
    "anthropic/claude-sonnet-4.6": "claude-sonnet-5-5",
    "anthropic/claude-sonnet-4.5": "claude-sonnet-5-5",
    "claude-sonnet-4.5": "claude-sonnet-5-5",
    # The ids Bedrock itself uses for the models above, so a config that copied
    # one from the AWS console resolves.
    "us.openai.gpt-6-luna": "openai.gpt-6-luna",
    "openai.gpt-oss-120b-1:0": "openai.gpt-oss-120b",
}

# Models that still accept sampling parameters. Claude 5-generation models
# (claude-sonnet-5-5) reject `temperature` with a 400 because the only value
# they accept is 1, the implicit default when the field is simply omitted.
# GPT-6 Luna rejects `temperature` and `topP` the same way (Bedrock: "This model
# doesn't support the temperature field"); gpt-oss-120b and Qwen3 Next accept them.
SAMPLING_MODELS = {"claude-haiku-4-5", "openai.gpt-oss-120b", "qwen.qwen3-next-80b-a3b"}

# Reasoning effort (issue #545). One provider-neutral scale, lowest to highest, that a
# community sets once (``reasoning_effort`` in its config.yaml) and that each provider
# path turns into that platform's own request field. It is the union of what the
# platforms name: Anthropic's ``effort`` (low to max), OpenAI's ``reasoning.effort`` on
# Bedrock (none to max) and OpenRouter's (none to xhigh).
REASONING_SCALE: tuple[str, ...] = ("none", "low", "medium", "high", "xhigh", "max")
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]

# The levels each model accepts, in scale order. A level a model does not accept is never
# sent: ``resolve_reasoning_effort`` clamps to the nearest one it does. The levels are
# the models' own predetermined ones (Luna and gpt-oss measured against Bedrock on
# 2026-09-29: Luna accepts none, low, medium, high, xhigh and max and rejects
# ``minimal``; gpt-oss accepts low, medium and high and rejects ``max``), with one policy
# on top: Claude Sonnet 5.5 is never run above ``high``, whatever a community asks and on
# whichever platform it is reached, so it lists none through high even though the API
# accepts more.
REASONING_LEVELS: dict[str, tuple[str, ...]] = {
    "claude-sonnet-5-5": ("none", "low", "medium", "high"),
    "openai.gpt-6-luna": ("none", "low", "medium", "high", "xhigh", "max"),
    "openai.gpt-oss-120b": ("low", "medium", "high"),
}

# The offered models with no reasoning levels to set: Claude Haiku 4.5 thinks with a
# token budget rather than a level, and Qwen3 Next has no reasoning control. The key is
# ignored for them. Together with REASONING_LEVELS this names every offered model, which
# tests/test_core/test_reasoning_effort.py checks, so a newly offered model has to be
# put on one side or the other.
NO_REASONING_LEVELS: frozenset[str] = frozenset({"claude-haiku-4-5", "qwen.qwen3-next-80b-a3b"})

# Image media types the Messages API accepts on a content block, including a
# content block nested in a tool result.
#
# Nothing in the client stack checks this. langchain-anthropic copies the media
# type straight into the request, so a producer that emits an unaccepted type
# learns about it as a 400 from the endpoint, after the work that made the
# picture has already been done. A client-executed tool that returns figures
# (see .context/browser-execution-tool-design.md) has to gate on this set
# itself, which is why it is declared rather than left implicit. SVG is the
# trap worth naming: `savefig` defaults to PNG, but SVG is the usual choice
# when a figure is bound for a web page, and it is NOT accepted here.
#
# Declared here, next to the model tables and free of third-party imports, so
# community config validation can reach it on a CLI-only install. The anthropic
# SDK declares the same set on Base64ImageSourceParam.media_type, and
# tests/test_core/test_tool_result_image_transport.py compares the two so this
# copy cannot drift silently.
IMAGE_MEDIA_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)


def is_bedrock_model(model: str | None) -> bool:
    """Whether a model id (in any form ``normalize_model`` accepts) is served from Bedrock.

    Args:
        model: Model identifier, or None for the default.

    Returns:
        True for the Bedrock-served models. False for Claude models and for an id
        that is not offered at all.
    """
    try:
        return normalize_model(model) in BEDROCK_MODELS
    except ValueError:
        return False


def normalize_model(model: str | None) -> str:
    """Normalize a requested model id to an offered model.

    Args:
        model: Requested model identifier (first-party id, legacy
            OpenRouter-style id, or None for the default).

    Returns:
        An id present in ``OFFERED_MODELS``: a first-party Anthropic model id,
        or a Bedrock model id.

    Raises:
        ValueError: If the model is not an offered model.
    """
    if not model:
        return DEFAULT_MODEL
    resolved = MODEL_ALIASES.get(model, model)
    if resolved not in OFFERED_MODELS:
        offered = ", ".join(sorted(OFFERED_MODELS))
        raise ValueError(f"Model '{model}' is not available. Offered models: {offered}")
    return resolved


def reasoning_levels(model: str | None) -> tuple[str, ...]:
    """The reasoning levels a model accepts, lowest to highest; empty when it has none.

    Args:
        model: Model identifier, in any form ``normalize_model`` accepts.

    Returns:
        The levels from ``REASONING_LEVELS``, or an empty tuple for a model with no
        levels to set and for an id that is not offered.
    """
    try:
        return REASONING_LEVELS.get(normalize_model(model), ())
    except ValueError:
        return ()


def resolve_reasoning_effort(model: str | None, requested: str | None) -> str | None:
    """The level a model will actually run at for a requested one, or None to send none.

    A level the model accepts is used as asked. One above everything it accepts is
    lowered to its highest, so a community that asks for ``max`` gets a Claude Sonnet
    at ``high``; one below everything is raised to its lowest (gpt-oss cannot go below
    ``low``); and one in a gap between two it accepts takes the higher of the levels
    below it. None comes back when nothing was requested, or when the model has no
    levels (Haiku, Qwen3 Next, an id that is not offered): there is nothing to send.

    Args:
        model: Model identifier, in any form ``normalize_model`` accepts.
        requested: A level from ``REASONING_SCALE``, or None.

    Returns:
        A level from the model's own list, or None.

    Raises:
        ValueError: If ``requested`` is not on the scale.
    """
    if requested is None:
        return None
    if requested not in REASONING_SCALE:
        raise ValueError(
            f"reasoning effort {requested!r} is not one of {', '.join(REASONING_SCALE)}"
        )
    levels = reasoning_levels(model)
    if not levels:
        return None
    if requested in levels:
        return requested
    rank = REASONING_SCALE.index(requested)
    below = [level for level in levels if REASONING_SCALE.index(level) < rank]
    return below[-1] if below else levels[0]


def accepts_temperature(model: str | None) -> bool:
    """Whether a model honors a ``temperature``, ignoring unknown ids.

    Only answers the model half of the question: a request also drops
    ``temperature`` when extended thinking is on, since the two cannot be
    combined (see ``create_anthropic_llm``).

    Args:
        model: Model identifier, in any form ``normalize_model`` accepts.

    Returns:
        True if the model accepts ``temperature``. False for an id that is
        not offered, since no request can be sent with it either.
    """
    try:
        return normalize_model(model) in SAMPLING_MODELS
    except ValueError:
        return False
