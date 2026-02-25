"""Generic knowledge discovery tool factories.

These factories create parameterized tools for any community's knowledge base.
Tools search the community-specific database (knowledge/{community_id}.db)
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
import sqlite3

from langchain_core.tools import BaseTool, StructuredTool

from src.knowledge.db import get_db_path
from src.knowledge.search import (
    list_recent_github_items,
    search_docstrings,
    search_github_items,
    search_papers,
)

logger = logging.getLogger(__name__)


def _check_db_exists(community_id: str) -> bool:
    """Check if the community's knowledge database exists."""
    return get_db_path(community_id).exists()


def create_search_discussions_tool(
    community_id: str,
    community_name: str,
    repos: list[str] | None = None,
) -> BaseTool:
    """Create a tool for searching GitHub discussions for a community.

    Args:
        community_id: The community identifier (e.g., 'hed', 'bids')
        community_name: Display name (e.g., 'HED', 'BIDS')
        repos: Optional list of repos to mention in help text

    Returns:
        A LangChain tool for searching discussions
    """
    repo_help = ""
    if repos:
        repo_list = "\n".join(f"  - {r}" for r in repos[:5])
        repo_help = f"\n\nAvailable repositories:\n{repo_list}"

    def search_discussions_impl(
        query: str,
        include_issues: bool = True,
        include_prs: bool = True,
        limit: int = 5,
    ) -> str:
        """Search GitHub discussions implementation."""
        if not _check_db_exists(community_id):
            return (
                f"Knowledge database for {community_name} not initialized. "
                "Run 'osa sync init' and 'osa sync github' to populate it."
            )

        results = []

        if include_issues:
            issues = search_github_items(
                query, project=community_id, limit=limit, item_type="issue"
            )
            results.extend(issues)

        if include_prs:
            prs = search_github_items(query, project=community_id, limit=limit, item_type="pr")
            results.extend(prs)

        # Limit total combined results
        results = results[:limit]

        if not results:
            return f"No related discussions found for '{query}'."

        lines = [f"Related {community_name} discussions:\n"]
        for r in results:
            status_label = "(open)" if r.status == "open" else "(closed)"
            item_label = "Issue" if r.item_type == "issue" else "PR"
            lines.append(f"- [{item_label}] {r.title} {status_label}")
            lines.append(f"  [View on GitHub]({r.url})")
            if r.snippet:
                snippet = r.snippet[:200] + "..." if len(r.snippet) > 200 else r.snippet
                lines.append(f"  Preview: {snippet}")
            lines.append("")

        return "\n".join(lines)

    description = (
        f"Search {community_name} GitHub discussions (issues and PRs) for related topics. "
        "**IMPORTANT: This is for DISCOVERY, not answering.** "
        "Use this tool to find related discussions that the user might find helpful. "
        'Always present results as: "There\'s a related discussion, see: [link]" '
        f"Do NOT use discussion content to formulate answers.{repo_help}"
    )

    return StructuredTool.from_function(
        func=search_discussions_impl,
        name=f"search_{community_id}_discussions",
        description=description,
    )


