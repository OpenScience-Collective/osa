"""FTS5 search for knowledge sources.

Provides full-text search over GitHub discussions and papers.
These are for DISCOVERY, not answering - the agent should link
users to relevant discussions, not answer from them.
"""

import logging
from dataclasses import dataclass

from src.knowledge.db import get_connection

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A search result from the knowledge database."""

    title: str
    url: str
    snippet: str
    source: str  # 'github' or paper source name
    item_type: str | None  # 'issue', 'pr', or None for papers
    status: str  # 'open', 'closed', or 'published'
    created_at: str


def search_github_items(
    query: str,
    limit: int = 10,
    item_type: str | None = None,
    status: str | None = None,
    repo: str | None = None,
) -> list[SearchResult]:
    """Search GitHub issues and PRs using FTS5.

    Args:
        query: Search query (FTS5 syntax supported, e.g., "validation AND error")
        limit: Maximum number of results
        item_type: Filter by 'issue' or 'pr'
        status: Filter by 'open' or 'closed'
        repo: Filter by repository name

    Returns:
        List of matching results, ordered by relevance
    """
    # Build SQL query with optional filters
    sql = """
        SELECT g.title, g.url, g.first_message, g.item_type, g.status,
               g.created_at, g.repo
        FROM github_items_fts f
        JOIN github_items g ON f.rowid = g.id
        WHERE github_items_fts MATCH ?
    """
    params: list[str | int] = [query]

    if item_type:
        sql += " AND g.item_type = ?"
        params.append(item_type)
    if status:
        sql += " AND g.status = ?"
        params.append(status)
    if repo:
        sql += " AND g.repo = ?"
        params.append(repo)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    results = []
    try:
        with get_connection() as conn:
            for row in conn.execute(sql, params):
                # Create snippet from first_message (first 200 chars)
                first_message = row["first_message"] or ""
                snippet = first_message[:200].strip()
                if len(first_message) > 200:
                    snippet += "..."

                results.append(
                    SearchResult(
                        title=row["title"],
                        url=row["url"],
                        snippet=snippet,
                        source="github",
                        item_type=row["item_type"],
                        status=row["status"],
                        created_at=row["created_at"] or "",
                    )
                )
    except Exception as e:
        # FTS5 query errors (e.g., syntax errors) should not crash
        logger.warning("FTS5 search error for '%s': %s", query, e)

    return results


def search_papers(
    query: str,
    limit: int = 10,
    source: str | None = None,
) -> list[SearchResult]:
    """Search papers using FTS5.

    Args:
        query: Search query (FTS5 syntax supported)
        limit: Maximum number of results
        source: Filter by source ('openalex', 'semanticscholar', 'pubmed')

    Returns:
        List of matching results, ordered by relevance
    """
    sql = """
        SELECT p.title, p.url, p.first_message, p.source, p.created_at
        FROM papers_fts f
        JOIN papers p ON f.rowid = p.id
        WHERE papers_fts MATCH ?
    """
    params: list[str | int] = [query]

    if source:
        sql += " AND p.source = ?"
        params.append(source)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    results = []
    try:
        with get_connection() as conn:
            for row in conn.execute(sql, params):
                # Create snippet from abstract (first 200 chars)
                first_message = row["first_message"] or ""
                snippet = first_message[:200].strip()
                if len(first_message) > 200:
                    snippet += "..."

                results.append(
                    SearchResult(
                        title=row["title"],
                        url=row["url"],
                        snippet=snippet,
                        source=row["source"],
                        item_type=None,
                        status="published",
                        created_at=row["created_at"] or "",
                    )
                )
    except Exception as e:
        logger.warning("FTS5 search error for '%s': %s", query, e)

    return results


def search_all(
    query: str,
    limit: int = 10,
) -> dict[str, list[SearchResult]]:
    """Search both GitHub items and papers.

    Args:
        query: Search query
        limit: Maximum results per category

    Returns:
        Dict with 'github' and 'papers' keys containing results
    """
    return {
        "github": search_github_items(query, limit=limit),
        "papers": search_papers(query, limit=limit),
    }
