"""Tests for quality metrics queries."""

import pytest

from src.metrics.db import RequestLogEntry, get_metrics_connection, init_metrics_db, log_request


@pytest.fixture
def quality_db(tmp_path):
    """Create a metrics DB with quality-related data."""
    db_path = tmp_path / "metrics.db"
    init_metrics_db(db_path)

    entries = [
        RequestLogEntry(
            request_id="q1",
            timestamp="2025-01-15T10:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=200.0,
            status_code=200,
            model="qwen/qwen3-235b",
            tool_call_count=2,
            langfuse_trace_id="trace-abc",
        ),
        RequestLogEntry(
            request_id="q2",
            timestamp="2025-01-15T11:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=500.0,
            status_code=200,
            model="qwen/qwen3-235b",
            tool_call_count=1,
            langfuse_trace_id="trace-def",
        ),
        RequestLogEntry(
            request_id="q3",
            timestamp="2025-01-15T12:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=1500.0,
            status_code=500,
            model="qwen/qwen3-235b",
            tool_call_count=0,
            error_message="LLM timeout",
        ),
        RequestLogEntry(
            request_id="q4",
            timestamp="2025-01-16T10:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=300.0,
            status_code=200,
            model="qwen/qwen3-235b",
            tool_call_count=3,
            langfuse_trace_id="trace-ghi",
        ),
        RequestLogEntry(
            request_id="q5",
            timestamp="2025-01-15T10:00:00+00:00",
            endpoint="/eeglab/ask",
            method="POST",
            community_id="eeglab",
            duration_ms=250.0,
            status_code=200,
            tool_call_count=1,
        ),
    ]
    for e in entries:
        log_request(e, db_path=db_path)

    return db_path


class TestGetQualityMetrics:
    """Tests for get_quality_metrics()."""

    def test_daily_buckets(self, quality_db):
        from src.metrics.queries import get_quality_metrics

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_metrics("hed", conn, "daily")
            assert result["community_id"] == "hed"
            assert result["period"] == "daily"
            buckets = {b["bucket"]: b for b in result["buckets"]}
            assert "2025-01-15" in buckets
            assert "2025-01-16" in buckets
        finally:
            conn.close()

    def test_error_rate_per_bucket(self, quality_db):
        from src.metrics.queries import get_quality_metrics

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_metrics("hed", conn, "daily")
            buckets = {b["bucket"]: b for b in result["buckets"]}
            # Jan 15: 1 error out of 3 requests
            assert abs(buckets["2025-01-15"]["error_rate"] - 0.3333) < 0.01
            # Jan 16: 0 errors
            assert buckets["2025-01-16"]["error_rate"] == 0.0
        finally:
            conn.close()

    def test_avg_tool_calls(self, quality_db):
        from src.metrics.queries import get_quality_metrics

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_metrics("hed", conn, "daily")
            buckets = {b["bucket"]: b for b in result["buckets"]}
            # Jan 15: (2+1+0)/3 = 1.0
            assert buckets["2025-01-15"]["avg_tool_calls"] == 1.0
            # Jan 16: 3/1 = 3.0
            assert buckets["2025-01-16"]["avg_tool_calls"] == 3.0
        finally:
            conn.close()

    def test_agent_errors(self, quality_db):
        from src.metrics.queries import get_quality_metrics

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_metrics("hed", conn, "daily")
            buckets = {b["bucket"]: b for b in result["buckets"]}
            assert buckets["2025-01-15"]["agent_errors"] == 1  # "LLM timeout"
            assert buckets["2025-01-16"]["agent_errors"] == 0
        finally:
            conn.close()

    def test_traced_requests(self, quality_db):
        from src.metrics.queries import get_quality_metrics

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_metrics("hed", conn, "daily")
            buckets = {b["bucket"]: b for b in result["buckets"]}
            assert buckets["2025-01-15"]["traced_requests"] == 2
            assert buckets["2025-01-16"]["traced_requests"] == 1
        finally:
            conn.close()

    def test_latency_percentiles(self, quality_db):
        from src.metrics.queries import get_quality_metrics

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_metrics("hed", conn, "daily")
            buckets = {b["bucket"]: b for b in result["buckets"]}
            # Jan 15: durations [200, 500, 1500] sorted
            # p50 = values[1] = 500, p95 = values[2] = 1500
            assert buckets["2025-01-15"]["p50_duration_ms"] == 500.0
            assert buckets["2025-01-15"]["p95_duration_ms"] == 1500.0
        finally:
            conn.close()

    def test_empty_community(self, quality_db):
        from src.metrics.queries import get_quality_metrics

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_metrics("nonexistent", conn, "daily")
            assert result["buckets"] == []
        finally:
            conn.close()


