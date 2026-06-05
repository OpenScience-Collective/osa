"""Metrics storage layer using SQLite with WAL mode.

Single SQLite database at {data_dir}/metrics.db stores request logs and user
feedback. WAL mode enables concurrent reads during writes.
"""

import json
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage

logger = logging.getLogger(__name__)

# Track consecutive log_request failures for escalation
_log_request_failures: int = 0

# Track consecutive write_feedback failures for escalation
_write_feedback_failures: int = 0

# Allowed feedback type / sentiment values. These mirror the SQLite CHECK
# constraints below and the FeedbackEntry / FeedbackRequest invariants.
FEEDBACK_TYPES = ("response", "general")
FEEDBACK_SENTIMENTS = ("up", "down")

METRICS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    community_id TEXT,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    duration_ms REAL,
    status_code INTEGER,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    estimated_cost REAL,
    tools_called TEXT,
    key_source TEXT,
    stream INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    error_message TEXT,
    langfuse_trace_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_request_log_community
    ON request_log(community_id);
CREATE INDEX IF NOT EXISTS idx_request_log_timestamp
    ON request_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_request_log_community_timestamp
    ON request_log(community_id, timestamp);

CREATE TABLE IF NOT EXISTS feedback_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    community_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL CHECK (feedback_type IN ('response', 'general')),
    sentiment TEXT CHECK (sentiment IN ('up', 'down') OR sentiment IS NULL),
    request_id TEXT,
    session_id TEXT,
    message_index INTEGER,
    comment TEXT,
    page_url TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_log_feedback_id
    ON feedback_log(feedback_id);
CREATE INDEX IF NOT EXISTS idx_feedback_log_community
    ON feedback_log(community_id);
CREATE INDEX IF NOT EXISTS idx_feedback_log_community_timestamp
    ON feedback_log(community_id, timestamp);
