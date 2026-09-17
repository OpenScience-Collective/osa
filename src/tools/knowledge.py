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
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from src.knowledge.db import get_db_path
from src.knowledge.papers_sync import search_papers_live
from src.knowledge.search import (
    get_full_docstring,
    list_recent_github_items,
    search_discourse_topics,
    search_docstrings,
    search_github_items,
    search_papers,
)
from src.tools.citations import build_search_result, truncate

logger = logging.getLogger(__name__)

# Cap on one FAQ answer's citable text. The other two citable tools pass
# search snippets, which src/knowledge/search.py has already truncated, but
# a FAQ answer comes straight out of the database and is only capped at
# ingest (5000 chars, src/knowledge/db.py). With the default limit=5 that
# would put 25k chars of tool_result on the wire per search, ten times the
# plain-string path's 5 x 500. This is more generous than the 500-char
# display truncation below, because a citation points at an exact span and
# a span cut mid-sentence is worse than no citation, but it is still bounded.
_MAX_CITABLE_FAQ_ANSWER_CHARS = 2000


def _check_db_exists(community_id: str) -> bool:
    """Check if the community's knowledge database exists."""
    return get_db_path(community_id).exists()


def _build_citation_blocks(
    results: list[Any],
    *,
    source: Any,
    title: Any,
    text: Any,
) -> list[dict[str, Any]]:
    """Build one search_result block per result item, for a citable tool.

    Args:
        results: The tool's own result objects (already fetched).
        source: Callable ``item -> str`` returning the citation source
            (typically the item's URL).
        title: Callable ``item -> str`` returning the citation title.
        text: Callable ``item -> str`` returning the citable text. Items
            whose text is empty are skipped -- a search_result's content
            cannot be empty, and an item with nothing to show is not worth
            a block. An item with no source is still included: see below.

    Returns:
        A list of search_result blocks, one per item with non-empty text.
        Never raises even if every item lacks text; callers fall back to
        the plain-string formatting when this comes back empty.
    """
    blocks = []
    for item in results:
        item_text = text(item)
        if not item_text:
            # A skipped item is one the model never sees on this path, while
            # the plain-string path still shows its title and link, so the
            # same query can yield a different set of sources depending on
            # which provider is paying. Logged so a systematic ingestion gap
            # (rows with an empty answer) is visible instead of quietly
            # shrinking the citable corpus.
            logger.warning(
                "Skipping an uncitable search result with no text: source=%r", source(item)
            )
            continue
        item_source = source(item)
        if not item_source:
            # Kept rather than skipped. The API accepts an empty source
            # (verified against the live endpoint: the request succeeds and
            # the citation comes back with source ""), so the only thing lost
            # is the marker, which CitationTracker drops and logs. Skipping
            # would instead hide retrieved content from the answer entirely,
            # which is the worse of the two failures for a tool whose job is
            # answering from documentation.
            logger.warning(
                "Citable search result has no source, so its citation cannot be rendered: %r",
                title(item),
            )
        blocks.append(build_search_result(source=item_source, title=title(item), text=item_text))
    return blocks


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
        "**IMPORTANT: This is for DISCOVERY, not answering.** "
        "Use when users ask about recent activity, latest PRs, or newest issues. "
        "Unlike search which finds by keywords, this lists items by creation date. "
        f"Do NOT use listed item content to formulate answers.{repo_options}"
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

    Not citable: its description tells the model this is for discovery,
    not for formulating answers (see create_knowledge_tools' `citations`
    docstring for the rule this follows).

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
                lines.append(f"  Abstract: {truncate(r.snippet, 200)}")
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


