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

import re
from dataclasses import dataclass
from typing import Any, Literal, get_args

# Model classes. A class is a family at one price and speed tier, named for the tier
# ("haiku" is Anthropic's small fast model, "luna" is OpenAI's), and this table is the
# one place that says which concrete model each class is today. A community's
# config.yaml, the CLI, a request and the code name the class; everything else in this
# file is keyed by the id the table gives, so moving a class to a new generation is:
#   1. change its entry here;
#   2. add the id it replaces to PREVIOUS_GENERATIONS, so saved settings still resolve;
#   3. where the new model differs from the last (its price in src/metrics/cost.py, its
#      reasoning levels, how its thinking is turned off, whether it takes a temperature),
#      change that model's own entry below.
# A class that is not offered (OFFERED_MODELS) is refused like any unknown model until its
# facts are filled in and it is added there.
MODEL_CLASSES: dict[str, str] = {
    # Anthropic, from the Claude Platform on AWS (or a BYOK key).
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
    "fable": "claude-fable-5-1",
    # OpenAI, from Amazon Bedrock.
    "luna": "openai.gpt-6-luna",
    "terra": "openai.gpt-5.6-terra",
    "sol": "openai.gpt-6.1-sol",
    "astra": "openai.gpt-6-astra",
}

# The ids a class used to be, which still resolve to its current model: a widget's saved
# setting, a community's config.yaml or a CLI config that was written before the move.
# Each spelling is an id as someone may have copied it (Claude Platform, dotted, or an
# OpenRouter-style slug).
PREVIOUS_GENERATIONS: dict[str, tuple[str, ...]] = {
    "haiku": (
        "claude-haiku-4-5",
        "claude-haiku-4.5",
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-haiku-4.5",
    ),
    "sonnet": (
        "claude-sonnet-5",
        "claude-sonnet-4.5",
        "anthropic/claude-sonnet-5",
        "anthropic/claude-sonnet-4.6",
        "anthropic/claude-sonnet-4.5",
    ),
}

# The classes OSA offers, as the ids they are today. The rest of this file keys the
# facts about a model (levels, sampling, thinking, Bedrock serving) by these, never by
# a literal id.
HAIKU = MODEL_CLASSES["haiku"]
SONNET = MODEL_CLASSES["sonnet"]
LUNA = MODEL_CLASSES["luna"]

# Default offered model.
DEFAULT_MODEL = HAIKU


def _claude_label(model_id: str) -> str:
    """The widget's name for a Claude model id: ``claude-haiku-5-5`` is "Claude Haiku 5.5"."""
    match = re.fullmatch(r"claude-([a-z]+)-(\d+(?:-\d+)?)", model_id)
    if match is None:
        raise ValueError(
            f"{model_id!r} is not a Claude model id of the form claude-<name>-<version>"
        )
    return f"Claude {match.group(1).title()} {match.group(2).replace('-', '.')}"


def _claude_dotted(model_id: str) -> str:
    """A Claude model id with its version dotted: ``claude-haiku-5-5`` is ``claude-haiku-5.5``."""
    return re.sub(r"(\d)-(\d)", r"\1.\2", model_id)


