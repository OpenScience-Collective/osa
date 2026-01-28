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
    branch TEXT NOT NULL DEFAULT 'main',
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

-- Raw mailing list messages (complete archive)
CREATE TABLE IF NOT EXISTS mailing_list_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_name TEXT NOT NULL,
    message_id TEXT NOT NULL,
    thread_id TEXT,
    subject TEXT NOT NULL,
    author TEXT,
    author_email TEXT,
    date TEXT NOT NULL,
    body TEXT,
    in_reply_to TEXT,
    url TEXT NOT NULL,
    year INTEGER NOT NULL,
    synced_at TEXT NOT NULL,
    UNIQUE(list_name, message_id)
);

-- FTS5 for message search
CREATE VIRTUAL TABLE IF NOT EXISTS mailing_list_messages_fts USING fts5(
    subject,
    body,
    author,
    content='mailing_list_messages',
    content_rowid='id'
);

-- Triggers to keep FTS in sync with mailing_list_messages
CREATE TRIGGER IF NOT EXISTS mailing_list_messages_ai AFTER INSERT ON mailing_list_messages BEGIN
    INSERT INTO mailing_list_messages_fts(rowid, subject, body, author)
    VALUES (new.id, new.subject, new.body, new.author);
END;

CREATE TRIGGER IF NOT EXISTS mailing_list_messages_ad AFTER DELETE ON mailing_list_messages BEGIN
    INSERT INTO mailing_list_messages_fts(mailing_list_messages_fts, rowid, subject, body, author)
    VALUES('delete', old.id, old.subject, old.body, old.author);
END;

CREATE TRIGGER IF NOT EXISTS mailing_list_messages_au AFTER UPDATE ON mailing_list_messages BEGIN
    INSERT INTO mailing_list_messages_fts(mailing_list_messages_fts, rowid, subject, body, author)
    VALUES('delete', old.id, old.subject, old.body, old.author);
    INSERT INTO mailing_list_messages_fts(rowid, subject, body, author)
    VALUES (new.id, new.subject, new.body, new.author);
END;

-- FAQ summaries (LLM-generated from threads)
CREATE TABLE IF NOT EXISTS faq_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_name TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    thread_url TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    tags TEXT,
    category TEXT,
    message_count INTEGER DEFAULT 1,
    participant_count INTEGER DEFAULT 1,
    first_message_date TEXT,
    quality_score REAL,
    summarized_at TEXT NOT NULL,
    summary_model TEXT,
    UNIQUE(list_name, thread_id)
);

-- FTS5 for FAQ search
CREATE VIRTUAL TABLE IF NOT EXISTS faq_entries_fts USING fts5(
    question,
    answer,
    tags,
    content='faq_entries',
    content_rowid='id'
);

