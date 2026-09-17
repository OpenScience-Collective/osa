"""Tests for API security and authentication.

These tests use real HTTP requests against the actual FastAPI application
to verify authentication behavior.
"""

import os
import typing
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import security
from src.api.security import RequireAuth, api_key_header


def _resolvable_byok_headers() -> set[str]:
    """Header names that ``resolve_byok`` turns into a usable credential.

    Derived from ``ByokCredential.provider``, the type that says what a
    resolved BYOK credential can be, mapped back to header names through the
    ``{provider}_key_header`` convention in src/api/security.py. Deriving it
    means a provider added to ``resolve_byok`` cannot be left out of the
    server-auth bypass, and a bypass header with no provider behind it cannot
    be left in.
    """
    providers = typing.get_args(typing.get_type_hints(security.ByokCredential)["provider"])
    return {getattr(security, f"{provider}_key_header").model.name for provider in providers}


def _headers_read_by(dependency: Callable) -> set[str]:
    """Names of every APIKeyHeader a FastAPI dependency declares in its signature.

    Reads the ``Security(...)`` markers out of the annotations, which is where
    a header parameter is actually wired up, so a parameter that is declared
    but never used still counts. That is the shape of issue #393: the bypass
    read a header nothing downstream could resolve.
    """
    names = set()
    for annotation in typing.get_type_hints(dependency, include_extras=True).values():
        for marker in getattr(annotation, "__metadata__", ()):
            model = getattr(getattr(marker, "dependency", None), "model", None)
            if getattr(model, "name", None):
                names.add(model.name)
    return names


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

    def test_unresolvable_byok_header_does_not_bypass_server_auth(
        self, client_with_auth: TestClient
    ) -> None:
        """A key header for a provider the server cannot use must not open the door.

        X-OpenAI-API-Key used to bypass server auth, from back when there was
        an OpenAI code path. There is not one now, and resolve_byok ignores the
        header, so the request would have run on the community's key or the
        platform's: free answers on our bill for anyone who sent the header
        with any value at all (issue #393).
        """
        response = client_with_auth.get(
            "/protected",
            headers={"X-OpenAI-API-Key": "literally-anything"},
        )
        assert response.status_code == 401
        assert "API key required" in response.json()["detail"]

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


class TestByokBypassCoversExactlyTheResolvableProviders:
    """The bypass and resolve_byok have to agree on which headers count.

    The bypass is only safe because the header it accepts is the credential the
    request then runs on: send junk and the call fails against your own
    provider, so there is nothing to gain. A header that bypasses but is not
    resolved breaks that argument, and a provider that resolves but does not
    bypass would make BYOK callers hit an unnecessary 401.
    """

    def test_verify_api_key_reads_exactly_the_resolvable_byok_headers(self) -> None:
        byok_headers = _headers_read_by(security.verify_api_key) - {api_key_header.model.name}
        assert byok_headers == _resolvable_byok_headers()

    @pytest.mark.parametrize("header_name", sorted(_resolvable_byok_headers()))
    def test_each_resolvable_header_bypasses(
        self, client_with_auth: TestClient, header_name: str
    ) -> None:
        response = client_with_auth.get("/protected", headers={header_name: "user-supplied-key"})
        assert response.status_code == 200
        assert response.json()["has_key"] is False

    def test_admin_auth_reads_no_byok_headers_at_all(self) -> None:
        """Admin endpoints spend server resources, so BYOK never bypasses them."""
        assert _headers_read_by(security.verify_admin_api_key) == {api_key_header.model.name}