"""

# Columns added after initial schema; ALTER TABLE for existing databases
_MIGRATION_COLUMNS = [
    ("tool_call_count", "INTEGER DEFAULT 0"),
    ("error_message", "TEXT"),
    ("langfuse_trace_id", "TEXT"),
]


@dataclass
class RequestLogEntry:
    """A single request log entry for the metrics database."""

    request_id: str
    timestamp: str
    endpoint: str
    method: str
    community_id: str | None = None
    duration_ms: float | None = None
    status_code: int | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    tools_called: list[str] = field(default_factory=list)
    key_source: str | None = None
    stream: bool = False
    tool_call_count: int = 0
    error_message: str | None = None
    langfuse_trace_id: str | None = None


@dataclass
class FeedbackEntry:
    """A single user-feedback entry for the metrics database.

    Two flavors share one table:

    * ``feedback_type="response"`` -- a thumbs up/down on a specific assistant
      reply. ``sentiment`` is required; ``request_id`` (and/or ``message_index``)
      links it back to the answer it rates.
    * ``feedback_type="general"`` -- free-text feedback not tied to a single
      reply. ``sentiment`` is typically ``None`` and ``comment`` carries the text.
    """

    feedback_id: str
    timestamp: str
    community_id: str
    feedback_type: Literal["response", "general"]
    sentiment: Literal["up", "down"] | None = None
    request_id: str | None = None
    session_id: str | None = None
    message_index: int | None = None
    comment: str | None = None
    page_url: str | None = None

    def __post_init__(self) -> None:
        """Enforce the storage-layer invariants.

        ``write_feedback`` is a public function callable outside the API router,
        so this is the last checkpoint before a row reaches SQLite. Reject the
        illegal shapes the satisfaction-rate query would otherwise be corrupted
        by (a 'response' with no sentiment, or a 'general' carrying one).
        """
        if self.feedback_type not in FEEDBACK_TYPES:
            raise ValueError(f"feedback_type must be one of {FEEDBACK_TYPES!r}")
        if self.sentiment is not None and self.sentiment not in FEEDBACK_SENTIMENTS:
            raise ValueError(f"sentiment must be one of {FEEDBACK_SENTIMENTS!r} or None")
        if self.feedback_type == "response" and self.sentiment is None:
            raise ValueError("response feedback requires a sentiment")
        if self.feedback_type == "general" and self.sentiment is not None:
            raise ValueError("general feedback must not carry a sentiment")


def get_metrics_db_path() -> Path:
    """Return path to the metrics SQLite database.

    Uses DATA_DIR environment variable if set (Docker deployments),
    otherwise falls back to platform-specific user data directory.
    """
    import os

    from platformdirs import user_data_dir

    data_dir_env = os.environ.get("DATA_DIR")
    base = Path(data_dir_env) if data_dir_env else Path(user_data_dir("osa", "osc"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "metrics.db"


def get_metrics_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the metrics database.

    Args:
        db_path: Optional path override (for testing).
    """
    path = db_path or get_metrics_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def metrics_connection(db_path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for a metrics database connection.

    Ensures the connection is closed after use, even if an exception occurs.

    Args:
        db_path: Optional path override (for testing).
    """
    conn = get_metrics_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Add new columns to existing databases (backward-compatible migration).

    Attempts ALTER TABLE ADD COLUMN for each new column. If the column
    already exists, SQLite raises OperationalError with 'duplicate column',
    which we catch and ignore, making this function idempotent.
    """
    for col_name, col_def in _MIGRATION_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE request_log ADD COLUMN {col_name} {col_def}")
            logger.info("Added column %s to request_log", col_name)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                pass  # Expected on subsequent runs
            else:
                logger.error("Failed to add column %s to request_log: %s", col_name, e)
                raise


def init_metrics_db(db_path: Path | None = None) -> None:
    """Initialize the metrics database schema. Idempotent.

    Creates the request_log table and indexes if they don't exist.
    Enables WAL mode for concurrent read/write access.
    Runs migrations to add new columns to existing databases.

    Args:
        db_path: Optional path override (for testing).
    """
    conn = get_metrics_connection(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(METRICS_SCHEMA_SQL)
        _migrate_columns(conn)
        conn.commit()
        logger.info("Metrics database initialized at %s", db_path or get_metrics_db_path())
    finally:
        conn.close()


def log_request(entry: RequestLogEntry, db_path: Path | None = None) -> None:
    """Insert a request log entry into the database.

    Args:
        entry: The log entry to insert.
        db_path: Optional path override (for testing).
    """
    global _log_request_failures
    conn = get_metrics_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO request_log (
                request_id, timestamp, community_id, endpoint, method,
                duration_ms, status_code, model, input_tokens, output_tokens,
                total_tokens, estimated_cost, tools_called, key_source, stream,
                tool_call_count, error_message, langfuse_trace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.request_id,
                entry.timestamp,
                entry.community_id,
                entry.endpoint,
                entry.method,
                entry.duration_ms,
                entry.status_code,
                entry.model,
                entry.input_tokens,
                entry.output_tokens,
                entry.total_tokens,
                entry.estimated_cost,
                json.dumps(entry.tools_called) if entry.tools_called else None,
                entry.key_source,
                1 if entry.stream else 0,
                entry.tool_call_count,
                entry.error_message,
                entry.langfuse_trace_id,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        _log_request_failures += 1
        logger.exception(
            "Failed to log metrics request %s (endpoint=%s, community=%s) [failure #%d]",
            entry.request_id,
            entry.endpoint,
            entry.community_id,
            _log_request_failures,
        )
        if _log_request_failures >= 10:
            logger.critical(
                "Metrics DB write has failed %d times. "
                "Possible disk/database issue requiring investigation.",
                _log_request_failures,
            )
    else:
        # Reset counter on success
        _log_request_failures = 0
    finally:
        conn.close()


def write_feedback(entry: FeedbackEntry, db_path: Path | None = None) -> None:
    """Insert a feedback entry into the database.

    Mirrors :func:`log_request`: best-effort, never raises to the caller, and
    escalates to a CRITICAL log after repeated failures so a broken disk or DB
    surfaces in monitoring instead of silently dropping feedback.

    Args:
        entry: The feedback entry to insert.
        db_path: Optional path override (for testing).
    """
    global _write_feedback_failures
    conn: sqlite3.Connection | None = None
    try:
        # Acquire the connection inside the try so a connect failure (e.g. the
        # data dir vanished) is handled here too and the function honors its
        # "never raises" contract.
        conn = get_metrics_connection(db_path)
        conn.execute(
            """
            INSERT INTO feedback_log (
                feedback_id, timestamp, community_id, feedback_type, sentiment,
                request_id, session_id, message_index, comment, page_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.feedback_id,
                entry.timestamp,
                entry.community_id,
                entry.feedback_type,
                entry.sentiment,
                entry.request_id,
                entry.session_id,
                entry.message_index,
                entry.comment,
                entry.page_url,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        _write_feedback_failures += 1
        logger.exception(
            "Failed to write feedback %s (community=%s, type=%s) [failure #%d]",
            entry.feedback_id,
            entry.community_id,
            entry.feedback_type,
            _write_feedback_failures,
        )
        if _write_feedback_failures >= 10:
            logger.critical(
                "Feedback DB write has failed %d times. "
                "Possible disk/database issue requiring investigation.",
                _write_feedback_failures,
            )
    else:
        _write_feedback_failures = 0
    finally:
        if conn is not None:
            conn.close()


def extract_token_usage(result: dict) -> tuple[int, int, int]:
    """Extract token usage from agent result messages.

    Sums usage_metadata from all AIMessages in result["messages"].

    Args:
        result: Agent result dict containing "messages" list.

    Returns:
        Tuple of (input_tokens, output_tokens, total_tokens).
        Returns (0, 0, 0) if no usage data is available.
    """
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0

    messages: list[BaseMessage] = result.get("messages", [])
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        usage = getattr(msg, "usage_metadata", None)
        if usage is None:
            continue
        # usage_metadata is a dict with input_tokens, output_tokens, total_tokens
        if isinstance(usage, dict):
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
            total_tokens += usage.get("total_tokens", 0)

    return input_tokens, output_tokens, total_tokens


def extract_tool_names(result: dict) -> list[str]:
    """Extract tool names from agent result.

    Args:
        result: Agent result dict containing "tool_calls" list.

    Returns:
        List of tool names called during the request.
    """
    tool_calls = result.get("tool_calls", [])
    return [tc.get("name", "") for tc in tool_calls if tc.get("name")]


def now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(UTC).isoformat()
