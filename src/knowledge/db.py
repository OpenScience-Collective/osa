"""SQLite + FTS5 database for knowledge sources.

Stores minimal metadata about GitHub discussions and papers:
- Title
- First message (body/abstract)
- Status (open/closed/published)
- URL
- Created date

Design: Discovery, not knowledge. These are pointers to discussions,
not authoritative sources for answering questions.
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from src.cli.config import get_data_dir

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
-- GitHub issues and PRs from HED repositories
CREATE TABLE IF NOT EXISTS github_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    item_type TEXT NOT NULL,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    first_message TEXT,
    status TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    UNIQUE(repo, item_type, number)
);

-- FTS5 virtual table for full-text search on GitHub items
CREATE VIRTUAL TABLE IF NOT EXISTS github_items_fts USING fts5(
    title,
    first_message,
    content='github_items',
    content_rowid='id'
);

-- Triggers to keep FTS in sync with github_items
CREATE TRIGGER IF NOT EXISTS github_items_ai AFTER INSERT ON github_items BEGIN
    INSERT INTO github_items_fts(rowid, title, first_message)
    VALUES (new.id, new.title, new.first_message);
END;

CREATE TRIGGER IF NOT EXISTS github_items_ad AFTER DELETE ON github_items BEGIN
    INSERT INTO github_items_fts(github_items_fts, rowid, title, first_message)
    VALUES('delete', old.id, old.title, old.first_message);
END;

CREATE TRIGGER IF NOT EXISTS github_items_au AFTER UPDATE ON github_items BEGIN
    INSERT INTO github_items_fts(github_items_fts, rowid, title, first_message)
    VALUES('delete', old.id, old.title, old.first_message);
    INSERT INTO github_items_fts(rowid, title, first_message)
    VALUES (new.id, new.title, new.first_message);
END;

-- Papers from OpenALEX, Semantic Scholar, and PubMed Central
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    first_message TEXT,
    status TEXT NOT NULL DEFAULT 'published',
    url TEXT NOT NULL,
    created_at TEXT,
    synced_at TEXT NOT NULL,
    UNIQUE(source, external_id)
);

-- FTS5 virtual table for full-text search on papers
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title,
    first_message,
    content='papers',
    content_rowid='id'
);

-- Triggers to keep FTS in sync with papers
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, first_message)
    VALUES (new.id, new.title, new.first_message);
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, first_message)
    VALUES('delete', old.id, old.title, old.first_message);
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, first_message)
    VALUES('delete', old.id, old.title, old.first_message);
    INSERT INTO papers_fts(rowid, title, first_message)
    VALUES (new.id, new.title, new.first_message);
END;

-- Sync metadata for tracking last sync time
CREATE TABLE IF NOT EXISTS sync_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    last_sync_at TEXT NOT NULL,
    items_synced INTEGER DEFAULT 0,
    UNIQUE(source_type, source_name)
);

-- Docstrings extracted from source code
CREATE TABLE IF NOT EXISTS docstrings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT NOT NULL,
    symbol_name TEXT NOT NULL,
    symbol_type TEXT NOT NULL,
    docstring TEXT NOT NULL,
    line_number INTEGER,
    synced_at TEXT NOT NULL,
    UNIQUE(repo, file_path, symbol_name)
);

-- FTS5 virtual table for full-text search on docstrings
CREATE VIRTUAL TABLE IF NOT EXISTS docstrings_fts USING fts5(
    symbol_name,
    docstring,
    content='docstrings',
    content_rowid='id'
);

-- Triggers to keep FTS in sync with docstrings
CREATE TRIGGER IF NOT EXISTS docstrings_ai AFTER INSERT ON docstrings BEGIN
    INSERT INTO docstrings_fts(rowid, symbol_name, docstring)
    VALUES (new.id, new.symbol_name, new.docstring);
END;

CREATE TRIGGER IF NOT EXISTS docstrings_ad AFTER DELETE ON docstrings BEGIN
    INSERT INTO docstrings_fts(docstrings_fts, rowid, symbol_name, docstring)
    VALUES('delete', old.id, old.symbol_name, old.docstring);
END;

CREATE TRIGGER IF NOT EXISTS docstrings_au AFTER UPDATE ON docstrings BEGIN
    INSERT INTO docstrings_fts(docstrings_fts, rowid, symbol_name, docstring)
    VALUES('delete', old.id, old.symbol_name, old.docstring);
    INSERT INTO docstrings_fts(rowid, symbol_name, docstring)
    VALUES (new.id, new.symbol_name, new.docstring);
END;

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_github_items_repo ON github_items(repo);
CREATE INDEX IF NOT EXISTS idx_github_items_status ON github_items(status);
CREATE INDEX IF NOT EXISTS idx_github_items_type ON github_items(item_type);
CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source);
CREATE INDEX IF NOT EXISTS idx_docstrings_repo ON docstrings(repo);
CREATE INDEX IF NOT EXISTS idx_docstrings_language ON docstrings(language);
"""


