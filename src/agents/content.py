"""Helpers for extracting text from Anthropic's block-list message content.

An assistant message's ``content`` is not always a plain string. Whenever
extended thinking is enabled, or whenever tools are bound to the model,
langchain-anthropic stops coercing content to a string: both the final
message and every streamed chunk instead carry a list of typed content
blocks, e.g. ``{"type": "text", "text": "..."}`` alongside
``{"type": "thinking", "thinking": "...", "signature": "..."}``.

Treating that list as a string (``str(content)``) puts a stringified Python
list into the answer; concatenating it onto a string accumulator
(``full_response += chunk``) raises ``TypeError``. These helpers are the one
place that understands the block-list shape, so call sites do not each need
to re-derive it -- and so streamed reasoning never leaks to a client: the
product decision is that clients see a content-free ``thinking`` signal
(the model is working), never the reasoning text itself.
"""

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Block "type" values that carry reasoning rather than answer text.
# `redacted_thinking` is Anthropic's encrypted-reasoning variant (returned
# when a thinking block is flagged, e.g. by safety systems); it carries no
# usable text either way, so it is classified the same as `thinking`.
_THINKING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})

# Non-text block types with their own dedicated handling elsewhere (tool-call
# metadata streamed via separate events), so silently contributing nothing
# here is expected, not a sign of a problem.
_KNOWN_NON_TEXT_BLOCK_TYPES = frozenset({"tool_use"})

BlockKind = Literal["text", "thinking"]


def extract_text(content: str | list[Any]) -> str:
    """Return only the answer text of a message's ``content``.

    Args:
        content: A message's ``content``: a plain string (the common case,
            when thinking is off and no tools are bound), or a list of
            content blocks (thinking enabled, or tools bound; see the
            module docstring).

    Returns:
        The concatenated text of every ``"text"`` block, in order. Thinking
        blocks and any other non-text block (e.g. ``tool_use``) contribute
        nothing: reasoning is never surfaced as answer text, and non-text
        blocks are handled by their own dedicated paths (tool-call
        metadata), not the answer string.
    """
    if isinstance(content, str):
        return content
    return "".join(text for kind, text in classify_content_blocks(content) if kind == "text")


def classify_content_blocks(content: str | list[Any]) -> list[tuple[BlockKind, str]]:
    """Classify a message's or streamed chunk's content into (kind, text) pairs.

    Args:
        content: A message's or streamed chunk's ``content``.

    Returns:
        A list of ``(kind, text)`` pairs in block order, skipping blocks
        with no useful signal (empty text blocks, and non-text/non-thinking
        blocks such as ``tool_use``, which are already handled by their own
        dedicated event streams). ``"thinking"`` pairs always carry an
        empty string: callers use that kind only as a liveness signal (e.g.
        an SSE ``thinking`` event with no payload), never as text. A plain
        string is returned as a single ``("text", content)`` pair (or ``[]``
        for an empty string), matching the pre-thinking shape.
    """
    if isinstance(content, str):
        return [("text", content)] if content else []

    pairs: list[tuple[BlockKind, str]] = []
    warned_block_types: set[Any] = set()
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text", "")
            if text:
                pairs.append(("text", text))
        elif block_type in _THINKING_BLOCK_TYPES:
            pairs.append(("thinking", ""))
        elif block_type in _KNOWN_NON_TEXT_BLOCK_TYPES:
            # tool_use, etc. -- intentionally not surfaced here; handled by
            # their own dedicated events elsewhere.
            pass
        elif block_type not in warned_block_types:
            # A future langchain-anthropic block type that carries answer
            # text would otherwise truncate answers with no error -- the
            # same invisible-failure class the caching layer already guards
            # against. Dedupe per call so one odd chunk (or a whole streamed
            # response full of them) cannot flood the log.
            warned_block_types.add(block_type)
            logger.warning(
                "classify_content_blocks: unrecognized content block type %r; "
                "any text it carries is dropped, not surfaced as answer text.",
                block_type,
            )
    return pairs