def create_list_recent_tool(
    community_id: str,
    community_name: str,
    repos: list[str] | None = None,
) -> BaseTool:
    """Create a tool for listing recent GitHub activity for a community.

    Args:
        community_id: The community identifier (e.g., 'hed', 'bids')
        community_name: Display name (e.g., 'HED', 'BIDS')
        repos: Optional list of repos to mention in help text

    Returns:
        A LangChain tool for listing recent activity
    """
    repo_options = ""
    if repos:
        repo_list = "\n".join(f'  - "{r}"' for r in repos[:5])
        repo_options = f"\n\nFilter by repository:\n{repo_list}\n  Or None for all repos"

    def list_recent_impl(
        item_type: str = "all",
        repo: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> str:
        """List recent GitHub activity implementation."""
        if not _check_db_exists(community_id):
            return (
                f"Knowledge database for {community_name} not initialized. "
                "Run 'osa sync init' and 'osa sync github' to populate it."
            )

        # Convert "all" to None for the search function
        type_filter = None if item_type == "all" else item_type

        results = list_recent_github_items(
            project=community_id,
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

        lines = [f"Recent {community_name} GitHub activity:\n"]
        for r in results:
            status_label = "(open)" if r.status == "open" else "(closed)"
            item_label = "Issue" if r.item_type == "issue" else "PR"
            date_str = r.created_at[:10] if r.created_at else "unknown date"
            lines.append(f"- [{item_label}] {r.title} {status_label} - {date_str}")
            lines.append(f"  [View on GitHub]({r.url})")
            if r.snippet:
                snippet = r.snippet[:200] + "..." if len(r.snippet) > 200 else r.snippet
                lines.append(f"  Summary: {snippet}")
            lines.append("")

        return "\n".join(lines)

    description = (
        f"List recent {community_name} GitHub issues and PRs ordered by date. "
        "Use when users ask about recent activity, latest PRs, or newest issues. "
        f"Unlike search which finds by keywords, this lists items by creation date.{repo_options}"
    )

    return StructuredTool.from_function(
        func=list_recent_impl,
        name=f"list_{community_id}_recent",
        description=description,
    )


def create_search_papers_tool(
    community_id: str,
    community_name: str,
) -> BaseTool:
    """Create a tool for searching academic papers for a community.

    Args:
        community_id: The community identifier (e.g., 'hed', 'bids')
        community_name: Display name (e.g., 'HED', 'BIDS')

    Returns:
        A LangChain tool for searching papers
    """

    def search_papers_impl(query: str, limit: int = 5) -> str:
        """Search academic papers implementation."""
        if not _check_db_exists(community_id):
            return (
                f"Knowledge database for {community_name} not initialized. "
                "Run 'osa sync init' and 'osa sync papers' to populate it."
            )

        results = search_papers(query, project=community_id, limit=limit)

        if not results:
            return f"No related papers found for '{query}'."

        lines = ["Related papers:\n"]
        for r in results:
            source_label = f"[{r.source}]" if r.source else ""
            lines.append(f"- {r.title} {source_label}")
            lines.append(f"  [View Paper]({r.url})")
            if r.snippet:
                snippet = r.snippet[:200] + "..." if len(r.snippet) > 200 else r.snippet
                lines.append(f"  Abstract: {snippet}")
            if r.created_at:
                lines.append(f"  Published: {r.created_at}")
            lines.append("")

        return "\n".join(lines)

    description = (
        f"Search for academic papers related to {community_name}. "
        "**IMPORTANT: This is for DISCOVERY, not answering.** "
        f"Use this tool to find papers that cite or discuss {community_name}. "
        "Always present results as references for further reading. "
        "Do NOT use paper content to formulate answers."
    )

    return StructuredTool.from_function(
        func=search_papers_impl,
        name=f"search_{community_id}_papers",
        description=description,
    )


def create_search_docstrings_tool(
    community_id: str,
    community_name: str,
    language: str | None = None,
) -> BaseTool:
    """Create a tool for searching code docstrings for a community.

    Args:
        community_id: The community identifier (e.g., 'hed', 'bids', 'eeglab')
        community_name: Display name (e.g., 'HED', 'BIDS', 'EEGLAB')
        language: Optional language filter ('matlab' or 'python')

    Returns:
        A LangChain tool for searching code documentation
    """
    lang_help = ""
    if language:
        lang_help = f" Only searches {language.upper()} code."
    else:
        lang_help = " Searches both MATLAB and Python code."

    def search_docstrings_impl(query: str, limit: int = 5) -> str:
        """Search code docstrings implementation."""
        if not _check_db_exists(community_id):
            return (
                f"Knowledge database for {community_name} not initialized. "
                "Run 'osa sync init' and 'osa sync docstrings' to populate it."
            )

        try:
            results = search_docstrings(query, project=community_id, limit=limit, language=language)
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                logger.warning(
                    "Docstrings table not initialized for %s",
                    community_id,
                    extra={"query": query, "community": community_id},
                )
                return (
                    f"Knowledge database for {community_name} not initialized. "
                    f"Run 'osa sync docstrings --community {community_id}' to populate it."
                )
            raise

        if not results:
            lang_str = f" ({language})" if language else ""
            return f"No code documentation found for '{query}'{lang_str}."

        lines = [f"Code documentation in {community_name}:\n"]
        for r in results:
            lines.append(f"- {r.title}")
            lines.append(f"  [View source on GitHub]({r.url})")
            if r.snippet:
                snippet = r.snippet[:200] + "..." if len(r.snippet) > 200 else r.snippet
                lines.append(f"  Documentation: {snippet}")
            lines.append("")

        return "\n".join(lines)

    description = (
        f"Search {community_name} code documentation (docstrings from functions, classes, scripts).{lang_help} "
        "Use this to find how specific functions work, what parameters they accept, "
        "and see usage examples. Results include direct links to source code on GitHub."
    )

    return StructuredTool.from_function(
        func=search_docstrings_impl,
        name=f"search_{community_id}_code_docs",
        description=description,
    )


def create_search_faq_tool(
    community_id: str,
    community_name: str,
    list_names: list[str] | None = None,
) -> BaseTool:
    """Create a tool for searching FAQ entries from mailing lists.

    Args:
        community_id: The community identifier (e.g., 'eeglab', 'hed')
        community_name: Display name (e.g., 'EEGLAB', 'HED')
        list_names: Optional list of mailing list names for help text

    Returns:
        A LangChain tool for searching FAQ entries
    """
    list_help = ""
    if list_names:
        list_str = ", ".join(list_names)
        list_help = f" Sources: {list_str} mailing list archives."

    def search_faq_impl(
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> str:
        """Search FAQ entries implementation."""
        if not _check_db_exists(community_id):
            return (
                f"FAQ database for {community_name} not initialized. "
                "Run 'osa sync mailman' and 'osa sync faq' to populate it."
            )

        from src.knowledge.search import search_faq_entries

        try:
            results = search_faq_entries(
                query=query,
                project=community_id,
                limit=limit,
                category=category,
            )
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                logger.warning(
                    "FAQ table not initialized for %s",
                    community_id,
                    extra={"query": query, "community": community_id},
                )
                return (
                    f"FAQ database for {community_name} not initialized. "
                    f"Run 'osa sync mailman --community {community_id}' and "
                    f"'osa sync faq --community {community_id}' to populate it."
                )
            raise

        if not results:
            cat_str = f" (category: {category})" if category else ""
            return f"No FAQ entries found for '{query}'{cat_str}."

        lines = [f"Found {len(results)} FAQ entries:\n"]
        for i, result in enumerate(results, 1):
            lines.append(f"**{i}. {result.question}**")
            lines.append(
                f"Category: {result.category} | Quality: {result.quality_score:.1f}/1.0 | "
                f"Messages: {result.message_count}"
            )
            if result.tags:
                lines.append(f"Tags: {', '.join(result.tags)}")

            # Truncate answer if too long
            answer = result.answer
            if len(answer) > 500:
                answer = answer[:500] + "..."
            lines.append(f"\n{answer}\n")
            lines.append(f"[View full thread]({result.thread_url})\n")

        return "\n".join(lines)

    description = (
        f"Search {community_name} mailing list FAQ entries for answered questions. "
        "**Use this for:** Finding solutions to common problems, learning from past discussions. "
        f"Returns: question, answer summary, category, quality score, link to original thread.{list_help}"
    )

    return StructuredTool.from_function(
        func=search_faq_impl,
        name=f"search_{community_id}_faq",
        description=description,
    )


def create_knowledge_tools(
    community_id: str,
    community_name: str,
    repos: list[str] | None = None,
    include_discussions: bool = True,
    include_recent: bool = True,
    include_papers: bool = True,
    include_docstrings: bool = False,
    docstrings_language: str | None = None,
    include_faq: bool = False,
    faq_list_names: list[str] | None = None,
) -> list[BaseTool]:
    """Create all knowledge discovery tools for a community.

    This is a convenience function that creates all standard knowledge tools
    based on the community configuration.

    Args:
        community_id: The community identifier (e.g., 'hed', 'bids', 'eeglab')
        community_name: Display name (e.g., 'HED', 'BIDS', 'EEGLAB')
        repos: Optional list of GitHub repos for help text
        include_discussions: Include discussion search tool (default: True)
        include_recent: Include recent activity tool (default: True)
        include_papers: Include paper search tool (default: True)
        include_docstrings: Include code docstring search tool (default: False)
        docstrings_language: Filter docstrings by language ('matlab' or 'python')
        include_faq: Include mailing list FAQ search tool (default: False)
        faq_list_names: List of mailing list names for FAQ help text

    Returns:
        List of LangChain tools for the community
    """
    tools: list[BaseTool] = []

    if include_discussions:
        tools.append(create_search_discussions_tool(community_id, community_name, repos))

    if include_recent:
        tools.append(create_list_recent_tool(community_id, community_name, repos))

    if include_papers:
        tools.append(create_search_papers_tool(community_id, community_name))

    if include_docstrings:
        tools.append(
            create_search_docstrings_tool(community_id, community_name, docstrings_language)
        )

    if include_faq:
        tools.append(create_search_faq_tool(community_id, community_name, faq_list_names))

    return tools