def get_db_path(project: str = "hed") -> Path:
    """Get path to knowledge database for a project.

    Each assistant/project has its own isolated knowledge database.

    Args:
        project: Assistant/project name (e.g., 'hed', 'bids', 'eeglab').
                 Defaults to 'hed' for backward compatibility.

    Returns:
        Path to the project's knowledge database.

    Raises:
        ValueError: If project name contains invalid characters.
    """
    # Validate project name to prevent path traversal
    if not project or not project.replace("-", "").replace("_", "").isalnum():
        raise ValueError(
            f"Invalid project name: {project}. "
            "Use only alphanumeric characters, hyphens, and underscores."
        )

    return get_data_dir() / "knowledge" / f"{project}.db"


@contextmanager
def get_connection(project: str = "hed") -> Iterator[sqlite3.Connection]:
    """Get database connection with row factory.

    Args:
        project: Assistant/project name. Defaults to 'hed'.

    Usage:
        with get_connection() as conn:
            cursor = conn.execute("SELECT * FROM github_items")
            for row in cursor:
                print(row["title"])

        # For a specific project:
        with get_connection("bids") as conn:
            ...
    """
    db_path = get_db_path(project)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(project: str = "hed") -> None:
    """Initialize database schema for a project.

    Creates all tables, FTS5 virtual tables, triggers, and indexes.
    Safe to call multiple times (uses IF NOT EXISTS).

    Args:
        project: Assistant/project name. Defaults to 'hed'.
    """
    with get_connection(project) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    logger.info("Knowledge database initialized at %s", get_db_path(project))


