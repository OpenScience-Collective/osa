"""Tests for metrics API endpoints."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.metrics.db import (
    RequestLogEntry,
    init_metrics_db,
    log_request,
)

ADMIN_KEY = "test-metrics-admin-key"
COMMUNITY_KEY = "hed-community-key"


@pytest.fixture
def metrics_db(tmp_path):
    """Create isolated metrics database with sample data."""
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
            tool_call_count=1,
            langfuse_trace_id="trace-001",
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
            key_source="byok",
            tool_call_count=0,
        ),
        RequestLogEntry(
            request_id="r3",
            timestamp="2025-01-15T12:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=500.0,
            status_code=500,
            model="qwen/qwen3-235b",
            error_message="LLM timeout",
            tool_call_count=0,
        ),
    ]
    for e in entries:
        log_request(e, db_path=db_path)

    return db_path


@pytest.fixture
def isolated_metrics(metrics_db):
    """Patch metrics DB path for all metrics code."""
    with patch("src.metrics.db.get_metrics_db_path", return_value=metrics_db):
        yield metrics_db


@pytest.fixture
def auth_env():
    """Set up environment for admin auth and clear settings cache."""
    from src.api.config import get_settings

    os.environ["API_KEYS"] = ADMIN_KEY
    os.environ["REQUIRE_API_AUTH"] = "true"
    get_settings.cache_clear()

    yield

    del os.environ["API_KEYS"]
    del os.environ["REQUIRE_API_AUTH"]
    get_settings.cache_clear()


@pytest.fixture
def scoped_auth_env():
    """Set up environment with both admin and community keys."""
    from src.api.config import get_settings

    os.environ["API_KEYS"] = ADMIN_KEY
    os.environ["REQUIRE_API_AUTH"] = "true"
    os.environ["COMMUNITY_ADMIN_KEYS"] = f"hed:{COMMUNITY_KEY}"
    get_settings.cache_clear()

    yield

    del os.environ["API_KEYS"]
    del os.environ["REQUIRE_API_AUTH"]
    del os.environ["COMMUNITY_ADMIN_KEYS"]
    get_settings.cache_clear()


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestMetricsOverview:
    """Tests for GET /metrics/overview."""

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_returns_200_with_admin_key(self, client):
        response = client.get("/metrics/overview", headers={"X-API-Key": ADMIN_KEY})
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_response_structure(self, client):
        response = client.get("/metrics/overview", headers={"X-API-Key": ADMIN_KEY})
        data = response.json()
        assert "total_requests" in data
        assert "total_tokens" in data
        assert "avg_duration_ms" in data
        assert "error_rate" in data
        assert "communities" in data

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_returns_401_without_key(self, client):
        response = client.get("/metrics/overview")
        assert response.status_code == 401

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_returns_403_with_invalid_key(self, client):
        response = client.get("/metrics/overview", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 403


class TestTokenBreakdown:
    """Tests for GET /metrics/tokens."""

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_returns_200(self, client):
        response = client.get("/metrics/tokens", headers={"X-API-Key": ADMIN_KEY})
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_response_structure(self, client):
        response = client.get("/metrics/tokens", headers={"X-API-Key": ADMIN_KEY})
        data = response.json()
        assert "by_model" in data
        assert "by_key_source" in data

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_filter_by_community(self, client):
        response = client.get(
            "/metrics/tokens",
            params={"community_id": "hed"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        data = response.json()
        assert data["community_id"] == "hed"

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_requires_admin_auth(self, client):
        response = client.get("/metrics/tokens")
        assert response.status_code == 401


class TestCommunityMetrics:
    """Tests for GET /{community_id}/metrics."""

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_returns_200(self, client):
        response = client.get("/hed/metrics", headers={"X-API-Key": ADMIN_KEY})
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_response_structure(self, client):
        response = client.get("/hed/metrics", headers={"X-API-Key": ADMIN_KEY})
        data = response.json()
        assert data["community_id"] == "hed"
        assert "total_requests" in data
        assert "total_tokens" in data

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_requires_admin_auth(self, client):
        response = client.get("/hed/metrics")
        assert response.status_code == 401


class TestCommunityUsage:
    """Tests for GET /{community_id}/metrics/usage."""

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_daily_usage(self, client):
        response = client.get(
            "/hed/metrics/usage",
            params={"period": "daily"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["period"] == "daily"
        assert "buckets" in data

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_monthly_usage(self, client):
        response = client.get(
            "/hed/metrics/usage",
            params={"period": "monthly"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_invalid_period_returns_422(self, client):
        """Invalid period rejected by Query pattern validation."""
        response = client.get(
            "/hed/metrics/usage",
            params={"period": "hourly"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert response.status_code == 422

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_requires_admin_auth(self, client):
        response = client.get("/hed/metrics/usage", params={"period": "daily"})
        assert response.status_code == 401


class TestCommunityQuality:
    """Tests for GET /{community_id}/metrics/quality."""

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_returns_200(self, client):
        response = client.get("/hed/metrics/quality", headers={"X-API-Key": ADMIN_KEY})
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_response_structure(self, client):
        response = client.get("/hed/metrics/quality", headers={"X-API-Key": ADMIN_KEY})
        data = response.json()
        assert data["community_id"] == "hed"
        assert data["period"] == "daily"
        assert "buckets" in data
        assert len(data["buckets"]) > 0
        bucket = data["buckets"][0]
        assert "requests" in bucket
        assert "error_rate" in bucket
        assert "avg_tool_calls" in bucket
        assert "agent_errors" in bucket
        assert "traced_requests" in bucket
        assert "p50_duration_ms" in bucket
        assert "p95_duration_ms" in bucket

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_weekly_period(self, client):
        response = client.get(
            "/hed/metrics/quality",
            params={"period": "weekly"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert response.status_code == 200
        assert response.json()["period"] == "weekly"

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_invalid_period_returns_422(self, client):
        response = client.get(
            "/hed/metrics/quality",
            params={"period": "hourly"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert response.status_code == 422

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_requires_auth(self, client):
        response = client.get("/hed/metrics/quality")
        assert response.status_code == 401

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_quality_data_reflects_errors(self, client):
        """Verify error data from fixture is reflected in quality metrics."""
        response = client.get("/hed/metrics/quality", headers={"X-API-Key": ADMIN_KEY})
        data = response.json()
        # All 3 entries are on 2025-01-15, so one daily bucket
        assert len(data["buckets"]) == 1
        bucket = data["buckets"][0]
        assert bucket["requests"] == 3
        # 1 out of 3 requests has status_code >= 400
        assert bucket["error_rate"] > 0
        assert bucket["agent_errors"] == 1
        assert bucket["traced_requests"] == 1


class TestCommunityQualitySummary:
    """Tests for GET /{community_id}/metrics/quality/summary."""

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_returns_200(self, client):
        response = client.get("/hed/metrics/quality/summary", headers={"X-API-Key": ADMIN_KEY})
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_response_structure(self, client):
        response = client.get("/hed/metrics/quality/summary", headers={"X-API-Key": ADMIN_KEY})
        data = response.json()
        assert data["community_id"] == "hed"
        assert "total_requests" in data
        assert "error_rate" in data
        assert "avg_tool_calls" in data
        assert "agent_errors" in data
        assert "traced_pct" in data
        assert "p50_duration_ms" in data
        assert "p95_duration_ms" in data

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_summary_values(self, client):
        """Verify summary aggregates match fixture data."""
        response = client.get("/hed/metrics/quality/summary", headers={"X-API-Key": ADMIN_KEY})
        data = response.json()
        assert data["total_requests"] == 3
        assert data["agent_errors"] == 1
        # 1 traced out of 3
        assert 0 < data["traced_pct"] < 1

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_requires_auth(self, client):
        response = client.get("/hed/metrics/quality/summary")
        assert response.status_code == 401


class TestGlobalQuality:
    """Tests for GET /metrics/quality."""

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_returns_200_for_admin(self, client):
        response = client.get("/metrics/quality", headers={"X-API-Key": ADMIN_KEY})
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_admin_sees_communities_list(self, client):
        response = client.get("/metrics/quality", headers={"X-API-Key": ADMIN_KEY})
        data = response.json()
        assert "communities" in data
        assert len(data["communities"]) > 0
        community = data["communities"][0]
        assert community["community_id"] == "hed"
        assert "error_rate" in community
        assert "avg_tool_calls" in community

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_requires_auth(self, client):
        response = client.get("/metrics/quality")
        assert response.status_code == 401

    @pytest.mark.usefixtures("isolated_metrics", "auth_env")
    def test_returns_403_with_invalid_key(self, client):
        response = client.get("/metrics/quality", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 403


class TestScopedKeyOnGlobalEndpoints:
    """Tests for community-scoped keys on global metrics endpoints."""

    @pytest.mark.usefixtures("isolated_metrics", "scoped_auth_env")
    def test_community_key_on_overview_returns_scoped_data(self, client):
        """Community key on /metrics/overview should return only their community summary."""
        response = client.get("/metrics/overview", headers={"X-API-Key": COMMUNITY_KEY})
        assert response.status_code == 200
        data = response.json()
        # Scoped key gets community summary, not the full overview with communities list
        assert data["community_id"] == "hed"

    @pytest.mark.usefixtures("isolated_metrics", "scoped_auth_env")
    def test_community_key_on_tokens_is_auto_scoped(self, client):
        """Community key on /metrics/tokens should be auto-scoped to own community."""
        response = client.get(
            "/metrics/tokens",
            params={"community_id": "eeglab"},  # Try to view another community
            headers={"X-API-Key": COMMUNITY_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        # Should be forced to own community, ignoring the query param
        assert data["community_id"] == "hed"

    @pytest.mark.usefixtures("isolated_metrics", "scoped_auth_env")
    def test_community_key_on_quality_returns_own_summary(self, client):
        """Community key on /metrics/quality should return only own community quality."""
        response = client.get("/metrics/quality", headers={"X-API-Key": COMMUNITY_KEY})
        assert response.status_code == 200
        data = response.json()
        # Scoped key gets single community summary, not communities list
        assert data["community_id"] == "hed"
        assert "error_rate" in data

    @pytest.mark.usefixtures("isolated_metrics", "scoped_auth_env")
    def test_admin_key_still_sees_all(self, client):
        """Admin key should still see full data on all global endpoints."""
        response = client.get("/metrics/overview", headers={"X-API-Key": ADMIN_KEY})
        assert response.status_code == 200
        data = response.json()
        assert "communities" in data