class TestGetQualitySummary:
    """Tests for get_quality_summary()."""

    def test_summary_totals(self, quality_db):
        from src.metrics.queries import get_quality_summary

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_summary("hed", conn)
            assert result["community_id"] == "hed"
            assert result["total_requests"] == 4
            assert abs(result["error_rate"] - 0.25) < 0.01
        finally:
            conn.close()

    def test_summary_avg_tool_calls(self, quality_db):
        from src.metrics.queries import get_quality_summary

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_summary("hed", conn)
            # (2+1+0+3)/4 = 1.5
            assert result["avg_tool_calls"] == 1.5
        finally:
            conn.close()

    def test_summary_traced_pct(self, quality_db):
        from src.metrics.queries import get_quality_summary

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_summary("hed", conn)
            # 3 traced out of 4
            assert abs(result["traced_pct"] - 0.75) < 0.01
        finally:
            conn.close()

    def test_summary_latency(self, quality_db):
        from src.metrics.queries import get_quality_summary

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_summary("hed", conn)
            # durations sorted: [200, 300, 500, 1500]
            # nearest-rank p50: idx=int(0.5*4)=2 -> 500
            # nearest-rank p95: idx=int(0.95*4)=3 -> 1500
            assert result["p50_duration_ms"] == 500.0
            assert result["p95_duration_ms"] == 1500.0
        finally:
            conn.close()

    def test_empty_community(self, quality_db):
        from src.metrics.queries import get_quality_summary

        conn = get_metrics_connection(quality_db)
        try:
            result = get_quality_summary("nonexistent", conn)
            assert result["total_requests"] == 0
            assert result["error_rate"] == 0.0
            assert result["p50_duration_ms"] is None
        finally:
            conn.close()


class TestPercentileEdgeCases:
    """Tests for _percentile() helper edge cases."""

    def test_single_element(self):
        from src.metrics.queries import _percentile

        assert _percentile([100.0], 0.5) == 100.0
        assert _percentile([100.0], 0.95) == 100.0

    def test_two_elements(self):
        from src.metrics.queries import _percentile

        values = [100.0, 200.0]
        assert _percentile(values, 0.5) == 200.0  # idx=int(0.5*2)=1
        assert _percentile(values, 0.95) == 200.0  # idx=int(0.95*2)=1

    def test_empty_returns_none(self):
        from src.metrics.queries import _percentile

        assert _percentile([], 0.5) is None


class TestCountToolsMalformedJSON:
    """Tests for _count_tools() with malformed data."""

    def test_malformed_json_skipped(self, tmp_path):
        """Rows with invalid tools_called JSON should be skipped gracefully."""

        from src.metrics.queries import get_quality_metrics

        db_path = tmp_path / "malformed.db"
        init_metrics_db(db_path)

        # Insert a valid entry first
        log_request(
            RequestLogEntry(
                request_id="valid",
                timestamp="2025-01-15T10:00:00+00:00",
                endpoint="/hed/ask",
                method="POST",
                community_id="hed",
                duration_ms=200.0,
                status_code=200,
                tools_called=["search_docs"],
            ),
            db_path=db_path,
        )

        # Insert a row with invalid JSON directly via SQL
        conn = get_metrics_connection(db_path)
        try:
            conn.execute(
                """INSERT INTO request_log
                   (request_id, timestamp, endpoint, method, community_id,
                    duration_ms, status_code, tools_called)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "bad",
                    "2025-01-15T11:00:00+00:00",
                    "/hed/ask",
                    "POST",
                    "hed",
                    300.0,
                    200,
                    "not-valid-json{{{",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # Query should still work, returning valid tool counts
        conn = get_metrics_connection(db_path)
        try:
            result = get_quality_metrics("hed", conn, "daily")
            assert len(result["buckets"]) == 1
            # Should have at least the valid entry's tool
            bucket = result["buckets"][0]
            assert bucket["requests"] == 2
        finally:
            conn.close()


class TestMigrationColumns:
    """Tests for backward-compatible column migration."""

    def test_new_columns_exist_after_init(self, quality_db):
        """New quality columns should exist in freshly initialized DB."""
        conn = get_metrics_connection(quality_db)
        try:
            # Query using the new columns
            row = conn.execute(
                "SELECT tool_call_count, error_message, langfuse_trace_id FROM request_log LIMIT 1"
            ).fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_migration_idempotent(self, quality_db):
        """Running init_metrics_db twice should not fail."""
        # Second init should be a no-op for migrations
        init_metrics_db(quality_db)
        conn = get_metrics_connection(quality_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM request_log").fetchone()[0]
            assert count == 5  # Original data preserved
        finally:
            conn.close()