def _now_iso() -> str:
    """Get current UTC time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def upsert_github_item(
    conn: sqlite3.Connection,
    *,
    repo: str,
    item_type: str,
    number: int,
    title: str,
    first_message: str | None,
    status: str,
    url: str,
    created_at: str,
) -> None:
    """Insert or update a GitHub item.

    Args:
        conn: Database connection
        repo: Repository in owner/name format (e.g., 'hed-standard/hed-specification')
        item_type: 'issue' or 'pr'
        number: Issue/PR number
        title: Title of the issue/PR
        first_message: Body/description (first post only, NOT replies)
        status: 'open' or 'closed'
        url: URL to the issue/PR
        created_at: ISO 8601 creation timestamp
    """
    # Limit first_message size to prevent bloat
    if first_message and len(first_message) > 5000:
        first_message = first_message[:5000]

    conn.execute(
        """
        INSERT INTO github_items (repo, item_type, number, title, first_message,
                                  status, url, created_at, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo, item_type, number) DO UPDATE SET
            title=excluded.title,
            first_message=excluded.first_message,
            status=excluded.status,
            synced_at=excluded.synced_at
        """,
        (repo, item_type, number, title, first_message, status, url, created_at, _now_iso()),
    )


def upsert_paper(
    conn: sqlite3.Connection,
    *,
    source: str,
    external_id: str,
    title: str,
    first_message: str | None,
    url: str,
    created_at: str | None,
) -> None:
    """Insert or update a paper.

    Args:
        conn: Database connection
        source: 'openalex', 'semanticscholar', or 'pubmed'
        external_id: External ID from the source
        title: Paper title
        first_message: Abstract (limited to ~2000 chars)
        url: URL to the paper (DOI or source URL)
        created_at: Publication date (ISO 8601 or year string)
    """
    # Limit first_message size
    if first_message and len(first_message) > 2000:
        first_message = first_message[:2000]

    conn.execute(
        """
        INSERT INTO papers (source, external_id, title, first_message,
                            status, url, created_at, synced_at)
        VALUES (?, ?, ?, ?, 'published', ?, ?, ?)
        ON CONFLICT(source, external_id) DO UPDATE SET
            title=excluded.title,
            first_message=excluded.first_message,
            synced_at=excluded.synced_at
        """,
        (source, external_id, title, first_message, url, created_at, _now_iso()),
    )


def upsert_docstring(
    conn: sqlite3.Connection,
    *,
    repo: str,
    file_path: str,
    language: str,
    symbol_name: str,
    symbol_type: str,
    docstring: str,
    line_number: int | None = None,
) -> None:
    """Insert or update a docstring entry.

    Args:
        conn: Database connection
        repo: Repository in owner/name format
        file_path: Relative path from repo root
        language: 'matlab' or 'python'
        symbol_name: Function/class/method name
        symbol_type: 'function', 'class', 'method', 'script', 'module'
        docstring: Full docstring text
        line_number: Starting line in source file (optional)
    """
    # Limit docstring size to prevent bloat
    if len(docstring) > 10000:
        docstring = docstring[:10000]

    conn.execute(
        """
        INSERT INTO docstrings (repo, file_path, language, symbol_name,
                                symbol_type, docstring, line_number, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo, file_path, symbol_name) DO UPDATE SET
            docstring=excluded.docstring,
            symbol_type=excluded.symbol_type,
            line_number=excluded.line_number,
            synced_at=excluded.synced_at
        """,
        (repo, file_path, language, symbol_name, symbol_type, docstring, line_number, _now_iso()),
    )


def get_last_sync(source_type: str, source_name: str, project: str = "hed") -> str | None:
    """Get last sync time for a source.

    Args:
        source_type: 'github' or 'papers'
        source_name: Repository name or paper source name
        project: Assistant/project name. Defaults to 'hed'.

    Returns:
        ISO 8601 timestamp of last sync, or None if never synced
    """
    with get_connection(project) as conn:
        row = conn.execute(
            "SELECT last_sync_at FROM sync_metadata WHERE source_type = ? AND source_name = ?",
            (source_type, source_name),
        ).fetchone()
        return row["last_sync_at"] if row else None


def update_sync_metadata(
    source_type: str, source_name: str, items_synced: int, project: str = "hed"
) -> None:
    """Update sync metadata for a source.

    Args:
        source_type: 'github' or 'papers'
        source_name: Repository name or paper source name
        items_synced: Number of items synced in this run
        project: Assistant/project name. Defaults to 'hed'.
    """
    with get_connection(project) as conn:
        conn.execute(
            """
            INSERT INTO sync_metadata (source_type, source_name, last_sync_at, items_synced)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_type, source_name) DO UPDATE SET
                last_sync_at=excluded.last_sync_at,
                items_synced=excluded.items_synced
            """,
            (source_type, source_name, _now_iso(), items_synced),
        )
        conn.commit()


def get_stats(project: str = "hed") -> dict[str, int]:
    """Get database statistics for a project.

    Args:
        project: Assistant/project name. Defaults to 'hed'.

    Returns:
        Dict with counts for each category
    """
    with get_connection(project) as conn:
        stats = {}

        # GitHub stats
        stats["github_total"] = conn.execute("SELECT COUNT(*) FROM github_items").fetchone()[0]
        stats["github_issues"] = conn.execute(
            "SELECT COUNT(*) FROM github_items WHERE item_type='issue'"
        ).fetchone()[0]
        stats["github_prs"] = conn.execute(
            "SELECT COUNT(*) FROM github_items WHERE item_type='pr'"
        ).fetchone()[0]
        stats["github_open"] = conn.execute(
            "SELECT COUNT(*) FROM github_items WHERE status='open'"
        ).fetchone()[0]

        # Paper stats
        stats["papers_total"] = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        stats["papers_openalex"] = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE source='openalex'"
        ).fetchone()[0]
        stats["papers_semanticscholar"] = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE source='semanticscholar'"
        ).fetchone()[0]
        stats["papers_pubmed"] = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE source='pubmed'"
        ).fetchone()[0]

        # Docstring stats
        stats["docstrings_total"] = conn.execute("SELECT COUNT(*) FROM docstrings").fetchone()[0]
        stats["docstrings_matlab"] = conn.execute(
            "SELECT COUNT(*) FROM docstrings WHERE language='matlab'"
        ).fetchone()[0]
        stats["docstrings_python"] = conn.execute(
            "SELECT COUNT(*) FROM docstrings WHERE language='python'"
        ).fetchone()[0]

        return stats
