"""Tests for sync status API endpoints."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_db(tmp_path: Path):
    """Mock database path for isolated tests."""
    db_path = tmp_path / "knowledge" / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        # Initialize database
        from src.knowledge.db import init_db

        init_db()
        yield db_path


class TestSyncStatus:
    """Tests for GET /sync/status endpoint."""

    def test_status_returns_200(self, client: TestClient, mock_db: Path):
        """Test that status endpoint returns 200."""
        with patch("src.api.routers.sync.get_db_path", return_value=mock_db):
            response = client.get("/sync/status")
            assert response.status_code == 200

    def test_status_structure(self, client: TestClient, mock_db: Path):
        """Test that status response has expected structure."""
        with patch("src.api.routers.sync.get_db_path", return_value=mock_db):
            response = client.get("/sync/status")
            data = response.json()

            # Check top-level keys
            assert "github" in data
            assert "papers" in data
            assert "scheduler" in data
            assert "health" in data
            assert "database_path" in data

    def test_github_status_structure(self, client: TestClient, mock_db: Path):
        """Test GitHub status has expected fields."""
        with patch("src.api.routers.sync.get_db_path", return_value=mock_db):
            response = client.get("/sync/status")
            github = response.json()["github"]

            assert "total_items" in github
            assert "issues" in github
            assert "prs" in github
            assert "open_items" in github
            assert "repos" in github

    def test_papers_status_structure(self, client: TestClient, mock_db: Path):
        """Test papers status has expected fields."""
        with patch("src.api.routers.sync.get_db_path", return_value=mock_db):
            response = client.get("/sync/status")
            papers = response.json()["papers"]

            assert "total_items" in papers
            assert "sources" in papers
            assert "openalex" in papers["sources"]
            assert "semanticscholar" in papers["sources"]
            assert "pubmed" in papers["sources"]

    def test_scheduler_status_structure(self, client: TestClient, mock_db: Path):
        """Test scheduler status has expected fields."""
        with patch("src.api.routers.sync.get_db_path", return_value=mock_db):
            response = client.get("/sync/status")
            scheduler = response.json()["scheduler"]

            assert "enabled" in scheduler
            assert "running" in scheduler
            assert "github_cron" in scheduler
            assert "papers_cron" in scheduler

    def test_health_status_structure(self, client: TestClient, mock_db: Path):
        """Test health status has expected fields."""
        with patch("src.api.routers.sync.get_db_path", return_value=mock_db):
            response = client.get("/sync/status")
            health = response.json()["health"]

            assert "healthy" in health
            assert "github_healthy" in health
            assert "papers_healthy" in health


class TestSyncHealth:
    """Tests for GET /sync/health endpoint."""

    def test_health_returns_status(self, client: TestClient, mock_db: Path):
        """Test health endpoint returns status."""
        with patch("src.api.routers.sync.get_db_path", return_value=mock_db):
            # Empty DB will be unhealthy (no syncs yet)
            response = client.get("/sync/health")
            # May be 200 or 503 depending on health state
            assert response.status_code in (200, 503)
            data = response.json()
            assert "status" in data or "detail" in data


class TestSyncTrigger:
    """Tests for POST /sync/trigger endpoint."""

    def test_trigger_endpoint_exists(self, client: TestClient):
        """Test that trigger endpoint exists and responds."""
        response = client.post("/sync/trigger", json={"sync_type": "github"})
        # Endpoint exists - response depends on auth config and sync state
        # 401/403: Auth required but not provided
        # 200: Success (auth disabled or valid key)
        # 400: Invalid sync_type
        # 500: Sync error
        assert response.status_code in (200, 400, 401, 403, 500)

    def test_trigger_invalid_sync_type(self, client: TestClient):
        """Test that invalid sync_type is rejected (if auth passes)."""
        # Use BYOK header to bypass any auth config
        response = client.post(
            "/sync/trigger",
            json={"sync_type": "invalid_type"},
            headers={"X-OpenRouter-API-Key": "byok-bypass"},  # BYOK bypasses auth
        )

        # Should get 400 for invalid type (not 401/403)
        assert response.status_code == 400

    def test_trigger_valid_sync_types(self, client: TestClient):
        """Test that valid sync_types are accepted."""
        for sync_type in ["github", "papers", "all"]:
            response = client.post(
                "/sync/trigger",
                json={"sync_type": sync_type},
                headers={"X-OpenRouter-API-Key": "byok-bypass"},
            )
            # Should not get 400 (invalid type) - may get 200/500 depending on sync
            assert response.status_code != 400, f"Valid sync_type '{sync_type}' rejected"
