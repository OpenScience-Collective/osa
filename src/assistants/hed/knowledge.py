"""HED knowledge discovery tools.

These tools search the HED-specific knowledge database (knowledge/hed.db)
for related GitHub discussions and academic papers.

Purpose: DISCOVERY, not authoritative answers.
The agent should link users to relevant discussions, not answer from them.

Usage:
- "There's a related discussion, see: [link]"
- "You might find this paper helpful: [link]"

NOT:
- "Based on issue #123, the answer is..."
- "According to a discussion, you should..."
"""

import logging

from langchain_core.tools import tool

from src.knowledge.db import get_db_path
from src.knowledge.search import list_recent_github_items, search_github_items, search_papers

logger = logging.getLogger(__name__)

# Project name for database isolation
PROJECT = "hed"


def _check_db_exists() -> bool:
    """Check if the HED knowledge database exists."""
    return get_db_path(PROJECT).exists()


@tool
def search_hed_discussions(
    query: str,
    include_issues: bool = True,
    include_prs: bool = True,
    limit: int = 5,
) -> str:
    """Search HED GitHub discussions (issues and PRs) for related topics.

    **IMPORTANT: This is for DISCOVERY, not answering.**

    Use this tool to find related discussions that the user might find helpful.
    Always present results as: "There's a related discussion, see: [link]"
    Do NOT use discussion content to formulate answers.

    Args:
        query: Search terms to find relevant discussions (e.g., "validation error",
               "library schema", "definitions")
        include_issues: Whether to include GitHub issues (default: True)
        include_prs: Whether to include GitHub PRs (default: True)
        limit: Maximum number of results (default: 5)

    Returns:
        Formatted list of relevant discussions with links, or a message if
        the database is not initialized.
    """
    if not _check_db_exists():
        return (
            "Knowledge database not initialized. "
            "Run 'osa sync init' and 'osa sync github' to populate it."
        )

    results = []

    if include_issues:
        issues = search_github_items(query, project=PROJECT, limit=limit, item_type="issue")
        results.extend(issues)

    if include_prs:
        prs = search_github_items(query, project=PROJECT, limit=limit, item_type="pr")
        results.extend(prs)

    # Sort by relevance and limit total results
    results = results[:limit]

    if not results:
        return f"No related discussions found for '{query}'."

    lines = ["Related HED discussions:\n"]
    for r in results:
        status_label = "(open)" if r.status == "open" else "(closed)"
        item_label = "Issue" if r.item_type == "issue" else "PR"
        lines.append(f"- [{item_label}] {r.title} {status_label}")
        # Use markdown link format (no angle brackets)
        lines.append(f"  [View on GitHub]({r.url})")
        if r.snippet:
            # Truncate long snippets
            snippet = r.snippet[:200] + "..." if len(r.snippet) > 200 else r.snippet
            lines.append(f"  Preview: {snippet}")
        lines.append("")

    return "\n".join(lines)


@tool
def list_hed_recent(
    item_type: str = "all",
    repo: str | None = None,
    status: str | None = None,
    limit: int = 10,
) -> str:
    """List recent HED GitHub issues and PRs ordered by date.

    Use this tool when users ask about recent activity, latest PRs, or newest issues.
    Unlike search_hed_discussions which searches by keywords, this tool lists items
    by creation date.

    Args:
        item_type: Type of items to list: "issue", "pr", or "all" (default: "all")
        repo: Filter by repository name. Options:
              - "hed-standard/hed-specification"
              - "hed-standard/hed-python"
              - "hed-standard/hed-javascript"
              - "hed-standard/hed-schemas"
              Or None for all repos (default: None)
        status: Filter by status: "open", "closed", or None for all (default: None)
        limit: Maximum number of results (default: 10)

    Returns:
        Formatted list of recent GitHub items with links, or a message if
        the database is not initialized.
    """
    if not _check_db_exists():
        return (
            "Knowledge database not initialized. "
            "Run 'osa sync init' and 'osa sync github' to populate it."
        )

    # Convert "all" to None for the search function
    type_filter = None if item_type == "all" else item_type

    results = list_recent_github_items(
        project=PROJECT,
        limit=limit,
        item_type=type_filter,
        status=status,
        repo=repo,
    )

    if not results:
        filter_desc = []
        if item_type != "all":
            filter_desc.append(f"type={item_type}")
        if repo:
            filter_desc.append(f"repo={repo}")
        if status:
            filter_desc.append(f"status={status}")
        filter_str = ", ".join(filter_desc) if filter_desc else "no filters"
        return f"No GitHub items found ({filter_str})."

    lines = ["Recent HED GitHub activity:\n"]
    for r in results:
        status_label = "(open)" if r.status == "open" else "(closed)"
        item_label = "Issue" if r.item_type == "issue" else "PR"
        date_str = r.created_at[:10] if r.created_at else "unknown date"
        lines.append(f"- [{item_label}] {r.title} {status_label} - {date_str}")
        # Use markdown link format (no angle brackets)
        lines.append(f"  [View on GitHub]({r.url})")
        # Include first comment/body snippet if available
        if r.snippet:
            snippet = r.snippet[:200] + "..." if len(r.snippet) > 200 else r.snippet
            lines.append(f"  Summary: {snippet}")
        lines.append("")

    return "\n".join(lines)


@tool
def search_hed_papers(query: str, limit: int = 5) -> str:
    """Search for academic papers related to HED.

    **IMPORTANT: This is for DISCOVERY, not answering.**

    Use this tool to find papers that cite or discuss HED.
    Always present results as references for further reading.
    Do NOT use paper content to formulate answers.

    Args:
        query: Search terms to find relevant papers (e.g., "HED annotation",
               "neuroimaging events", "BIDS")
        limit: Maximum number of results (default: 5)

    Returns:
        Formatted list of relevant papers with links, or a message if
        the database is not initialized.
    """
    if not _check_db_exists():
        return (
            "Knowledge database not initialized. "
            "Run 'osa sync init' and 'osa sync papers' to populate it."
        )

    results = search_papers(query, project=PROJECT, limit=limit)

    if not results:
        return f"No related papers found for '{query}'."

    lines = ["Related papers:\n"]
    for r in results:
        source_label = f"[{r.source}]" if r.source else ""
        lines.append(f"- {r.title} {source_label}")
        # Use markdown link format (no angle brackets)
        lines.append(f"  [View Paper]({r.url})")
        if r.snippet:
            # Truncate long snippets
            snippet = r.snippet[:200] + "..." if len(r.snippet) > 200 else r.snippet
            lines.append(f"  Abstract: {snippet}")
        if r.created_at:
            lines.append(f"  Published: {r.created_at}")
        lines.append("")

    return "\n".join(lines)
