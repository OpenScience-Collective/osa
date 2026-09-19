"""Anthropic ``search_result`` content block schema, shared by citable tools.

Claude only attaches an inline citation to a claim when that claim was
generated from a ``search_result`` content block returned as a tool's
output (see the Messages API's citations feature). This module is the one
place that knows that block's shape, verified live against the Claude
Platform on AWS endpoint (GA, no beta header):

    {
        "type": "search_result",
        "source": str,
        "title": str,
        "content": [{"type": "text", "text": str}],
        "citations": {"enabled": True},
    }

Every knowledge tool that wants its results to be citable builds its
blocks through :func:`build_search_result` rather than open-coding this
dict, so a future field change (or an Anthropic schema revision) is one
edit instead of six.
"""

from typing import Any

DEFAULT_TRUNCATION_SUFFIX = "..."


def truncate(text: str, limit: int, suffix: str = DEFAULT_TRUNCATION_SUFFIX) -> str:
    """Truncate ``text`` to at most ``limit`` characters.

    Consolidates the truncate-and-mark-with-ellipsis pattern the knowledge
    and community tools used to open-code independently (``text[:200] +
    "..." if len(text) > 200 else text``, repeated with different limits
    and suffixes). ``suffix`` is only appended when truncation actually
    happens, so a caller can tell from the result alone whether the text
    was cut.

    Args:
        text: The text to (possibly) truncate.
        limit: Maximum number of characters to keep from ``text`` itself
            (the suffix is added on top of this, not counted against it).
        suffix: Appended only when ``text`` is longer than ``limit``.

    Returns:
        ``text`` unchanged if it fits within ``limit``, otherwise the first
        ``limit`` characters of ``text`` followed by ``suffix``.
    """
    if len(text) <= limit:
        return text
    return text[:limit] + suffix


def build_search_result(source: str, title: str, text: str) -> dict[str, Any]:
    """Build an Anthropic ``search_result`` content block.

    Args:
        source: Stable identifier for citation attribution. Typically a
            URL, but the API accepts any stable string.
        title: Human-readable title, shown to a client rendering the
            citation (e.g. the widget's numbered source list).
        text: The retrieved content. Must be non-empty: a citation can only
            point at text that exists.

    Returns:
        A ``search_result`` block dict with citations enabled.

    Raises:
        ValueError: If ``text`` is empty, since an empty search result has
            nothing for a citation to point at.
    """
    if not text:
        raise ValueError("search_result content text must not be empty")
    return {
        "type": "search_result",
        "source": source,
        "title": title,
        "content": [{"type": "text", "text": text}],
        "citations": {"enabled": True},
    }
