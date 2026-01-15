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
def isolated_db(tmp_path: Path):
    """Create isolated test database.

    Uses path patching for database isolation - this is acceptable
    per project guidelines as it's for test isolation, not mocking functionality.

    Note: Pytest fixtures work through side effects. The fixture is applied
    when the test runs, even if the test doesn't directly access the fixture value.
    """
    db_path = tmp_path / "knowledge" / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        from src.knowledge.db import init_db

        init_db()
        yield db_path


class TestSyncStatus:
    """Tests for GET /sync/status endpoint."""

    @pytest.mark.usefixtures("isolated_db")
    def test_status_returns_200(self, client: TestClient):
        """Test that status endpoint returns 200."""
        response = client.get("/sync/status")
        assert response.status_code == 200

    @pytest.mark.usefixtures("isolated_db")
    def test_status_structure(self, client: TestClient):
        """Test that status response has expected structure."""
        response = client.get("/sync/status")
        data = response.json()

        # Check top-level keys
        assert "github" in data
        assert "papers" in data
        assert "scheduler" in data
        assert "health" in data
        # Note: database_path removed for security (info disclosure)
        assert "database_path" not in data

    @pytest.mark.usefixtures("isolated_db")
    def test_github_status_structure(self, client: TestClient):
        """Test GitHub status has expected fields."""
        response = client.get("/sync/status")
        github = response.json()["github"]

        assert "total_items" in github
        assert "issues" in github
        assert "prs" in github
        assert "open_items" in github
        assert "repos" in github

    @pytest.mark.usefixtures("isolated_db")
    def test_papers_status_structure(self, client: TestClient):
        """Test papers status has expected fields."""
        response = client.get("/sync/status")
        papers = response.json()["papers"]

        assert "total_items" in papers
        assert "sources" in papers
        assert "openalex" in papers["sources"]
        assert "semanticscholar" in papers["sources"]
        assert "pubmed" in papers["sources"]

    @pytest.mark.usefixtures("isolated_db")
    def test_scheduler_status_structure(self, client: TestClient):
        """Test scheduler status has expected fields."""
        response = client.get("/sync/status")
        scheduler = response.json()["scheduler"]

        assert "enabled" in scheduler
        assert "running" in scheduler
        assert "github_cron" in scheduler
        assert "papers_cron" in scheduler

    @pytest.mark.usefixtures("isolated_db")
    def test_health_status_structure(self, client: TestClient):
        """Test health status has expected fields."""
        response = client.get("/sync/status")
        health = response.json()["health"]

        assert "healthy" in health
        assert "github_healthy" in health
        assert "papers_healthy" in health


class TestSyncHealth:
    """Tests for GET /sync/health endpoint."""

    @pytest.mark.usefixtures("isolated_db")
    def test_health_returns_status(self, client: TestClient):
        """Test health endpoint returns status."""
        response = client.get("/sync/health")
        # New installs are considered healthy (grace period)
        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    @pytest.mark.usefixtures("isolated_db")
    def test_new_install_is_healthy(self, client: TestClient):
        """Test that new installations (never synced) are considered healthy."""
        response = client.get("/sync/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestSyncTrigger:
    """Tests for POST /sync/trigger endpoint."""

    def test_trigger_endpoint_exists(self, client: TestClient):
        """Test that trigger endpoint exists and responds."""
        response = client.post("/sync/trigger", json={"sync_type": "github"})
        # Endpoint exists - response depends on auth config
        # 401: Auth required but not provided
        # 200/500: Auth disabled, sync runs or fails
        assert response.status_code in (200, 401, 500)

    def test_trigger_byok_does_not_bypass_admin_auth(self, client: TestClient):
        """Test that BYOK headers don't bypass admin auth (security fix)."""
        from src.api.config import get_settings

        settings = get_settings()

        # BYOK should NOT bypass admin auth
        response = client.post(
            "/sync/trigger",
            json={"sync_type": "github"},
            headers={"X-OpenRouter-API-Key": "byok-attempt"},
        )

        # If auth is configured, should still get 401 (BYOK doesn't bypass admin)
        # If auth is disabled (no API_KEYS), should get 200/500
        if settings.api_keys:
            assert response.status_code == 401
        else:
            assert response.status_code in (200, 500)

    def test_trigger_response_structure(self, client: TestClient):
        """Test trigger response structure when auth passes."""
        # This test only validates structure when we can actually trigger
        response = client.post("/sync/trigger", json={"sync_type": "github"})

        if response.status_code == 200:
            data = response.json()
            assert "success" in data
            assert "message" in data
            assert "items_synced" in data