# The platforms a model can be reached on: the Claude Platform on AWS ("anthropic"),
# Amazon Bedrock and OpenRouter. The one spelling every module uses, for a request's
# provider and for the reasoning levels that differ between platforms.
ProviderName = Literal["anthropic", "bedrock", "openrouter"]

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
        reasoning_field: How the model is told its reasoning level in
            ``additionalModelRequestFields``: "nested" for GPT-6 Luna
            (``{"reasoning": {"effort": level}}``), "flat" for gpt-oss-120b
            (``{"reasoning_effort": level}``), None for Qwen3 Next, which has no
            reasoning control and must be sent nothing. The shapes are not
            interchangeable and a wrong one is often a silent no-op: gpt-oss ignores
            the nested field with a 200, Luna answers the flat one with a 400, and
            Qwen3 Next hangs on a flat ``high`` (all measured 2026-09-29).
        caching: "automatic" when the service caches a repeated prompt prefix on
            its own and reports the tokens read and written; "none" otherwise.
            Explicit cache points are rejected by all three models, so no client
            code is needed for the first and none is possible for the second.
        prompt_addendum: Model-specific text appended to the system prompt.
    """

    label: str
    invoke_id: str
    region: str | None = None
    reasoning_field: Literal["nested", "flat"] | None = None
    caching: Literal["automatic", "none"] = "none"
    prompt_addendum: str = ""

    def reasoning_request_fields(self, level: str | None) -> dict[str, Any]:
        """The ``additionalModelRequestFields`` that set this model's reasoning level.

        Args:
            level: A level the model accepts (see ``resolve_reasoning_effort``), or None.

        Returns:
            The fields, or an empty dict when there is no level or the model has no
            reasoning control.
        """
        if level is None or self.reasoning_field is None:
            return {}
        if self.reasoning_field == "nested":
            return {"reasoning": {"effort": level}}
        return {"reasoning_effort": level}


# Every model here is priced at or below the old cheap tier, $1 / $5 per 1M tokens (what
# Claude Haiku 4.5 cost; see src/metrics/cost.py); tests/test_metrics/test_cost.py enforces
# that, because these are the models a deployment can offer as cheap alternatives to its
# Claude default. Claude Haiku 5.5 is cheaper still, about what Luna costs.
BEDROCK_MODELS: dict[str, BedrockModel] = {
    LUNA: BedrockModel(
        label="OpenAI GPT-6 Luna",
        invoke_id="us.openai.gpt-6-luna",
        reasoning_field="nested",
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
        reasoning_field="flat",
        prompt_addendum=_TOOL_DISCIPLINE,
    ),
}

# Models offered to callers (widget dropdown, CLI, community config.yaml).
OFFERED_MODELS: dict[str, str] = {
    HAIKU: _claude_label(HAIKU),
    SONNET: _claude_label(SONNET),
    **{model_id: spec.label for model_id, spec in BEDROCK_MODELS.items()},
}

#: The models to suggest, in order, to a reader whose model could not finish a request.
#: Haiku first: the HED validate-and-refine loop (#514) that Luna, GPT-OSS and Qwen
#: failed finished on it, and Sonnet costs more per token.
#: Sonnet follows, for the reader whose Haiku is the one that failed.
SUGGESTED_MODELS: tuple[str, ...] = (HAIKU, SONNET)

# Every other name a request, a saved widget setting, a CLI config or a community's
# config.yaml may use for an offered model, normalized to the id it runs: the class
# names, the ids a class used to be (PREVIOUS_GENERATIONS), the current Claude ids as
# the dotted and OpenRouter-style spellings, and the ids Bedrock itself uses.
MODEL_ALIASES: dict[str, str] = {
    **{name: model for name, model in MODEL_CLASSES.items() if model in OFFERED_MODELS},
    **{old: MODEL_CLASSES[name] for name, olds in PREVIOUS_GENERATIONS.items() for old in olds},
    **{
        spelling: model
        for model in (HAIKU, SONNET)
        for spelling in (
            _claude_dotted(model),
            f"anthropic/{model}",
            f"anthropic/{_claude_dotted(model)}",
        )
    },
    # The ids Bedrock itself uses for the models above, so a config that copied
    # one from the AWS console resolves.
    f"us.{LUNA}": LUNA,
    "openai.gpt-oss-120b-1:0": "openai.gpt-oss-120b",
}

# OpenRouter slugs for the models OSA offers. OpenRouter serves the same
# Claude models under creator/model-name slugs, so a request funded by an
# OpenRouter key (BYOK, or a community's own funded key) still runs the
# community's chosen model rather than switching to a different model family
# just because of which key paid for it. Bare first-party ids such as
# "claude-haiku-5-5" are not valid OpenRouter slugs, hence the mapping.
# tests/test_core/test_litellm_llm.py asserts these keys stay in step with
# OFFERED_MODELS so adding a model cannot silently skip this. The table lives here,
# with no third-party imports, so community config validation can reach it on a CLI-only
# install; ``litellm_llm`` re-exports it.
OPENROUTER_MODEL_IDS: dict[str, str] = {
    HAIKU: f"anthropic/{_claude_dotted(HAIKU)}",
    SONNET: f"anthropic/{_claude_dotted(SONNET)}",
    # The Bedrock-served models, for a caller who brings an OpenRouter key.
    LUNA: LUNA.replace(".", "/", 1),
    "openai.gpt-oss-120b": "openai/gpt-oss-120b",
    "qwen.qwen3-next-80b-a3b": "qwen/qwen3-next-80b-a3b-instruct",
}


# OpenRouter's routing variants ("Model variants" in its documentation): a suffix accepted
# on any model that only changes how the request is routed (fastest providers, cheapest,
# best at tool calls, and the deprecated ":online" web search), so the same model runs;
# ":nitro" and ":floor" can also change the price tier. They can be stacked
# ("openai/gpt-5.2:nitro:exacto"). The others (":free", ":batch", ":thinking",
# ":extended") are catalog entries of their own, at most one to a slug, and are not
# looked through: stripping ":free" would resolve to the paid entry.
OPENROUTER_ROUTING_VARIANTS = ("nitro", "floor", "exacto", "online")


def _without_routing_variants(slug: str) -> str:
    """An OpenRouter slug with its routing variants (``:nitro``, ``:floor``...) removed.

    A variant that is a catalog entry of its own (``:free``) is kept: it names a different
    entry, not a different route to the same one.
    """
    base, *variants = slug.split(":")
    kept = [v for v in variants if v not in OPENROUTER_ROUTING_VARIANTS]
    return ":".join([base, *kept])


def openrouter_model_id(slug: str | None) -> str | None:
    """The offered model id an OpenRouter slug stands for, or None when OSA does not know it.

    The reverse of ``OPENROUTER_MODEL_IDS``, looking through routing variants, in any
    order (``openai/gpt-oss-120b:nitro`` is ``openai.gpt-oss-120b``, run through faster
    providers). A catalog variant left over (``:free``) makes it a different entry, so
    None. The ``anthropic/claude-*`` aliases ``normalize_model`` resolves are
    deliberately not followed, with or without a variant: they name older models (Claude
    Sonnet 4.5, say) that OpenRouter runs as themselves, so the offered model's reasoning
    levels say nothing about them. A slug that is not an offered model's is a caller's own
    choice, about which nothing is assumed, including whether it reasons.
    """
    if not slug:
        return None
    slug = _without_routing_variants(slug)
    for model_id, known_slug in OPENROUTER_MODEL_IDS.items():
        if known_slug == slug:
            return model_id
    return None


# Models that still accept sampling parameters. Claude 5-generation models (Sonnet 5.5,
# Haiku 5.5) reject `temperature` with a 400 because the only value they accept is 1,
# the implicit default when the field is simply omitted. GPT-6 Luna rejects
# `temperature` and `topP` the same way (Bedrock: "This model doesn't support the
# temperature field"); gpt-oss-120b and Qwen3 Next accept them.
SAMPLING_MODELS: frozenset[str] = frozenset({"openai.gpt-oss-120b", "qwen.qwen3-next-80b-a3b"})

# Reasoning effort (issue #545). One provider-neutral scale, lowest to highest, that a
# community sets once (``reasoning_effort`` in its config.yaml) and that each provider
# path turns into that platform's own request field. It is the union of what the
# platforms name: Anthropic's ``effort`` (low to max), OpenAI's ``reasoning.effort`` on
# Bedrock (none to max) and OpenRouter's (none to max; it treats ``max`` as ``xhigh``).
# The tuple is derived from the Literal, so a level added to one cannot be missing from the
# other (the config would load a level that ``resolve_reasoning_effort`` then refuses).
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
REASONING_SCALE: tuple[ReasoningEffort, ...] = get_args(ReasoningEffort)

# The levels each model accepts, in scale order. A level a model does not accept is never
# sent: ``resolve_reasoning_effort`` clamps to the nearest one it does. The levels are
# the models' own predetermined ones (Luna and gpt-oss measured against Bedrock on
# 2026-09-29: Luna accepts none, low, medium, high, xhigh and max and rejects
# ``minimal``; gpt-oss accepts low, medium and high and rejects ``max``), with one policy
# on top: the Claude models are never run above ``high``, whatever a community asks and on
# whichever platform they are reached, so they list none through high even though the API
# accepts more (Sonnet 5.5 and Haiku 5.5 take xhigh and max too). ``none`` is no up-front
# thinking, at the lowest effort.
REASONING_LEVELS: dict[str, tuple[ReasoningEffort, ...]] = {
    SONNET: ("none", "low", "medium", "high"),
    HAIKU: ("none", "low", "medium", "high"),
    LUNA: ("none", "low", "medium", "high", "xhigh", "max"),
    "openai.gpt-oss-120b": ("low", "medium", "high"),
}

# The Claude models that think adaptively, steered by an effort level, and the `thinking`
# value that turns up-front thinking off on each. The two generations name it differently
# and the API is strict about it (a mismatch is a 400): Sonnet 5.5 rejects "disabled"
# ("Use thinking.type.between_tools for the lowest thinking setting"), and Haiku 5.5 has
# no "between_tools". Neither takes a token budget: ``thinking.type "enabled"`` with
# ``budget_tokens`` is a 400 on both. The API allows an off value only at effort "high"
# or below and with no other field inside ``thinking``; OSA sends an effort of at most
# "high" with it.
THINKING_OFF: dict[str, dict[str, str]] = {
    SONNET: {"type": "between_tools"},
    HAIKU: {"type": "disabled"},
}

# The level every model runs at when a community sets none, on every provider, so a model
# behaves the same whichever key paid for it: ``high``. That is Claude Sonnet 5.5 (the
# Claude Platform's own default is the same), Claude Haiku 5.5 (whose own default is
# ``medium``, so it is sent ``high`` explicitly), GPT-6 Luna and gpt-oss-120b. Luna at ``max`` made a
# tool-using turn take 15 to 50 seconds before its first word, and at ``xhigh`` and ``max``
# it answered with no documentation search in the median of three runs on one question, so
# no citations. A model with no levels (Qwen3 Next) is sent nothing.
DEFAULT_REASONING_EFFORT: ReasoningEffort = "high"

# Models whose reasoning OpenRouter does not let a request turn off (its model metadata
# marks it mandatory and the docs say not to send ``effort: none``), so ``none`` is not
# a level there: it is raised to the model's lowest. On the Claude Platform, ``none`` for
# Claude Sonnet 5.5 is real (``thinking: between_tools``). Claude Haiku 5.5's reasoning is
# not mandatory there (OpenRouter's metadata for it says so), so it keeps ``none``.
MANDATORY_REASONING_ON_OPENROUTER: frozenset[str] = frozenset({SONNET, "openai.gpt-oss-120b"})

# The offered models with no reasoning levels to set: Qwen3 Next has no reasoning control.
# The key is ignored for it. Together with REASONING_LEVELS this names every offered model,
# which tests/test_core/test_reasoning_effort.py checks, so a newly offered model has to be
# put on one side or the other.
NO_REASONING_LEVELS: frozenset[str] = frozenset({"qwen.qwen3-next-80b-a3b"})

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


def reasoning_levels(
    model: str | None, provider: ProviderName | None = None
) -> tuple[ReasoningEffort, ...]:
    """The reasoning levels a model accepts, lowest to highest; empty when it has none.

    Args:
        model: Model identifier, in any form ``normalize_model`` accepts.
        provider: The platform the request goes to, for the levels that differ
            between platforms (OpenRouter has no ``none`` for a model whose reasoning
            it makes mandatory); None for the model's own levels.

    Returns:
        The levels from ``REASONING_LEVELS``, or an empty tuple for a model with no
        levels to set and for an id that is not offered.
    """
    try:
        resolved = normalize_model(model)
    except ValueError:
        return ()
    levels = REASONING_LEVELS.get(resolved, ())
    if provider == "openrouter" and resolved in MANDATORY_REASONING_ON_OPENROUTER:
        levels = tuple(level for level in levels if level != "none")
    return levels


def resolve_reasoning_effort(
    model: str | None, requested: str | None, provider: ProviderName | None = None
) -> ReasoningEffort | None:
    """The level a model will actually run at for a requested one, or None to send none.

    A level the model accepts is used as asked. One above everything it accepts is
    lowered to its highest, so a community that asks for ``max`` gets a Claude Sonnet
    at ``high``; one below everything is raised to its lowest (gpt-oss cannot go below
    ``low``); and one in a gap between two it accepts takes the higher of the levels
    below it. None comes back when nothing was requested, or when the model has no
    levels (Qwen3 Next, or an id that is not offered): there is nothing to send.

    Args:
        model: Model identifier, in any form ``normalize_model`` accepts.
        requested: A level from ``REASONING_SCALE``, or None.
        provider: The platform the request goes to (see ``reasoning_levels``).

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
    levels = reasoning_levels(model, provider)
    if not levels:
        return None
    if requested in levels:
        return requested
    rank = REASONING_SCALE.index(requested)
    below = [level for level in levels if REASONING_SCALE.index(level) < rank]
    return below[-1] if below else levels[0]


