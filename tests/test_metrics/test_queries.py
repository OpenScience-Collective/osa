"""Tests for metrics aggregation queries."""

import pytest

from src.metrics.db import RequestLogEntry, get_metrics_connection, init_metrics_db, log_request


@pytest.fixture
def populated_db(tmp_path):
    """Create a metrics DB with sample data for query testing."""
    db_path = tmp_path / "metrics.db"
    init_metrics_db(db_path)

    entries = [
        RequestLogEntry(
            request_id="r1",
            timestamp="2025-01-15T10:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=200.0,
            status_code=200,
            model="qwen/qwen3-235b",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            estimated_cost=0.001,
            tools_called=["search_docs"],
            key_source="platform",
        ),
        RequestLogEntry(
            request_id="r2",
            timestamp="2025-01-15T11:00:00+00:00",
            endpoint="/hed/chat",
            method="POST",
            community_id="hed",
            duration_ms=300.0,
            status_code=200,
            model="qwen/qwen3-235b",
            input_tokens=200,
            output_tokens=100,
            total_tokens=300,
            estimated_cost=0.002,
            tools_called=["search_docs", "validate_hed"],
            key_source="byok",
        ),
        RequestLogEntry(
            request_id="r3",
            timestamp="2025-01-16T10:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=150.0,
            status_code=500,
            model="openai/gpt-4o",
            input_tokens=50,
            output_tokens=0,
            total_tokens=50,
            key_source="byok",
        ),
        RequestLogEntry(
            request_id="r4",
            timestamp="2025-01-15T12:00:00+00:00",
            endpoint="/bids/ask",
            method="POST",
            community_id="bids",
            duration_ms=250.0,
            status_code=200,
            model="qwen/qwen3-235b",
            input_tokens=80,
            output_tokens=40,
            total_tokens=120,
            estimated_cost=0.0008,
            tools_called=["search_docs"],
            key_source="platform",
        ),
        RequestLogEntry(
            request_id="r5",
            timestamp="2025-01-15T09:00:00+00:00",
            endpoint="/health",
            method="GET",
            duration_ms=5.0,
            status_code=200,
        ),
    ]
    for e in entries:
        log_request(e, db_path=db_path)

    return db_path


class TestGetCommunitySummary:
    """Tests for get_community_summary()."""

    def test_returns_correct_totals(self, populated_db):
        from src.metrics.queries import get_community_summary

        conn = get_metrics_connection(populated_db)
        try:
            result = get_community_summary("hed", conn)
            assert result["community_id"] == "hed"
            assert result["total_requests"] == 3
            assert result["total_input_tokens"] == 350
            assert result["total_output_tokens"] == 150
            assert result["total_tokens"] == 500
        finally:
            conn.close()

    def test_error_rate(self, populated_db):
        from src.metrics.queries import get_community_summary

        conn = get_metrics_connection(populated_db)
        try:
            result = get_community_summary("hed", conn)
            # 1 error out of 3 requests
            assert abs(result["error_rate"] - 0.3333) < 0.01
        finally:
            conn.close()

    def test_top_models(self, populated_db):
        from src.metrics.queries import get_community_summary

        conn = get_metrics_connection(populated_db)
        try:
            result = get_community_summary("hed", conn)
            models = {m["model"]: m["count"] for m in result["top_models"]}
            assert models["qwen/qwen3-235b"] == 2
            assert models["openai/gpt-4o"] == 1
        finally:
            conn.close()

    def test_top_tools(self, populated_db):
        from src.metrics.queries import get_community_summary

        conn = get_metrics_connection(populated_db)
        try:
            result = get_community_summary("hed", conn)
            tools = {t["tool"]: t["count"] for t in result["top_tools"]}
            assert tools["search_docs"] == 2
            assert tools["validate_hed"] == 1
        finally:
            conn.close()

    def test_empty_community(self, populated_db):
        from src.metrics.queries import get_community_summary

        conn = get_metrics_connection(populated_db)
        try:
            result = get_community_summary("nonexistent", conn)
            assert result["total_requests"] == 0
            assert result["total_tokens"] == 0
            assert result["error_rate"] == 0.0
        finally:
            conn.close()


