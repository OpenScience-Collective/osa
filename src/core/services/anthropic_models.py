"""Offered Claude models, their aliases, and what they accept.

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

# Default (and cheapest) offered model.
DEFAULT_MODEL = "claude-haiku-4-5"

# Models offered to callers (widget dropdown, CLI, community config.yaml).
OFFERED_MODELS: dict[str, str] = {
    "claude-haiku-4-5": "Claude Haiku 4.5",
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

# Models that still accept sampling parameters. Claude 5-generation models
# (claude-sonnet-5) reject `temperature` with a 400 because the only value
# they accept is 1, the implicit default when the field is simply omitted.
SAMPLING_MODELS = {"claude-haiku-4-5"}

# Image media types the Messages API accepts on a content block, including a
# content block nested in a tool result.
#
# Nothing in the client stack checks this. langchain-anthropic copies the media
# type straight into the request, so a producer that emits an unaccepted type
# learns about it as a 400 from the endpoint, after the work that made the
# picture has already been done. A client-executed tool that returns figures
# (see .context/browser-execution-tool-design.md) has to gate on this set
# itself, which is why it is declared rather than left implicit; SVG in
# particular is matplotlib's natural vector output and is NOT accepted.
#
# Declared here, next to the model tables and free of third-party imports, so
# community config validation can reach it on a CLI-only install. The anthropic
# SDK declares the same set on Base64ImageSourceParam.media_type, and
# tests/test_core/test_tool_result_image_transport.py compares the two so this
# copy cannot drift silently.
IMAGE_MEDIA_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)


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
