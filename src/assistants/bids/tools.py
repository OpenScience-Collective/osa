"""BIDS community-specific tools."""

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def lookup_bep(
    query: str,
    limit: int = 3,
) -> str:
    """Look up BIDS Extension Proposals (BEPs) by number or keyword.

    Use this when users ask about:
    - Extending BIDS for new data types
    - Data types not yet in the specification
    - Specific BEP numbers or topics (e.g., "BEP032", "neuropixels", "eye tracking")
    - Proposals currently in review

    Args:
        query: BEP number (e.g., "032", "BEP032") or keyword (e.g., "neuropixels")
        limit: Max results to return (default: 3)

    Returns:
        Formatted BEP information with metadata, links, and content snippets.

    Example:
        >>> lookup_bep("032")
        **BEP032: Microelectrode electrophysiology**
        Status: proposed (open PR)
        PR: https://github.com/bids-standard/bids-specification/pull/1705
        Preview: https://bids-specification--1705.org.readthedocs.build/...

        >>> lookup_bep("eye tracking")
        **BEP020: Eye Tracking including Gaze Position and Pupil Size**
        ...
    """
    import sqlite3

    from src.knowledge.db import get_db_path
    from src.knowledge.search import search_beps

    community_id = "bids"

    db_path = get_db_path(community_id)
    if not db_path.exists():
        return (
            f"BEP knowledge base not initialized for {community_id}.\n\n"
            f"To sync BEP data:\n"
            f"  osa sync beps --community {community_id}\n\n"
            f"Contact your administrator if you don't have sync permissions."
        )

    try:
        results = search_beps(query=query, project=community_id, limit=limit)
    except sqlite3.OperationalError:
        logger.warning("BEP table not initialized for %s", community_id, exc_info=True)
        return (
            f"BEP knowledge base not initialized for {community_id}.\n\n"
            f"To sync BEP data:\n"
            f"  osa sync beps --community {community_id}\n\n"
            f"Contact your administrator if you don't have sync permissions."
        )

    if not results:
        return f"No BEPs found matching: {query}"

    lines = [f"Found {len(results)} BEP(s):\n"]
    for result in results:
        lines.append(f"**BEP{result.bep_number}: {result.title}**")
        lines.append(f"Status: {result.status}")

        if result.leads:
            lines.append(f"Leads: {', '.join(result.leads)}")

        if result.pull_request_url:
            lines.append(f"PR: {result.pull_request_url}")
        if result.html_preview_url:
            lines.append(f"Preview: {result.html_preview_url}")
        if result.google_doc_url:
            lines.append(f"Google Doc: {result.google_doc_url}")

        if result.snippet:
            lines.append(f"\n{result.snippet}\n")
        else:
            lines.append("")

    return "\n".join(lines)


# Export for plugin discovery
__all__ = ["lookup_bep"]