class TestGetUsageStats:
    """Tests for get_usage_stats()."""

    def test_daily_bucketing(self, populated_db):
        from src.metrics.queries import get_usage_stats

        conn = get_metrics_connection(populated_db)
        try:
            result = get_usage_stats("hed", "daily", conn)
            assert result["period"] == "daily"
            assert result["community_id"] == "hed"
            buckets = {b["bucket"]: b for b in result["buckets"]}
            assert "2025-01-15" in buckets
            assert "2025-01-16" in buckets
            assert buckets["2025-01-15"]["requests"] == 2
            assert buckets["2025-01-16"]["requests"] == 1
        finally:
            conn.close()

    def test_monthly_bucketing(self, populated_db):
        from src.metrics.queries import get_usage_stats

        conn = get_metrics_connection(populated_db)
        try:
            result = get_usage_stats("hed", "monthly", conn)
            buckets = result["buckets"]
            assert len(buckets) == 1
            assert buckets[0]["bucket"] == "2025-01"
            assert buckets[0]["requests"] == 3
        finally:
            conn.close()

    def test_invalid_period_raises(self, populated_db):
        from src.metrics.queries import get_usage_stats

        conn = get_metrics_connection(populated_db)
        try:
            with pytest.raises(ValueError, match="Invalid period"):
                get_usage_stats("hed", "hourly", conn)
        finally:
            conn.close()

    def test_empty_community_returns_empty_buckets(self, populated_db):
        from src.metrics.queries import get_usage_stats

        conn = get_metrics_connection(populated_db)
        try:
            result = get_usage_stats("nonexistent", "daily", conn)
            assert result["buckets"] == []
        finally:
            conn.close()


class TestGetOverview:
    """Tests for get_overview()."""

    def test_overview_totals(self, populated_db):
        from src.metrics.queries import get_overview

        conn = get_metrics_connection(populated_db)
        try:
            result = get_overview(conn)
            # 5 total entries
            assert result["total_requests"] == 5
            assert result["total_tokens"] == 620  # 150+300+50+120+0
        finally:
            conn.close()

    def test_overview_communities(self, populated_db):
        from src.metrics.queries import get_overview

        conn = get_metrics_connection(populated_db)
        try:
            result = get_overview(conn)
            communities = {c["community_id"]: c for c in result["communities"]}
            assert "hed" in communities
            assert "bids" in communities
            assert communities["hed"]["requests"] == 3
            assert communities["bids"]["requests"] == 1
        finally:
            conn.close()

    def test_error_rate_in_overview(self, populated_db):
        from src.metrics.queries import get_overview

        conn = get_metrics_connection(populated_db)
        try:
            result = get_overview(conn)
            # 1 error out of 5 requests
            assert abs(result["error_rate"] - 0.2) < 0.01
        finally:
            conn.close()


class TestGetTokenBreakdown:
    """Tests for get_token_breakdown()."""

    def test_by_model(self, populated_db):
        from src.metrics.queries import get_token_breakdown

        conn = get_metrics_connection(populated_db)
        try:
            result = get_token_breakdown(conn)
            models = {m["model"]: m for m in result["by_model"]}
            assert "qwen/qwen3-235b" in models
            assert models["qwen/qwen3-235b"]["requests"] == 3
        finally:
            conn.close()

    def test_by_key_source(self, populated_db):
        from src.metrics.queries import get_token_breakdown

        conn = get_metrics_connection(populated_db)
        try:
            result = get_token_breakdown(conn)
            sources = {s["key_source"]: s for s in result["by_key_source"]}
            assert "platform" in sources
            assert "byok" in sources
            assert sources["platform"]["requests"] == 2
            assert sources["byok"]["requests"] == 2
        finally:
            conn.close()

    def test_filter_by_community(self, populated_db):
        from src.metrics.queries import get_token_breakdown

        conn = get_metrics_connection(populated_db)
        try:
            result = get_token_breakdown(conn, community_id="bids")
            assert result["community_id"] == "bids"
            assert len(result["by_model"]) == 1
            assert result["by_model"][0]["model"] == "qwen/qwen3-235b"
        finally:
            conn.close()

    def test_empty_db_returns_empty(self, tmp_path):
        from src.metrics.queries import get_token_breakdown

        db_path = tmp_path / "empty.db"
        init_metrics_db(db_path)
        conn = get_metrics_connection(db_path)
        try:
            result = get_token_breakdown(conn)
            assert result["by_model"] == []
            assert result["by_key_source"] == []
        finally:
            conn.close()
