"""FTS5 search for knowledge sources.

Provides full-text search over GitHub discussions and papers.
These are for DISCOVERY, not answering - the agent should link
users to relevant discussions, not answer from them.
"""

import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass

from src.knowledge.db import get_connection

logger = logging.getLogger(__name__)


def _normalize_title_for_dedup(title: str) -> set[str]:
    """Normalize a paper title to a set of words for deduplication.

    This handles different Unicode representations, punctuation variants,
    and whitespace differences that might exist between the same paper
    indexed from different sources.

    Args:
        title: Raw paper title

    Returns:
        Set of normalized words for similarity comparison
    """
    # Unicode NFKC normalization - converts all Unicode variants to canonical form
    normalized = unicodedata.normalize("NFKC", title)

    # Lowercase for case-insensitive comparison
    normalized = normalized.lower()

    # Remove all punctuation and special characters, keep only alphanumeric and spaces
    normalized = re.sub(r"[^\w\s]", "", normalized)

    # Split into words and filter out very short words (less than 3 chars)
    words = {word for word in normalized.split() if len(word) >= 3}

    return words


def _titles_are_similar(
    title1_words: set[str], title2_words: set[str], threshold: float = 0.7
) -> bool:
    """Check if two titles are similar based on word overlap (Jaccard-like similarity).

    Args:
        title1_words: Set of words from first title
        title2_words: Set of words from second title
        threshold: Minimum similarity ratio (0.0 to 1.0), default 0.7 (70%)

    Returns:
        True if titles are similar enough to be considered duplicates
    """
    if not title1_words or not title2_words:
        return False

    # Calculate intersection and union
    intersection = len(title1_words & title2_words)
    union = len(title1_words | title2_words)

    if union == 0:
        return False

    similarity = intersection / union
    return similarity >= threshold


def _sanitize_fts5_query(query: str) -> str:
    """Sanitize user input for safe FTS5 queries.

    IMPORTANT: This function wraps ALL input in quotes, converting queries to
    exact phrase searches. This prevents FTS5 operator injection but also
    disables legitimate FTS5 features (AND/OR/NOT, wildcards, NEAR, etc.).

    For a production system with advanced search needs, consider implementing
    proper query parsing instead of blanket phrase conversion.

    Args:
        query: Raw user input

    Returns:
        Sanitized query safe for FTS5 MATCH (as a phrase search)
    """
    # Escape internal double quotes by doubling them
    escaped = query.replace('"', '""')
    # Wrap in quotes to treat entire input as phrase search
    return f'"{escaped}"'


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


def _is_pure_number_query(query: str) -> bool:
    """Check if the query is purely a number lookup with no useful text for FTS.

    Returns True for queries like "2022", "#500", "PR 2022", "issue #500"
    where FTS would produce poor results (searching for literal "#500" or "PR 10").
    """
    stripped = query.strip()
    # Pure number or hash-number
    if stripped.lstrip("#").isdigit():
        return True
    # Keyword + number with nothing else
    return bool(
        re.fullmatch(
            r"(?:pr|pull|issue|bug|feature)\s*#?\s*\d+", stripped, re.IGNORECASE
        )
    )


def _extract_number(query: str) -> int | None:
    """Extract an issue/PR number from a query string.

    Handles patterns like "2022", "#2022", "PR 2022", "issue #500".
    Pure numeric queries (e.g. "2022") are treated as number lookups first;
    this may match a PR/issue number rather than items mentioning the year.

    Returns:
        The extracted number, or None if no number pattern found.
    """
    # Strip and try common patterns
    stripped = query.strip().lstrip("#")
    # Direct number
    if stripped.isdigit():
        return int(stripped)
    # "PR 2022", "issue #500", "pull #2022", etc.
    m = re.match(
        r"(?:pr|pull|issue|bug|feature)\s*#?\s*(\d+)", query.strip(), re.IGNORECASE
    )
    if m:
        return int(m.group(1))
    return None


def _row_to_result(row: sqlite3.Row) -> SearchResult:
    """Convert a database row to a SearchResult."""
    first_message = row["first_message"] or ""
    snippet = first_message[:200].strip()
    if len(first_message) > 200:
        snippet += "..."
    return SearchResult(
        title=row["title"],
        url=row["url"],
        snippet=snippet,
        source="github",
        item_type=row["item_type"],
        status=row["status"],
        created_at=row["created_at"] or "",
    )