def create_search_papers_live_tool(
    community_id: str,
    community_name: str,
) -> BaseTool:
    """Create a tool for live (on-demand) academic paper search via opencite.

    Unlike the local paper search (pre-synced rows), this fetches fresh results
    from the live literature, newest first, and caches them for next time.

    Not citable: its description tells the model this is for discovery,
    not for formulating answers (see create_search_papers_tool).

    Args:
        community_id: The community identifier (e.g., 'hed', 'eeglab')
        community_name: Display name (e.g., 'HED', 'EEGLAB')

    Returns:
        A LangChain tool for live paper search
    """

    def search_papers_live_impl(query: str, limit: int = 5) -> str:
        """Live academic paper search implementation."""
        results = search_papers_live(query, project=community_id, limit=limit)

        if not results:
            return (
                f"No recent papers found online for '{query}'. "
                "Try rephrasing, or use the local paper search."
            )

        lines = ["Most recent papers (live search):\n"]
        for r in results:
            year = f" ({r.created_at})" if r.created_at else ""
            source_label = f"[{r.source}]" if r.source else ""
            lines.append(f"- {r.title}{year} {source_label}")
            lines.append(f"  [View Paper]({r.url})")
            if r.snippet:
                lines.append(f"  Abstract: {truncate(r.snippet, 200)}")
            lines.append("")

        return "\n".join(lines)

    description = (
        f"Live, on-demand search of the latest external literature about {community_name}, "
        "newest first. It is slower than the local search (queries the web on demand; "
        "up to ~15 seconds). "
        f"Always try the local `search_{community_id}_papers` first. "
        "**Only call this tool after the user has explicitly confirmed they want a live "
        "literature search** (or explicitly asked to search the web / for the very latest "
        "papers). Do NOT call it automatically as a first step. When you call it, your "
        "message in that turn should first tell the user you are searching the latest "
        "literature and it may take a few seconds. "
        "**This is for DISCOVERY, not answering** - present results as references for "
        "further reading; do NOT use paper content to formulate answers."
    )

    return StructuredTool.from_function(
        func=search_papers_live_impl,
        name=f"search_{community_id}_papers_live",
        description=description,
    )


def create_search_docstrings_tool(
    community_id: str,
    community_name: str,
    language: str | None = None,
    citations: bool = False,
) -> BaseTool:
    """Create a tool for searching code docstrings for a community.

    Args:
        community_id: The community identifier (e.g., 'hed', 'bids', 'eeglab')
        community_name: Display name (e.g., 'HED', 'BIDS', 'EEGLAB')
        language: Optional language filter ('matlab' or 'python')
        citations: When True, and at least one result has a snippet to
            cite, return search_result blocks (one per symbol) instead of
            the formatted string. See create_search_papers_tool.

    Returns:
        A LangChain tool for searching code documentation
    """
    lang_help = ""
    if language:
        lang_help = f" Only searches {language.upper()} code."
    else:
        lang_help = " Searches both MATLAB and Python code."

    def search_docstrings_impl(query: str, limit: int = 5) -> str | list[dict[str, Any]]:
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

        if citations:
            blocks = _build_citation_blocks(
                results,
                source=lambda r: r.url,
                title=lambda r: r.title,
                text=lambda r: r.snippet,
            )
            if blocks:
                return blocks

        lines = [f"Code documentation in {community_name}:\n"]
        # `_make_snippet` appends "..." iff it truncated. Only nudge the LLM
        # toward the full-fetch tool when at least one result was actually
        # truncated; otherwise the hint encourages a wasteful follow-up.
        any_truncated = any(r.snippet.endswith("...") for r in results)
        for r in results:
            lines.append(f"- {r.title}")
            lines.append(f"  [View source on GitHub]({r.url})")
            if r.snippet:
                lines.append(f"  Documentation: {r.snippet}")
            lines.append("")
        if any_truncated:
            lines.append(
                f"One or more snippets were truncated. If the user is asking about "
                f"specific outputs, parameters, or examples, call "
                f"get_{community_id}_full_docstring with the symbol_name to "
                f"retrieve the stored docstring in full."
            )

        return "\n".join(lines)

    description = (
        f"Search {community_name} code documentation (docstrings from functions, classes, scripts).{lang_help} "
        "Use this to find how specific functions work, what parameters they accept, "
        "and see usage examples. Results include direct links to source code on GitHub. "
        "If the returned snippet is truncated (marked with `...`) and you need full "
        "details about outputs, parameters, or examples, follow up with "
        f"get_{community_id}_full_docstring."
    )

    return StructuredTool.from_function(
        func=search_docstrings_impl,
        name=f"search_{community_id}_code_docs",
        description=description,
    )


