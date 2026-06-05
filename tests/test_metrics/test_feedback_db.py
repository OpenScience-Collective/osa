"""Tests for the feedback storage layer (feedback_log table + queries)."""

import logging

import pytest

import src.metrics.db as db_module
from src.metrics.db import (
    FeedbackEntry,
    get_metrics_connection,
    init_metrics_db,
    now_iso,
    write_feedback,
)
from src.metrics.queries import get_feedback_entries, get_feedback_summary


@pytest.fixture
def reset_feedback_counter():
    """Reset the module-global failure counter around tests that exercise it."""
    db_module._write_feedback_failures = 0
    yield
    db_module._write_feedback_failures = 0


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

    @pytest.mark.usefixtures("reset_feedback_counter")
    def test_failure_counter_increments_and_resets(self, tmp_path):
        # A UNIQUE feedback_id collision makes the second write fail at INSERT.
        # write_feedback must swallow it, bump the counter, then reset on success.
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        dup = FeedbackEntry(
            feedback_id="dup",
            timestamp=now_iso(),
            community_id="hed",
            feedback_type="response",
            sentiment="up",
        )
        write_feedback(dup, db_path=db_path)
        assert db_module._write_feedback_failures == 0
        write_feedback(dup, db_path=db_path)  # duplicate -> swallowed failure
        assert db_module._write_feedback_failures == 1
        write_feedback(
            FeedbackEntry(
                feedback_id="ok2",
                timestamp=now_iso(),
                community_id="hed",
                feedback_type="response",
                sentiment="up",
            ),
            db_path=db_path,
        )
        assert db_module._write_feedback_failures == 0

    @pytest.mark.usefixtures("reset_feedback_counter")
    def test_failure_escalates_to_critical(self, tmp_path, caplog):
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        dup = FeedbackEntry(
            feedback_id="d",
            timestamp=now_iso(),
            community_id="hed",
            feedback_type="response",
            sentiment="up",
        )
        write_feedback(dup, db_path=db_path)  # first succeeds
        with caplog.at_level(logging.CRITICAL):
            for _ in range(10):
                write_feedback(dup, db_path=db_path)  # all collide
        assert any(r.levelno == logging.CRITICAL for r in caplog.records)


class TestFeedbackEntryInvariants:
    """FeedbackEntry.__post_init__ rejects illegal shapes at the storage layer."""

    def test_response_requires_sentiment(self):
        with pytest.raises(ValueError, match="response feedback requires a sentiment"):
            FeedbackEntry(
                feedback_id="a",
                timestamp=now_iso(),
                community_id="hed",
                feedback_type="response",
                sentiment=None,
            )

    def test_general_must_not_carry_sentiment(self):
        with pytest.raises(ValueError, match="general feedback must not carry a sentiment"):
            FeedbackEntry(
                feedback_id="b",
                timestamp=now_iso(),
                community_id="hed",
                feedback_type="general",
                sentiment="up",
                comment="x",
            )

    def test_bad_feedback_type_rejected(self):
        with pytest.raises(ValueError, match="feedback_type must be"):
            FeedbackEntry(
                feedback_id="c",
                timestamp=now_iso(),
                community_id="hed",
                feedback_type="bogus",
            )

    def test_bad_sentiment_rejected(self):
        with pytest.raises(ValueError, match="sentiment must be"):
            FeedbackEntry(
                feedback_id="d",
                timestamp=now_iso(),
                community_id="hed",
                feedback_type="response",
                sentiment="meh",
            )


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

    def test_limit_floor_clamped(self, feedback_db):
        # limit=0 must be clamped up to 1 (without clamping, SQL LIMIT 0 would
        # return zero rows). Asserting a non-empty result tests the clamp itself.
        conn = get_metrics_connection(feedback_db)
        try:
            rows = get_feedback_entries(conn, community_id="hed", limit=0)
        finally:
            conn.close()
        assert len(rows) == 1

    def test_offset_pagination(self, feedback_db):
        # hed has 3 entries; page through them with limit=2.
        conn = get_metrics_connection(feedback_db)
        try:
            page1 = get_feedback_entries(conn, community_id="hed", limit=2, offset=0)
            page2 = get_feedback_entries(conn, community_id="hed", limit=2, offset=2)
        finally:
            conn.close()
        assert len(page1) == 2
        assert len(page2) == 1
        stamps = {r["timestamp"] for r in page1} | {r["timestamp"] for r in page2}
        # No overlap, full coverage of the 3 hed rows.
        assert len(stamps) == 3
