"""Tests for API security and authentication.

These tests use real HTTP requests against the actual FastAPI application
to verify authentication behavior.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.security import RequireAuth


@pytest.fixture
def app_with_auth() -> FastAPI:
    """Create a test app with a protected endpoint."""
    # Set API keys in environment for this test
    os.environ["API_KEYS"] = "test-secret-key"
    os.environ["REQUIRE_API_AUTH"] = "true"

    # Clear the settings cache to pick up new env var
    from src.api.config import get_settings

    get_settings.cache_clear()

    app = FastAPI()

    @app.get("/protected")
    async def protected_route(auth: RequireAuth) -> dict:
        return {"message": "authenticated", "has_key": auth is not None}

    yield app

    # Cleanup
    del os.environ["API_KEYS"]
    del os.environ["REQUIRE_API_AUTH"]
    get_settings.cache_clear()


@pytest.fixture
def client_with_auth(app_with_auth: FastAPI) -> TestClient:
    """Create a test client for the auth-enabled app."""
    return TestClient(app_with_auth)


@pytest.fixture
def app_no_auth() -> FastAPI:
    """Create a test app without server authentication configured."""
    # Ensure auth is disabled
    if "API_KEYS" in os.environ:
        del os.environ["API_KEYS"]
    os.environ["REQUIRE_API_AUTH"] = "false"

    from src.api.config import get_settings

    get_settings.cache_clear()

    app = FastAPI()

    @app.get("/protected")
    async def protected_route(auth: RequireAuth) -> dict:
        return {"message": "no auth required", "has_key": auth is not None}

    yield app

    del os.environ["REQUIRE_API_AUTH"]
    get_settings.cache_clear()


@pytest.fixture
def client_no_auth(app_no_auth: FastAPI) -> TestClient:
    """Create a test client for the no-auth app."""
    return TestClient(app_no_auth)


class TestAPIKeyAuthentication:
    """Tests for API key authentication."""

    def test_protected_route_requires_key_when_configured(
        self, client_with_auth: TestClient
    ) -> None:
        """Protected route should require API key when server auth is enabled."""
        response = client_with_auth.get("/protected")
        assert response.status_code == 401
        assert "API key required" in response.json()["detail"]

    def test_byok_bypasses_server_auth_openrouter(self, client_with_auth: TestClient) -> None:
        """OpenRouter BYOK header should bypass server API key requirement."""
        response = client_with_auth.get(
            "/protected",
            headers={"X-OpenRouter-Key": "sk-or-user-key"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "authenticated"
        assert data["has_key"] is False

    def test_byok_bypasses_server_auth_openai(self, client_with_auth: TestClient) -> None:
        """OpenAI BYOK header should bypass server API key requirement."""
        response = client_with_auth.get(
            "/protected",
            headers={"X-OpenAI-API-Key": "sk-openai-user-key"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "authenticated"
        assert data["has_key"] is False

    def test_byok_bypasses_server_auth_anthropic(self, client_with_auth: TestClient) -> None:
        """Anthropic BYOK header should bypass server API key requirement."""
        response = client_with_auth.get(
            "/protected",
            headers={"X-Anthropic-API-Key": "sk-ant-user-key"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "authenticated"
        assert data["has_key"] is False

    def test_protected_route_rejects_invalid_key(self, client_with_auth: TestClient) -> None:
        """Protected route should reject invalid API key."""
        response = client_with_auth.get("/protected", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 403
        assert response.json()["detail"] == "Invalid API key"

    def test_protected_route_accepts_valid_key(self, client_with_auth: TestClient) -> None:
        """Protected route should accept valid API key."""
        response = client_with_auth.get("/protected", headers={"X-API-Key": "test-secret-key"})
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "authenticated"
        assert data["has_key"] is True

    def test_no_auth_required_when_not_configured(self, client_no_auth: TestClient) -> None:
        """Protected route should allow access when server auth is not configured."""
        response = client_no_auth.get("/protected")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "no auth required"
        assert data["has_key"] is False