def create_get_full_docstring_tool(
    community_id: str,
    community_name: str,
    language: str | None = None,
    citations: bool = False,
) -> BaseTool:
    """Create a tool for fetching the complete docstring of a specific symbol.

    Used as a follow-up after `search_{community_id}_code_docs` when the
    returned snippet is truncated and the user is asking about specific
    outputs, parameters, or examples that fall past the snippet cap.

    Args:
        community_id: The community identifier (e.g., 'hed', 'eeglab')
        community_name: Display name (e.g., 'HED', 'EEGLAB')
        language: Optional language filter ('matlab' or 'python')
        citations: When True, and at least one match has a docstring to
            cite, return search_result blocks (one per match) instead of
            the formatted string. See create_search_papers_tool.

    Returns:
        A LangChain tool that returns the full docstring for a given symbol
    """

    def get_full_docstring_impl(symbol_name: str) -> str | list[dict[str, Any]]:
        """Fetch the stored docstring for a symbol (in full, up to 10K chars)."""
        if not _check_db_exists(community_id):
            return (
                f"Knowledge database for {community_name} not initialized. "
                "Run 'osa sync init' and 'osa sync docstrings' to populate it."
            )

        try:
            results = get_full_docstring(symbol_name, project=community_id, language=language)
        except sqlite3.OperationalError as e:
            # Case-insensitive match: SQLite phrasing has varied across
            # versions/drivers (e.g. "no such table: docstrings" vs
            # "No such table"). Lowercase before matching so a
            # future capitalization shift can't silently break the
            # friendly path and surface as a 500 to the user.
            if "no such table" in str(e).lower():
                logger.warning(
                    "Docstrings table not initialized for %s",
                    community_id,
                    extra={"symbol_name": symbol_name, "community": community_id},
                )
                return (
                    f"Knowledge database for {community_name} not initialized. "
                    f"Run 'osa sync docstrings --community {community_id}' to populate it."
                )
            logger.error(
                "Database operational error in get_full_docstring tool: %s",
                e,
                exc_info=True,
                extra={"symbol_name": symbol_name, "community": community_id},
            )
            raise
        except sqlite3.Error as e:
            logger.warning(
                "Database error in get_full_docstring tool: %s",
                e,
                extra={"symbol_name": symbol_name, "community": community_id},
            )
            raise

        if not results:
            return (
                f"No docstring found for symbol '{symbol_name}' in {community_name}. "
                f"Try search_{community_id}_code_docs first to find the correct symbol name."
            )

        if citations:
            blocks = _build_citation_blocks(
                results,
                source=lambda r: r.url,
                title=lambda r: r.title,
                text=lambda r: r.snippet,
            )
            if blocks:
                return blocks

        lines = [f"Full docstring(s) for '{symbol_name}' in {community_name}:\n"]
        for r in results:
            lines.append(f"## {r.title}")
            lines.append(f"[View source on GitHub]({r.url})\n")
            if r.snippet:
                lines.append(r.snippet)
            lines.append("")

        return "\n".join(lines)

    description = (
        f"Fetch the stored docstring in full (up to ~10K chars) for a specific "
        f"symbol in {community_name}. Use this as a follow-up to "
        f"search_{community_id}_code_docs when the snippet appears truncated "
        "(marked with `...`) and the user is asking about specific outputs, "
        "parameters, or examples. Takes an exact symbol_name (case-insensitive). "
        "Returns up to 5 matches when the same symbol appears in multiple "
        "files/repos."
    )

    return StructuredTool.from_function(
        func=get_full_docstring_impl,
        name=f"get_{community_id}_full_docstring",
        description=description,
    )


