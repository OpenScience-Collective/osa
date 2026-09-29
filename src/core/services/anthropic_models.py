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

# Instruction added to the system prompt of models that go back to the search
# tools over and over. GPT-6 Luna at maximum effort and gpt-oss-120b, given
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
            reasoning effort is set here, to its maximum.
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
        extra_request_fields={"reasoning": {"effort": "max"}},
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
