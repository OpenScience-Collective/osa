"""Tests for API health endpoints.

These tests use real HTTP requests against the actual FastAPI application,
not mocks. They verify the actual behavior of the health check endpoint.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.version import __version__


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI application."""
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_200(self, client: TestClient) -> None:
        """Health endpoint should return 200 OK."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_healthy_status(self, client: TestClient) -> None:
        """Health endpoint should return status 'healthy'."""
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_returns_version(self, client: TestClient) -> None:
        """Health endpoint should return application version."""
        response = client.get("/health")
        data = response.json()
        assert "version" in data
        assert data["version"] == __version__

    def test_health_returns_valid_timestamp(self, client: TestClient) -> None:
        """Health endpoint should return a valid ISO format timestamp."""
        response = client.get("/health")
        data = response.json()
        assert "timestamp" in data
        # Verify it's a valid ISO timestamp
        timestamp = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
        assert timestamp is not None

    def test_health_returns_environment(self, client: TestClient) -> None:
        """Health endpoint should return environment info."""
        response = client.get("/health")
        data = response.json()
        assert "environment" in data
        assert data["environment"] in ["development", "production"]


class TestRootEndpoint:
    """Tests for the root / endpoint."""

    def test_root_returns_200(self, client: TestClient) -> None:
        """Root endpoint should return 200 OK."""
        response = client.get("/")
        assert response.status_code == 200

    def test_root_returns_app_name(self, client: TestClient) -> None:
        """Root endpoint should return application name."""
        response = client.get("/")
        data = response.json()
        assert "name" in data
        assert data["name"] == "Open Science Assistant"

    def test_root_returns_version(self, client: TestClient) -> None:
        """Root endpoint should return version."""
        response = client.get("/")
        data = response.json()
        assert "version" in data
        assert data["version"] == __version__
