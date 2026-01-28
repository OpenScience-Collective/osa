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
        Found 1 function(s):

        **1. pop_loadset (function) - functions/popfunc/pop_loadset.m**
        Language: matlab
        [View source](https://github.com/sccn/eeglab/blob/main/functions/popfunc/pop_loadset.m#L1)

        Load an EEGLAB dataset file. POP_LOADSET is used to load or import
        EEGLAB datasets...
    """
    import sqlite3

    from src.knowledge.db import get_db_path
    from src.knowledge.search import search_docstrings

    community_id = "eeglab"

    # Check if database exists
    db_path = get_db_path(community_id)
    if not db_path.exists():
        return (
            f"Knowledge base not initialized for {community_id}.\n\n"
            f"To populate function documentation:\n"
            f"  osa sync docstrings --community {community_id}\n\n"
            f"Contact your administrator if you don't have sync permissions."
        )

    # Search docstrings table
    try:
        results = search_docstrings(
            query=query,
            project=community_id,
            limit=limit,
            language=language,
        )
    except sqlite3.OperationalError:
        # Database exists but tables not initialized (e.g., FTS5 tables missing)
        logger.warning("Docstrings table not initialized for %s", community_id, exc_info=True)
        return (
            f"Knowledge base not initialized for {community_id}.\n\n"
            f"To populate function documentation:\n"
            f"  osa sync docstrings --community {community_id}\n\n"
            f"Contact your administrator if you don't have sync permissions."
        )

    if not results:
        return f"No function documentation found for: {query}"

    # Format results
    lines = [f"Found {len(results)} function(s):\n"]
    for i, result in enumerate(results, 1):
        # SearchResult has: title, url, snippet, source (language), item_type, status, created_at
        lines.append(f"**{i}. {result.title}**")
        lines.append(f"Language: {result.source}")
        lines.append(f"[View source]({result.url})")
        lines.append(f"\n{result.snippet}\n")

    return "\n".join(lines)


@tool
def search_eeglab_faqs(
    query: str,
    category: str | None = None,
    limit: int = 5,
) -> str:
    """Search FAQ from EEGLab mailing list history (since 2004).

    Search over 20 years of mailing list discussions to find solutions to common
    problems and learn from past Q&A. The FAQ database is generated from community
    discussions using LLM summarization.

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
    import sqlite3

    from src.knowledge.db import get_db_path
    from src.knowledge.search import search_faq_entries

    community_id = "eeglab"

    # Check if database exists
    db_path = get_db_path(community_id)
    if not db_path.exists():
        return (
            f"Knowledge base not initialized for {community_id}.\n\n"
            f"To populate FAQ database:\n"
            f"  Step 1: osa sync mailman --community {community_id}\n"
            f"  Step 2: osa sync faq --community {community_id}\n\n"
            f"Contact your administrator if you don't have sync permissions."
        )

    # Search FAQ entries
    try:
        results = search_faq_entries(
            query=query,
            project=community_id,
            limit=limit,
            category=category,
        )
    except sqlite3.OperationalError:
        # Database exists but tables not initialized (e.g., FTS5 tables missing)
        logger.warning("FAQ table not initialized for %s", community_id, exc_info=True)
        return (
            f"Knowledge base not initialized for {community_id}.\n\n"
            f"To populate FAQ database:\n"
            f"  Step 1: osa sync mailman --community {community_id}\n"
            f"  Step 2: osa sync faq --community {community_id}\n\n"
            f"Contact your administrator if you don't have sync permissions."
        )

    if not results:
        return f"No FAQ entries found for: {query}"

    # Format results
    lines = [f"Found {len(results)} FAQ entries:\n"]
    for i, result in enumerate(results, 1):
        lines.append(f"**{i}. {result.question}**")
        lines.append(f"Category: {result.category} | Quality: {result.quality_score:.1f}/1.0")
        lines.append(f"Tags: {', '.join(result.tags)}")
        answer_preview = result.answer[:400]
        if len(result.answer) > 400:
            answer_preview += "..."
        lines.append(f"\n{answer_preview}")
        lines.append(f"\n[View thread]({result.thread_url})\n")

    return "\n".join(lines)


# Export for plugin discovery
__all__ = ["search_eeglab_docstrings", "search_eeglab_faqs"]
