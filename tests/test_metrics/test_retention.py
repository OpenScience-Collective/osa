"""Usage metrics and feedback are kept for five years, then deleted (#725).

Real SQLite files and the real writers; the only thing pinned is the clock, passed in as
``now`` so a row's age is exact.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import src.api.scheduler as scheduler_module
from src.api.config import get_settings
from src.api.main import create_app, lifespan
from src.api.scheduler import (
    MAX_CONSECUTIVE_FAILURES,
    _add_metrics_retention_job,
    _purge_expired_metrics,
)
from src.metrics.db import (
    METRICS_RETENTION_YEARS,
    FeedbackEntry,
    PurgeCounts,
    RequestLogEntry,
    init_metrics_db,
    log_request,
    metrics_connection,
    purge_expired_metrics,
    retention_cutoff,
    write_feedback,
)

NOW = datetime(2031, 6, 15, 12, 0, tzinfo=UTC)


def _request(request_id: str, at: datetime) -> RequestLogEntry:
    return RequestLogEntry(
        request_id=request_id,
        timestamp=at.isoformat(),
        endpoint="/nemar/chat",
        method="POST",
        community_id="nemar",
        input_tokens=10,
        output_tokens=5,
        estimated_cost=0.001,
    )


def _feedback(feedback_id: str, at: datetime) -> FeedbackEntry:
    return FeedbackEntry(
        feedback_id=feedback_id,
        timestamp=at.isoformat(),
        community_id="nemar",
        feedback_type="response",
        sentiment="down",
        comment="the plot was empty",
    )


def _ids(db_path, table: str, column: str) -> set[str]:
    with metrics_connection(db_path) as conn:
        return {row[0] for row in conn.execute(f"SELECT {column} FROM {table}")}


class TestRetentionCutoff:
    def test_five_calendar_years_before_now(self) -> None:
        assert METRICS_RETENTION_YEARS == 5
        assert retention_cutoff(NOW) == "2026-06-15T12:00:00+00:00"

    def test_29_february_falls_back_to_28_february(self) -> None:
        leap_day = datetime(2032, 2, 29, 8, 30, tzinfo=UTC)
        assert retention_cutoff(leap_day) == "2027-02-28T08:30:00+00:00"

    def test_a_time_in_another_zone_is_compared_in_utc(self) -> None:
        """Rows are written in UTC, so the cutoff must be UTC too."""
        pacific = NOW.astimezone(timezone(timedelta(hours=-8)))
        assert pacific.hour != NOW.hour  # the same instant, another wall clock
        assert retention_cutoff(pacific) == retention_cutoff(NOW) == "2026-06-15T12:00:00+00:00"


class TestPurge:
    def test_it_deletes_what_is_older_than_five_years_from_both_tables(self, tmp_path) -> None:
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        old = NOW.replace(year=NOW.year - 5) - timedelta(seconds=1)
        recent = NOW.replace(year=NOW.year - 5) + timedelta(seconds=1)
        log_request(_request("old", old), db_path)
        log_request(_request("recent", recent), db_path)
        log_request(_request("today", NOW), db_path)
        write_feedback(_feedback("old", old), db_path)
        write_feedback(_feedback("recent", recent), db_path)

        counts = purge_expired_metrics(db_path, now=NOW)

        assert counts == PurgeCounts(requests=1, feedback=1)
        assert _ids(db_path, "request_log", "request_id") == {"recent", "today"}
        assert _ids(db_path, "feedback_log", "feedback_id") == {"recent"}

    def test_a_row_exactly_five_years_old_is_kept(self, tmp_path) -> None:
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        log_request(_request("boundary", NOW.replace(year=NOW.year - 5)), db_path)

        assert purge_expired_metrics(db_path, now=NOW) == PurgeCounts(0, 0)
        assert _ids(db_path, "request_log", "request_id") == {"boundary"}

    def test_a_row_without_microseconds_compares_by_its_time(self, tmp_path) -> None:
        """``isoformat`` drops a zero microsecond, so stored rows mix both forms."""
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        cutoff = NOW.replace(year=NOW.year - 5)
        log_request(_request("whole", cutoff - timedelta(seconds=1)), db_path)
        log_request(_request("fraction", cutoff + timedelta(microseconds=500)), db_path)

        purge_expired_metrics(db_path, now=NOW)

        assert _ids(db_path, "request_log", "request_id") == {"fraction"}

    def test_a_deleted_comment_is_erased_from_the_files(self, tmp_path) -> None:
        """A feedback comment can hold personal data: after the purge its text is in
        neither the database file nor the write-ahead log, not only unlinked."""
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        marker = "erase-me-7f3a9c-personal-detail"
        old = NOW.replace(year=NOW.year - 6)
        entry = _feedback("old", old)
        entry.comment = marker
        write_feedback(entry, db_path)
        write_feedback(_feedback("recent", NOW), db_path)

        def on_disk() -> bytes:
            files = [db_path, db_path.with_name(db_path.name + "-wal")]
            return b"".join(f.read_bytes() for f in files if f.exists())

        assert marker.encode() in on_disk()  # the check below is not vacuous

        purge_expired_metrics(db_path, now=NOW)

        assert _ids(db_path, "feedback_log", "feedback_id") == {"recent"}
        assert marker.encode() not in on_disk()

    def test_an_empty_database_is_a_no_op(self, tmp_path) -> None:
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        assert purge_expired_metrics(db_path, now=NOW) == PurgeCounts(0, 0)


class TestWhereItRuns:
    async def test_startup_purges_even_with_sync_scheduling_off(
        self, monkeypatch, tmp_path
    ) -> None:
        """The daily job needs the scheduler, which starts only with sync enabled, so
        startup runs the purge itself: every deploy and restart applies the period."""
        monkeypatch.setattr(get_settings(), "sync_enabled", False)
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        now = datetime.now(UTC)
        log_request(_request("ancient", now - timedelta(days=6 * 366)), db_path)
        log_request(_request("fresh", now), db_path)
        write_feedback(_feedback("ancient", now - timedelta(days=6 * 366)), db_path)

        async with lifespan(create_app()):
            pass

        assert _ids(db_path, "request_log", "request_id") == {"fresh"}
        assert _ids(db_path, "feedback_log", "feedback_id") == set()

    def test_the_daily_job_purges_the_configured_database(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        now = datetime.now(UTC)
        log_request(_request("ancient", now - timedelta(days=6 * 366)), db_path)

        _purge_expired_metrics()

        assert _ids(db_path, "request_log", "request_id") == set()

    @pytest.mark.usefixtures("reset_retention_failures")
    def test_the_daily_job_logs_a_broken_database_and_does_not_raise(
        self, monkeypatch, tmp_path, caplog
    ) -> None:
        """A database file that is not SQLite: the job logs the error with its traceback
        and returns, so the scheduler keeps running."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        (tmp_path / "metrics.db").write_bytes(b"this is not a sqlite database" * 100)

        _purge_expired_metrics()

        (record,) = [r for r in caplog.records if "expired usage metrics" in r.getMessage()]
        assert record.levelname == "ERROR"
        assert record.exc_info is not None

    @pytest.mark.usefixtures("reset_retention_failures")
    def test_repeated_failures_escalate_and_a_success_resets_them(
        self, monkeypatch, tmp_path, caplog
    ) -> None:
        """PRIVACY.md promises the deletion, so a job that keeps failing escalates to
        CRITICAL like the budget and mirror jobs, and one success starts the count over."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        broken = tmp_path / "metrics.db"
        broken.write_bytes(b"this is not a sqlite database" * 100)
        caplog.set_level(logging.ERROR)

        for _ in range(MAX_CONSECUTIVE_FAILURES):
            _purge_expired_metrics()

        critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical) == 1
        assert "not being deleted" in critical[0].getMessage()

        broken.unlink()
        init_metrics_db(broken)
        _purge_expired_metrics()
        assert scheduler_module._metrics_retention_failures == 0

    def test_the_scheduler_registers_the_daily_job_at_03_15(self) -> None:
        """A real APScheduler, never started: the job is registered with its trigger."""
        scheduler = BackgroundScheduler()
        _add_metrics_retention_job(scheduler)

        job = scheduler.get_job("metrics_retention")

        assert job is not None
        assert job.func is _purge_expired_metrics
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert (fields["hour"], fields["minute"]) == ("3", "15")


@pytest.fixture
def reset_retention_failures():
    """Reset the module-global failure counter around tests that exercise it."""
    scheduler_module._metrics_retention_failures = 0
    yield
    scheduler_module._metrics_retention_failures = 0