def search_github_items(
    query: str,
    project: str = "hed",
    limit: int = 10,
    item_type: str | None = None,
    status: str | None = None,
    repo: str | None = None,
) -> list[SearchResult]:
    """Search GitHub issues and PRs by number, title, or body text.

    When the query contains a number (e.g. "2022", "#500", "PR 2022"),
    results matching that number are returned first, followed by
    full-text search results.

    Args:
        query: Search phrase, PR/issue number, or keyword
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        limit: Maximum number of results
        item_type: Filter by 'issue' or 'pr'
        status: Filter by 'open' or 'closed'
        repo: Filter by repository name

    Returns:
        List of matching results, with number matches first
    """
    results = []
    seen_urls: set[str] = set()

    try:
        with get_connection(project) as conn:
            # Phase 1: Try direct number lookup
            number = _extract_number(query)
            is_pure_number = _is_pure_number_query(query)
            if number is not None:
                num_sql = """
                    SELECT title, url, first_message, item_type, status,
                           created_at, repo
                    FROM github_items WHERE number = ?
                """
                num_params: list[str | int] = [number]
                if item_type:
                    num_sql += " AND item_type = ?"
                    num_params.append(item_type)
                if status:
                    num_sql += " AND status = ?"
                    num_params.append(status)
                if repo:
                    num_sql += " AND repo = ?"
                    num_params.append(repo)

                for row in conn.execute(num_sql, num_params):
                    result = _row_to_result(row)
                    results.append(result)
                    seen_urls.add(result.url)

                if not results:
                    logger.debug("Number lookup for %d found no items", number)

            # Phase 2: Full-text search for remaining slots
            # Skip FTS for pure number queries (e.g. "#500", "PR 2022") since
            # the sanitized query would search for literal "#500" which won't
            # match anything useful in title/body text.
            remaining = limit - len(results)
            if remaining > 0 and not is_pure_number:
                fts_sql = """
                    SELECT g.title, g.url, g.first_message, g.item_type, g.status,
                           g.created_at, g.repo
                    FROM github_items_fts f
                    JOIN github_items g ON f.rowid = g.id
                    WHERE github_items_fts MATCH ?
                """
                fts_params: list[str | int] = [_sanitize_fts5_query(query)]

                if item_type:
                    fts_sql += " AND g.item_type = ?"
                    fts_params.append(item_type)
                if status:
                    fts_sql += " AND g.status = ?"
                    fts_params.append(status)
                if repo:
                    fts_sql += " AND g.repo = ?"
                    fts_params.append(repo)

                fts_sql += " ORDER BY rank LIMIT ?"
                fts_params.append(remaining)

                for row in conn.execute(fts_sql, fts_params):
                    if row["url"] not in seen_urls:
                        results.append(_row_to_result(row))
                        seen_urls.add(row["url"])

    except sqlite3.OperationalError as e:
        # Infrastructure failure (corruption, disk full, permissions) - must propagate
        logger.error(
            "Database operational error during search: %s",
            e,
            exc_info=True,
            extra={"query": query, "project": project},
        )
        raise  # Let API layer return 500, not empty results
    except sqlite3.Error as e:
        # Other database errors - still raise for debugging
        logger.warning("Database error during search '%s': %s", query, e)
        raise

    return results