-- Triggers to keep FTS in sync with faq_entries
CREATE TRIGGER IF NOT EXISTS faq_entries_ai AFTER INSERT ON faq_entries BEGIN
    INSERT INTO faq_entries_fts(rowid, question, answer, tags)
    VALUES (new.id, new.question, new.answer, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS faq_entries_ad AFTER DELETE ON faq_entries BEGIN
    INSERT INTO faq_entries_fts(faq_entries_fts, rowid, question, answer, tags)
    VALUES('delete', old.id, old.question, old.answer, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS faq_entries_au AFTER UPDATE ON faq_entries BEGIN
    INSERT INTO faq_entries_fts(faq_entries_fts, rowid, question, answer, tags)
    VALUES('delete', old.id, old.question, old.answer, old.tags);
    INSERT INTO faq_entries_fts(rowid, question, answer, tags)
    VALUES (new.id, new.question, new.answer, new.tags);
END;

-- Track summarization progress
CREATE TABLE IF NOT EXISTS summarization_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_name TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_reason TEXT,
    token_count INTEGER,
    cost_estimate REAL,
    attempted_at TEXT,
    UNIQUE(list_name, thread_id)
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_github_items_repo ON github_items(repo);
CREATE INDEX IF NOT EXISTS idx_github_items_status ON github_items(status);
CREATE INDEX IF NOT EXISTS idx_github_items_type ON github_items(item_type);
CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source);
CREATE INDEX IF NOT EXISTS idx_docstrings_repo ON docstrings(repo);
CREATE INDEX IF NOT EXISTS idx_docstrings_language ON docstrings(language);
CREATE INDEX IF NOT EXISTS idx_messages_list ON mailing_list_messages(list_name);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON mailing_list_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_year ON mailing_list_messages(year);
CREATE INDEX IF NOT EXISTS idx_messages_date ON mailing_list_messages(date);
CREATE INDEX IF NOT EXISTS idx_faq_list ON faq_entries(list_name);
CREATE INDEX IF NOT EXISTS idx_faq_category ON faq_entries(category);
CREATE INDEX IF NOT EXISTS idx_faq_quality ON faq_entries(quality_score);
CREATE INDEX IF NOT EXISTS idx_summarization_status ON summarization_status(list_name, status);
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


def _migrate_db(conn: sqlite3.Connection) -> None:
    """Run database migrations for schema changes.

    Handles adding new columns to existing tables that were created
    before the column was added to the schema.
    """
    # Migration: Add branch column to docstrings table (added 2026-01-27)
    try:
        # Check if branch column exists
        cursor = conn.execute("PRAGMA table_info(docstrings)")
        columns = [row[1] for row in cursor.fetchall()]

        if "branch" not in columns:
            logger.info("Migrating docstrings table: adding branch column")
            conn.execute("ALTER TABLE docstrings ADD COLUMN branch TEXT NOT NULL DEFAULT 'main'")
            conn.commit()
            logger.info("Migration complete: branch column added to docstrings")
    except sqlite3.OperationalError as e:
        # Table doesn't exist yet - this is fine, schema will create it
        logger.debug("Docstrings table not found during migration (will be created): %s", e)


def init_db(project: str = "hed") -> None:
    """Initialize database schema for a project.

    Creates all tables, FTS5 virtual tables, triggers, and indexes.
    Safe to call multiple times (uses IF NOT EXISTS).
    Runs migrations to handle schema changes for existing databases.

    Args:
        project: Assistant/project name. Defaults to 'hed'.
    """
    with get_connection(project) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()

        # Run migrations for existing databases
        _migrate_db(conn)

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
    branch: str = "main",
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
        branch: Git branch name (e.g., 'main', 'develop', 'master')
    """
    # Limit docstring size to prevent bloat
    if len(docstring) > 10000:
        docstring = docstring[:10000]

    conn.execute(
        """
        INSERT INTO docstrings (repo, file_path, language, symbol_name,
                                symbol_type, docstring, line_number, branch, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo, file_path, symbol_name) DO UPDATE SET
            docstring=excluded.docstring,
            symbol_type=excluded.symbol_type,
            line_number=excluded.line_number,
            branch=excluded.branch,
            synced_at=excluded.synced_at
        """,
        (
            repo,
            file_path,
            language,
            symbol_name,
            symbol_type,
            docstring,
            line_number,
            branch,
            _now_iso(),
        ),
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

        # Mailing list stats
        stats["mailing_list_total"] = conn.execute(
            "SELECT COUNT(*) FROM mailing_list_messages"
        ).fetchone()[0]
        stats["faq_total"] = conn.execute("SELECT COUNT(*) FROM faq_entries").fetchone()[0]

        return stats


def upsert_mailing_list_message(
    conn: sqlite3.Connection,
    *,
    list_name: str,
    message_id: str,
    thread_id: str | None,
    subject: str,
    author: str | None,
    author_email: str | None,
    date: str,
    body: str | None,
    in_reply_to: str | None,
    url: str,
    year: int,
) -> None:
    """Insert or update a mailing list message.

    Args:
        conn: Database connection
        list_name: Mailing list identifier (e.g., 'eeglablist')
        message_id: Unique message identifier
        thread_id: Thread identifier (first message_id in thread)
        subject: Message subject
        author: Author name
        author_email: Author email
        date: ISO 8601 timestamp
        body: Message content in markdown
        in_reply_to: Parent message_id
        url: URL to original message
        year: Year for partitioning
    """
    # Limit body size to prevent bloat
    if body and len(body) > 10000:
        body = body[:10000]

    conn.execute(
        """
        INSERT INTO mailing_list_messages (list_name, message_id, thread_id, subject,
                                           author, author_email, date, body, in_reply_to,
                                           url, year, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(list_name, message_id) DO UPDATE SET
            thread_id=excluded.thread_id,
            subject=excluded.subject,
            author=excluded.author,
            author_email=excluded.author_email,
            date=excluded.date,
            body=excluded.body,
            in_reply_to=excluded.in_reply_to,
            synced_at=excluded.synced_at
        """,
        (
            list_name,
            message_id,
            thread_id,
            subject,
            author,
            author_email,
            date,
            body,
            in_reply_to,
            url,
            year,
            _now_iso(),
        ),
    )


def upsert_faq_entry(
    conn: sqlite3.Connection,
    *,
    list_name: str,
    thread_id: str,
    thread_url: str,
    question: str,
    answer: str,
    tags: list[str],
    category: str,
    message_count: int,
    participant_count: int,
    first_message_date: str,
    quality_score: float,
    summary_model: str,
) -> None:
    """Insert or update a FAQ entry.

    Args:
        conn: Database connection
        list_name: Mailing list identifier
        thread_id: Thread identifier
        thread_url: URL to thread view
        question: Extracted core question
        answer: Synthesized answer from thread
        tags: Topic keywords
        category: 'troubleshooting', 'how-to', 'bug-report', 'feature-request', etc.
        message_count: Number of messages in thread
        participant_count: Unique participants
        first_message_date: Thread start date
        quality_score: 0.0-1.0, from LLM scoring
        summary_model: Model used for summarization
    """
    import json

    # Limit answer size
    if len(answer) > 5000:
        answer = answer[:5000]

    conn.execute(
        """
        INSERT INTO faq_entries (list_name, thread_id, thread_url, question, answer,
                                tags, category, message_count, participant_count,
                                first_message_date, quality_score, summarized_at,
                                summary_model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(list_name, thread_id) DO UPDATE SET
            question=excluded.question,
            answer=excluded.answer,
            tags=excluded.tags,
            category=excluded.category,
            message_count=excluded.message_count,
            participant_count=excluded.participant_count,
            quality_score=excluded.quality_score,
            summarized_at=excluded.summarized_at,
            summary_model=excluded.summary_model
        """,
        (
            list_name,
            thread_id,
            thread_url,
            question,
            answer,
            json.dumps(tags),
            category,
            message_count,
            participant_count,
            first_message_date,
            quality_score,
            _now_iso(),
            summary_model,
        ),
    )


def update_summarization_status(
    conn: sqlite3.Connection,
    *,
    list_name: str,
    thread_id: str,
    status: str,
    failure_reason: str | None = None,
    token_count: int | None = None,
    cost_estimate: float | None = None,
) -> None:
    """Track summarization progress.

    Args:
        conn: Database connection
        list_name: Mailing list identifier
        thread_id: Thread identifier
        status: 'pending', 'summarized', 'failed', 'skipped'
        failure_reason: Error message if failed
        token_count: Estimated tokens processed
        cost_estimate: Estimated cost in USD
    """
    conn.execute(
        """
        INSERT INTO summarization_status (list_name, thread_id, status, failure_reason,
                                         token_count, cost_estimate, attempted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(list_name, thread_id) DO UPDATE SET
            status=excluded.status,
            failure_reason=excluded.failure_reason,
            token_count=excluded.token_count,
            cost_estimate=excluded.cost_estimate,
            attempted_at=excluded.attempted_at
        """,
        (list_name, thread_id, status, failure_reason, token_count, cost_estimate, _now_iso()),
    )