def effective_reasoning_effort(
    model: str | None, requested: str | None, provider: ProviderName
) -> ReasoningEffort | None:
    """The level to send a model on a platform: the community's, else ``high``.

    Args:
        model: Model identifier, in any form ``normalize_model`` accepts. None (a slug
            OSA could not map to a model) sends nothing: it is not the default model
            here, as it is for ``normalize_model``.
        requested: The community's ``reasoning_effort``, or None.
        provider: The platform the request goes to.

    Returns:
        A level the model accepts on that platform, or None to send nothing: the model
        has no levels (or is not an offered model).
    """
    if not model:
        return None
    return resolve_reasoning_effort(
        model, DEFAULT_REASONING_EFFORT if requested is None else requested, provider
    )


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


def suggest_another_model(failed: str | None) -> tuple[str, str] | None:
    """The offered model to suggest to a reader whose ``failed`` model could not finish.

    Args:
        failed: The model that failed, as the request ran it: an offered id, a legacy alias,
            an OpenRouter slug, or None when it is not known.

    Returns:
        The first of ``SUGGESTED_MODELS`` that is not the one that failed, as ``(id,
        label)``. None only when every model in it is the failed one or is not offered
        (never, with today's two). With no failed model named, the first.
    """
    try:
        current = normalize_model(failed) if failed else None
    except ValueError:
        # An OpenRouter slug, perhaps with a routing variant (":nitro"): the offered model
        # it stands for, or else the string as it came. A slug of an earlier generation
        # ("anthropic/claude-haiku-4.5:nitro") counts as the class it was, as it does in
        # the widget, so the model that failed is not offered back to the reader.
        current = openrouter_model_id(failed)
        if current is None:
            try:
                current = normalize_model(_without_routing_variants(failed))
            except ValueError:
                current = failed
    for model_id in SUGGESTED_MODELS:
        if model_id != current and model_id in OFFERED_MODELS:
            return model_id, OFFERED_MODELS[model_id]
    return None