def search_papers(
    query: str,
    project: str = "hed",
    limit: int = 10,
    source: str | None = None,
) -> list[SearchResult]:
    """Search papers using phrase matching.

    Args:
        query: Search phrase (treated as exact phrase, not FTS5 operators)
        project: Assistant/project name for database isolation. Defaults to 'hed'.
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

    # Fetch more results than needed to allow for deduplication
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit * 3)

    results = []
    seen_titles: list[set[str]] = []  # List of word sets for fuzzy matching
    try:
        with get_connection(project) as conn:
            # Sanitize user query to prevent FTS5 injection
            safe_query = _sanitize_fts5_query(query)
            params[0] = safe_query

            for row in conn.execute(sql, params):
                # Deduplicate by fuzzy title matching (>70% word overlap)
                title_words = _normalize_title_for_dedup(row["title"])

                # Check if this title is similar to any we've already seen
                is_duplicate = False
                for seen_words in seen_titles:
                    if _titles_are_similar(title_words, seen_words):
                        is_duplicate = True
                        break

                if is_duplicate:
                    continue
                seen_titles.append(title_words)

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

                # Stop once we have enough unique results
                if len(results) >= limit:
                    break
    except sqlite3.OperationalError as e:
        # Infrastructure failure (corruption, disk full, permissions) - must propagate
        logger.error(
            "Database operational error during paper search: %s",
            e,
            exc_info=True,
            extra={"query": query, "project": project},
        )
        raise  # Let API layer return 500, not empty results
    except sqlite3.Error as e:
        # Other database errors - still raise for debugging
        logger.warning("Database error during paper search '%s': %s", query, e)
        raise

    return results


def search_all(
    query: str,
    project: str = "hed",
    limit: int = 10,
) -> dict[str, list[SearchResult]]:
    """Search both GitHub items and papers.

    Args:
        query: Search query
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        limit: Maximum results per category

    Returns:
        Dict with 'github' and 'papers' keys containing results
    """
    return {
        "github": search_github_items(query, project=project, limit=limit),
        "papers": search_papers(query, project=project, limit=limit),
    }


def list_recent_github_items(
    project: str = "hed",
    limit: int = 10,
    item_type: str | None = None,
    status: str | None = None,
    repo: str | None = None,
) -> list[SearchResult]:
    """List recent GitHub issues and PRs ordered by creation date.

    Unlike search_github_items which searches by text, this function
    simply lists the most recent items.

    Args:
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        limit: Maximum number of results
        item_type: Filter by 'issue' or 'pr'
        status: Filter by 'open' or 'closed'
        repo: Filter by repository name (e.g., 'hed-standard/hed-javascript')

    Returns:
        List of recent items, ordered by creation date (newest first)
    """
    sql = """
        SELECT title, url, first_message, item_type, status, created_at, repo
        FROM github_items
        WHERE 1=1
    """
    params: list[str | int] = []

    if item_type:
        sql += " AND item_type = ?"
        params.append(item_type)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if repo:
        sql += " AND repo = ?"
        params.append(repo)

    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    results = []
    try:
        with get_connection(project) as conn:
            for row in conn.execute(sql, params):
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
    except sqlite3.OperationalError as e:
        # Infrastructure failure (corruption, disk full, permissions) - must propagate
        logger.error(
            "Database operational error listing recent items: %s",
            e,
            exc_info=True,
            extra={"project": project},
        )
        raise  # Let API layer return 500, not empty results
    except sqlite3.Error as e:
        # Other database errors - still raise for debugging
        logger.warning("Database error listing recent items: %s", e)
        raise

    return results


def search_docstrings(
    query: str,
    project: str = "hed",
    limit: int = 10,
    language: str | None = None,
    repo: str | None = None,
) -> list[SearchResult]:
    """Search code docstrings using phrase matching.

    Args:
        query: Search phrase (treated as exact phrase, not FTS5 operators)
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        limit: Maximum number of results
        language: Filter by 'matlab' or 'python'
        repo: Filter by repository name

    Returns:
        List of matching results with GitHub source links, ordered by relevance
    """
    sql = """
        SELECT d.symbol_name, d.docstring, d.file_path, d.repo,
               d.language, d.symbol_type, d.line_number, d.branch
        FROM docstrings_fts f
        JOIN docstrings d ON f.rowid = d.id
        WHERE docstrings_fts MATCH ?
    """
    params: list[str | int] = [query]

    if language:
        sql += " AND d.language = ?"
        params.append(language)
    if repo:
        sql += " AND d.repo = ?"
        params.append(repo)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    results = []
    try:
        with get_connection(project) as conn:
            # Sanitize user query to prevent FTS5 injection
            safe_query = _sanitize_fts5_query(query)
            params[0] = safe_query

            for row in conn.execute(sql, params):
                # Create snippet from docstring (first 200 chars)
                docstring = row["docstring"] or ""
                snippet = docstring[:200].strip()
                if len(docstring) > 200:
                    snippet += "..."

                # Build GitHub URL to the specific line
                file_path = row["file_path"]
                repo_name = row["repo"]
                line_number = row["line_number"]
                branch = row["branch"] or "main"  # Fallback to 'main' if NULL

                # Use repo-specific branch (e.g., 'develop', 'main', 'master')
                github_url = f"https://github.com/{repo_name}/blob/{branch}/{file_path}"
                if line_number:
                    github_url += f"#L{line_number}"

                # Format title as "symbol_name (type) - file_path"
                symbol_name = row["symbol_name"]
                symbol_type = row["symbol_type"]
                title = f"{symbol_name} ({symbol_type}) - {file_path}"

                results.append(
                    SearchResult(
                        title=title,
                        url=github_url,
                        snippet=snippet,
                        source=row["language"],
                        item_type=symbol_type,
                        status="documented",
                        created_at="",
                    )
                )
    except sqlite3.OperationalError as e:
        # Infrastructure failure (corruption, disk full, permissions) - must propagate
        logger.error(
            "Database operational error during docstring search: %s",
            e,
            exc_info=True,
            extra={"query": query, "project": project},
        )
        raise  # Let API layer return 500, not empty results
    except sqlite3.Error as e:
        # Other database errors - still raise for debugging
        logger.warning("Database error during docstring search '%s': %s", query, e)
        raise

    return results


@dataclass
class FAQResult:
    """A FAQ search result from mailing list archives."""

    question: str
    answer: str
    thread_url: str
    tags: list[str]
    category: str
    quality_score: float
    message_count: int
    first_message_date: str


def search_faq_entries(
    query: str,
    project: str = "eeglab",
    limit: int = 5,
    list_name: str | None = None,
    category: str | None = None,
    min_quality: float = 0.0,
) -> list[FAQResult]:
    """Search FAQ entries using phrase matching.

    Args:
        query: Search phrase (treated as exact phrase, not FTS5 operators)
        project: Community ID for database isolation. Defaults to 'eeglab'.
        limit: Maximum number of results
        list_name: Filter by mailing list name
        category: Filter by category (e.g., 'troubleshooting', 'how-to')
        min_quality: Minimum quality score (0.0-1.0)

    Returns:
        List of matching FAQ entries, ordered by quality score and relevance
    """
    sql = """
        SELECT f.question, f.answer, f.thread_url, f.tags, f.category,
               f.quality_score, f.message_count, f.first_message_date
        FROM faq_entries_fts fts
        JOIN faq_entries f ON fts.rowid = f.id
        WHERE faq_entries_fts MATCH ?
    """
    params: list[str | int | float] = [query]

    if list_name:
        sql += " AND f.list_name = ?"
        params.append(list_name)

    if category:
        sql += " AND f.category = ?"
        params.append(category)

    if min_quality > 0:
        sql += " AND f.quality_score >= ?"
        params.append(min_quality)

    sql += " ORDER BY f.quality_score DESC, rank LIMIT ?"
    params.append(limit)

    results = []
    try:
        with get_connection(project) as conn:
            # Sanitize user query to prevent FTS5 injection
            safe_query = _sanitize_fts5_query(query)
            params[0] = safe_query

            for row in conn.execute(sql, params):
                # Parse tags from JSON
                import json

                tags = json.loads(row["tags"]) if row["tags"] else []

                results.append(
                    FAQResult(
                        question=row["question"],
                        answer=row["answer"],
                        thread_url=row["thread_url"],
                        tags=tags,
                        category=row["category"],
                        quality_score=row["quality_score"],
                        message_count=row["message_count"],
                        first_message_date=row["first_message_date"] or "",
                    )
                )
    except sqlite3.OperationalError as e:
        # Infrastructure failure (corruption, disk full, permissions) - must propagate
        logger.error(
            "Database operational error during FAQ search: %s",
            e,
            exc_info=True,
            extra={"query": query, "project": project},
        )
        raise  # Let API layer return 500, not empty results
    except sqlite3.Error as e:
        # Other database errors - still raise for debugging
        logger.warning("Database error during FAQ search '%s': %s", query, e)
        raise

    return results
