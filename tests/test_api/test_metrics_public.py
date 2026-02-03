"""Tests for public metrics API endpoints."""

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
            tools_called=["search_docs", "validate_hed"],
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
            tools_called=["search_docs"],
            key_source="byok",
        ),
        RequestLogEntry(
            request_id="r3",
            timestamp="2025-01-16T09:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=100.0,
            status_code=500,
        ),
        RequestLogEntry(
            request_id="r4",
            timestamp="2025-01-15T12:00:00+00:00",
            endpoint="/bids/ask",
            method="POST",
            community_id="bids",
            duration_ms=250.0,
            status_code=200,
            model="anthropic/claude-sonnet",
            input_tokens=150,
            output_tokens=75,
            total_tokens=225,
            key_source="platform",
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
def noauth_env():
    """Disable auth requirement."""
    from src.api.config import get_settings

    os.environ["REQUIRE_API_AUTH"] = "false"
    get_settings.cache_clear()
    yield
    del os.environ["REQUIRE_API_AUTH"]
    get_settings.cache_clear()


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestPublicOverview:
    """Tests for GET /metrics/public/overview."""

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_returns_200_without_auth(self, client):
        response = client.get("/metrics/public/overview")
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_response_structure(self, client):
        response = client.get("/metrics/public/overview")
        data = response.json()
        assert "total_requests" in data
        assert "error_rate" in data
        assert "communities_active" in data
        assert "communities" in data

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_no_sensitive_fields(self, client):
        response = client.get("/metrics/public/overview")
        data = response.json()
        assert "total_tokens" not in data
        assert "total_estimated_cost" not in data
        assert "avg_duration_ms" not in data

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_request_counts_correct(self, client):
        response = client.get("/metrics/public/overview")
        data = response.json()
        assert data["total_requests"] == 4
        assert data["communities_active"] == 2

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_error_rate_includes_errors(self, client):
        response = client.get("/metrics/public/overview")
        data = response.json()
        # 1 error out of 4 requests = 0.25
        assert data["error_rate"] == 0.25

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_community_breakdown_no_sensitive_fields(self, client):
        response = client.get("/metrics/public/overview")
        data = response.json()
        for community in data["communities"]:
            assert "community_id" in community
            assert "requests" in community
            assert "error_rate" in community
            assert "tokens" not in community
            assert "estimated_cost" not in community


class TestPublicCommunitySummary:
    """Tests for GET /metrics/public/{community_id}."""

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_returns_200_without_auth(self, client):
        response = client.get("/metrics/public/hed")
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_response_structure(self, client):
        response = client.get("/metrics/public/hed")
        data = response.json()
        assert data["community_id"] == "hed"
        assert "total_requests" in data
        assert "error_rate" in data
        assert "top_tools" in data

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_no_sensitive_fields(self, client):
        response = client.get("/metrics/public/hed")
        data = response.json()
        assert "total_tokens" not in data
        assert "total_estimated_cost" not in data
        assert "top_models" not in data
        assert "avg_duration_ms" not in data

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_top_tools_populated(self, client):
        response = client.get("/metrics/public/hed")
        data = response.json()
        tools = {t["tool"]: t["count"] for t in data["top_tools"]}
        assert "search_docs" in tools
        assert tools["search_docs"] == 2

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_unknown_community_returns_empty(self, client):
        response = client.get("/metrics/public/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["total_requests"] == 0


class TestPublicCommunityUsage:
    """Tests for GET /metrics/public/{community_id}/usage."""

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_daily_usage_returns_200(self, client):
        response = client.get("/metrics/public/hed/usage", params={"period": "daily"})
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_daily_usage_structure(self, client):
        response = client.get("/metrics/public/hed/usage", params={"period": "daily"})
        data = response.json()
        assert data["community_id"] == "hed"
        assert data["period"] == "daily"
        assert "buckets" in data

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_buckets_no_sensitive_fields(self, client):
        response = client.get("/metrics/public/hed/usage", params={"period": "daily"})
        data = response.json()
        for bucket in data["buckets"]:
            assert "bucket" in bucket
            assert "requests" in bucket
            assert "errors" in bucket
            assert "tokens" not in bucket
            assert "estimated_cost" not in bucket
            assert "avg_duration_ms" not in bucket

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_monthly_usage(self, client):
        response = client.get("/metrics/public/hed/usage", params={"period": "monthly"})
        assert response.status_code == 200
        data = response.json()
        assert data["period"] == "monthly"

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_weekly_usage(self, client):
        response = client.get("/metrics/public/hed/usage", params={"period": "weekly"})
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_invalid_period_returns_422(self, client):
        response = client.get("/metrics/public/hed/usage", params={"period": "hourly"})
        assert response.status_code == 422

    @pytest.mark.usefixtures("isolated_metrics", "noauth_env")
    def test_default_period_is_daily(self, client):
        response = client.get("/metrics/public/hed/usage")
        assert response.status_code == 200
        data = response.json()
        assert data["period"] == "daily"