def create_search_faq_tool(
    community_id: str,
    community_name: str,
    list_names: list[str] | None = None,
    citations: bool = False,
) -> BaseTool:
    """Create a tool for searching FAQ entries from mailing lists.

    Args:
        community_id: The community identifier (e.g., 'eeglab', 'hed')
        community_name: Display name (e.g., 'EEGLAB', 'HED')
        list_names: Optional list of mailing list names for help text
        citations: When True, and at least one result has an answer to
            cite, return search_result blocks (one per FAQ entry, keyed by
            its thread URL) instead of the formatted string. See
            create_search_papers_tool.

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
    ) -> str | list[dict[str, Any]]:
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

        if citations:
            blocks = _build_citation_blocks(
                results,
                source=lambda r: r.thread_url,
                title=lambda r: r.question,
                text=lambda r: truncate(r.answer, _MAX_CITABLE_FAQ_ANSWER_CHARS),
            )
            if blocks:
                return blocks

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
            answer = truncate(result.answer, 500)
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


def create_search_discourse_tool(
    community_id: str,
    community_name: str,
) -> BaseTool:
    """Create a tool for searching Discourse forum topics.

    Not citable: its description tells the model this is for discovery,
    not for formulating answers (see create_knowledge_tools' `citations`
    docstring for the rule this follows).

    Args:
        community_id: The community identifier (e.g., 'mne')
        community_name: Display name (e.g., 'MNE-Python')

    Returns:
        A LangChain tool for searching Discourse forum topics
    """

    def search_discourse_impl(
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> str:
        """Search Discourse forum topics implementation."""
        if not _check_db_exists(community_id):
            return (
                f"Knowledge database for {community_name} not initialized. "
                "Run 'osa sync discourse' to populate it."
            )

        try:
            results = search_discourse_topics(
                query=query,
                project=community_id,
                limit=limit,
                category_name=category,
            )
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                logger.warning(
                    "Discourse table not initialized for %s",
                    community_id,
                    extra={"query": query, "community": community_id},
                )
                return (
                    f"Discourse database for {community_name} not initialized. "
                    f"Run 'osa sync discourse --community {community_id}' to populate it."
                )
            raise

        if not results:
            cat_str = f" (category: {category})" if category else ""
            return f"No forum topics found for '{query}'{cat_str}."

        lines = [f"Found {len(results)} forum topics:\n"]
        for i, r in enumerate(results, 1):
            cat_label = f" [{r.category_name}]" if r.category_name else ""
            lines.append(f"**{i}. {r.title}**{cat_label}")
            lines.append(f"  Replies: {r.reply_count} | Likes: {r.like_count} | Views: {r.views}")
            if r.snippet:
                lines.append(f"  {r.snippet}")
            if r.accepted_answer_snippet:
                lines.append(f"  Accepted answer: {r.accepted_answer_snippet}")
            lines.append(f"  [View topic]({r.url})\n")

        return "\n".join(lines)

    description = (
        f"Search {community_name} Discourse forum topics for community discussions and Q&A. "
        "**IMPORTANT: This is for DISCOVERY, not answering.** "
        "Use this to find forum discussions where users have asked similar questions. "
        'Present results as: "There\'s a related discussion on the forum, see: [link]" '
        "Do NOT use forum content to formulate authoritative answers."
    )

    return StructuredTool.from_function(
        func=search_discourse_impl,
        name=f"search_{community_id}_forum",
        description=description,
    )


def create_knowledge_tools(
    community_id: str,
    community_name: str,
    repos: list[str] | None = None,
    include_discussions: bool = True,
    include_recent: bool = True,
    include_papers: bool = True,
    include_live_papers: bool = False,
    include_docstrings: bool = False,
    docstrings_language: str | None = None,
    include_faq: bool = False,
    faq_list_names: list[str] | None = None,
    include_discourse: bool = False,
    citations: bool = False,
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
        include_live_papers: Include on-demand live paper search tool (default: False)
        include_docstrings: Include code docstring search tool (default: False)
        docstrings_language: Filter docstrings by language ('matlab' or 'python')
        include_faq: Include mailing list FAQ search tool (default: False)
        faq_list_names: List of mailing list names for FAQ help text
        include_discourse: Include Discourse forum search tool (default: False)
        citations: When True, every citable tool (retrieve_docs [wired in
            CommunityAssistant, not here], code docs, full docstring, FAQ)
            returns search_result blocks instead of formatted strings,
            enabling Claude's native inline citations. Anthropic-only.

            The rule for which tools are citable: a tool is citable if and
            only if its description permits answering from its content.
            Discussion search, recent activity, papers, live papers, and
            forum search are all deliberately excluded here because their
            descriptions say the opposite -- "This is for DISCOVERY, not
            answering" / "Do NOT use ... content to formulate answers".
            Making any of them citable would invite exactly what their own
            description forbids, and would make what OSA treats as an
            authoritative source a side effect of this phase rather than a
            deliberate product decision. Their tool descriptions never
            change based on this flag, on either path.

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

    if include_live_papers:
        tools.append(create_search_papers_live_tool(community_id, community_name))

    if include_docstrings:
        tools.append(
            create_search_docstrings_tool(
                community_id, community_name, docstrings_language, citations=citations
            )
        )
        tools.append(
            create_get_full_docstring_tool(
                community_id, community_name, docstrings_language, citations=citations
            )
        )

    if include_faq:
        tools.append(
            create_search_faq_tool(
                community_id, community_name, faq_list_names, citations=citations
            )
        )

    if include_discourse:
        tools.append(create_search_discourse_tool(community_id, community_name))

    return tools
