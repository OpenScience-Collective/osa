"""EEGLab-specific tools for docstring and FAQ search."""

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def search_eeglab_docstrings(
    query: str,
    limit: int = 5,
    language: str | None = None,
) -> str:
    """Search function documentation from EEGLab codebase.

    Use this to find MATLAB/Python function signatures, parameters, and usage examples
    from the EEGLAB codebase.

    Args:
        query: Search query (function name or description)
        limit: Max results to return (default: 5)
        language: Filter by language: "matlab" or "python" (optional)

    Returns:
        Formatted search results with function signatures and documentation.

    Example:
        >>> search_eeglab_docstrings("pop_loadset")
        Found 1 functions:
        **1. pop_loadset** (matlab)
        File: functions/popfunc/pop_loadset.m
        ```matlab
        function [EEG, com] = pop_loadset(filename, filepath)
        ```
        Load an EEGLAB dataset file...
    """
    from src.knowledge.db import get_db_path
    from src.knowledge.search import search_docstrings

    community_id = "eeglab"

    # Check if database exists
    db_path = get_db_path(community_id)
    if not db_path.exists():
        return f"Database not initialized. Run: osa sync docstrings --community {community_id}"

    # Search docstrings table
    results = search_docstrings(
        query=query,
        project=community_id,
        limit=limit,
        language=language,
    )

    if not results:
        return f"No function documentation found for: {query}"

    # Format results
    lines = [f"Found {len(results)} functions:\n"]
    for i, result in enumerate(results, 1):
        lines.append(f"**{i}. {result.name}** ({result.language})")
        lines.append(f"File: {result.file_path}")
        if result.signature:
            lines.append(f"```{result.language}\n{result.signature}\n```")
        lines.append(f"{result.docstring[:300]}...")
        lines.append("")

    return "\n".join(lines)


@tool
def search_eeglab_faqs(
    query: str,
    category: str | None = None,
    limit: int = 5,
) -> str:
    """Search FAQ from EEGLab mailing list history (2004-2026).

    Search 22 years of mailing list discussions to find solutions to common problems
    and learn from past Q&A. The FAQ database is generated from community discussions
    using LLM summarization.

    Args:
        query: Search query (topic or question)
        category: Filter by category (troubleshooting, how-to, bug-report, etc.)
        limit: Max results to return (default: 5)

    Returns:
        Formatted FAQ entries with questions, answers, quality scores, and thread links.

    Example:
        >>> search_eeglab_faqs("artifact removal")
        Found 3 FAQ entries:
        **1. How do I remove artifacts from my EEG data?**
        Category: how-to | Quality: 0.9/1.0
        Tags: artifacts, preprocessing, ICA

        There are several approaches to artifact removal in EEGLAB...

        [View thread](https://sccn.ucsd.edu/pipermail/eeglablist/...)
    """
    from src.knowledge.db import get_db_path
    from src.knowledge.search import search_faq_entries

    community_id = "eeglab"

    # Check if database exists
    db_path = get_db_path(community_id)
    if not db_path.exists():
        return f"FAQ database not initialized. Run: osa sync mailman && osa sync faq --community {community_id}"

    # Search FAQ entries
    results = search_faq_entries(
        query=query,
        project=community_id,
        limit=limit,
        category=category,
    )

    if not results:
        return f"No FAQ entries found for: {query}"

    # Format results
    lines = [f"Found {len(results)} FAQ entries:\n"]
    for i, result in enumerate(results, 1):
        lines.append(f"**{i}. {result.question}**")
        lines.append(f"Category: {result.category} | Quality: {result.quality_score:.1f}/1.0")
        lines.append(f"Tags: {', '.join(result.tags)}")
        lines.append(f"\n{result.answer[:400]}...")
        lines.append(f"\n[View thread]({result.thread_url})\n")

    return "\n".join(lines)


# Export for plugin discovery
__all__ = ["search_eeglab_docstrings", "search_eeglab_faqs"]
