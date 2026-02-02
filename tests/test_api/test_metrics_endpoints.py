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
