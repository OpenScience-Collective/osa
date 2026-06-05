"""Tests for the feedback storage layer (feedback_log table + queries)."""

import pytest

from src.metrics.db import (
    FeedbackEntry,
    get_metrics_connection,
    init_metrics_db,
    now_iso,
    write_feedback,
)
from src.metrics.queries import get_feedback_entries, get_feedback_summary


@pytest.fixture
def feedback_db(tmp_path):
    """Create a temporary metrics database with feedback rows."""
    db_path = tmp_path / "metrics.db"
    init_metrics_db(db_path)

    entries = [
        FeedbackEntry(
            feedback_id="f1",
            timestamp="2025-01-15T10:00:00+00:00",
            community_id="hed",
            feedback_type="response",
            sentiment="up",
            request_id="r1",
            session_id="s1",
            message_index=0,
        ),
        FeedbackEntry(
            feedback_id="f2",
            timestamp="2025-01-15T11:00:00+00:00",
            community_id="hed",
            feedback_type="response",
            sentiment="down",
            request_id="r2",
            session_id="s1",
            message_index=2,
            comment="answer was wrong about epochs",
        ),
        FeedbackEntry(
            feedback_id="f3",
            timestamp="2025-01-15T12:00:00+00:00",
            community_id="hed",
            feedback_type="general",
            sentiment=None,
            comment="love this assistant",
            page_url="https://hedtags.org",
        ),
        # A different community, to prove scoping
        FeedbackEntry(
            feedback_id="f4",
            timestamp="2025-01-15T13:00:00+00:00",
            community_id="eeglab",
            feedback_type="response",
            sentiment="up",
        ),
    ]
    for e in entries:
        write_feedback(e, db_path=db_path)

    return db_path


class TestFeedbackTable:
    """Schema creation for feedback_log."""

    def test_table_created(self, tmp_path):
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        conn = get_metrics_connection(db_path)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_log'"
            ).fetchall()
            assert len(tables) == 1
        finally:
            conn.close()

    def test_table_added_to_existing_db(self, tmp_path):
        """A pre-existing DB without feedback_log gets the table on re-init.

        This proves the CREATE TABLE IF NOT EXISTS path auto-provisions the
        new table on already-deployed databases without a bespoke migration.
        Simulated by initializing the full schema, dropping feedback_log to
        recreate an "old" DB, then re-running init.
        """
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)

        conn = get_metrics_connection(db_path)
        conn.execute("DROP TABLE feedback_log")
        conn.commit()
        conn.close()

        # Re-init (mirrors a deploy bringing the new schema).
        init_metrics_db(db_path)

        conn = get_metrics_connection(db_path)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_log'"
            ).fetchall()
            assert len(tables) == 1
        finally:
            conn.close()


class TestWriteFeedback:
    """write_feedback() persistence."""

    def test_round_trip(self, tmp_path):
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        write_feedback(
            FeedbackEntry(
                feedback_id="x1",
                timestamp=now_iso(),
                community_id="hed",
                feedback_type="response",
                sentiment="up",
                request_id="req-abc",
            ),
            db_path=db_path,
        )
        conn = get_metrics_connection(db_path)
        try:
            row = conn.execute("SELECT * FROM feedback_log WHERE feedback_id = 'x1'").fetchone()
            assert row["sentiment"] == "up"
            assert row["request_id"] == "req-abc"
            assert row["feedback_type"] == "response"
        finally:
            conn.close()


class TestFeedbackSummary:
    """get_feedback_summary() aggregation."""

    def test_counts_for_community(self, feedback_db):
        conn = get_metrics_connection(feedback_db)
        try:
            summary = get_feedback_summary(conn, community_id="hed")
        finally:
            conn.close()
        assert summary["thumbs_up"] == 1
        assert summary["thumbs_down"] == 1
        assert summary["response_total"] == 2
        assert summary["general_total"] == 1
        assert summary["comment_total"] == 2  # f2 (note) + f3 (general)
        assert summary["satisfaction_rate"] == 0.5

    def test_counts_all_communities(self, feedback_db):
        conn = get_metrics_connection(feedback_db)
        try:
            summary = get_feedback_summary(conn, community_id=None)
        finally:
            conn.close()
        assert summary["thumbs_up"] == 2  # hed f1 + eeglab f4
        assert summary["thumbs_down"] == 1

    def test_satisfaction_rate_none_when_no_ratings(self, tmp_path):
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        conn = get_metrics_connection(db_path)
        try:
            summary = get_feedback_summary(conn, community_id="hed")
        finally:
            conn.close()
        assert summary["satisfaction_rate"] is None


class TestFeedbackEntries:
    """get_feedback_entries() listing."""

    def test_scoped_to_community(self, feedback_db):
        conn = get_metrics_connection(feedback_db)
        try:
            rows = get_feedback_entries(conn, community_id="hed")
        finally:
            conn.close()
        assert len(rows) == 3
        assert all(r["community_id"] == "hed" for r in rows)
        # Most recent first
        assert rows[0]["timestamp"] >= rows[-1]["timestamp"]

    def test_comments_only_filter(self, feedback_db):
        conn = get_metrics_connection(feedback_db)
        try:
            rows = get_feedback_entries(conn, community_id="hed", with_comment_only=True)
        finally:
            conn.close()
        assert len(rows) == 2
        assert all(r["comment"] for r in rows)

    def test_limit_clamped(self, feedback_db):
        conn = get_metrics_connection(feedback_db)
        try:
            rows = get_feedback_entries(conn, community_id="hed", limit=1)
        finally:
            conn.close()
        assert len(rows) == 1
