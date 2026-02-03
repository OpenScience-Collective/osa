"""Tests for per-community scoped authentication.

Tests AuthScope, verify_scoped_admin_key, and per-community metrics access control.
Uses real HTTP requests against FastAPI test apps.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.config import Settings, get_settings
from src.api.security import AuthScope, RequireScopedAuth


@pytest.fixture
def app_scoped_auth() -> FastAPI:
    """Create a test app with scoped auth endpoints."""
    os.environ["API_KEYS"] = "global-admin-key"
    os.environ["REQUIRE_API_AUTH"] = "true"
    os.environ["COMMUNITY_ADMIN_KEYS"] = "hed:hed-key-1,eeglab:eeglab-key-1,hed:hed-key-2"

    get_settings.cache_clear()

    app = FastAPI()

    @app.get("/scoped")
    async def scoped_route(auth: RequireScopedAuth) -> dict:
        return {
            "role": auth.role,
            "community_id": auth.community_id,
        }

    @app.get("/metrics/{community_id}")
    async def community_metrics(community_id: str, auth: RequireScopedAuth) -> dict:
        if not auth.can_access_community(community_id):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Access denied")
        return {"community_id": community_id, "role": auth.role}

    yield app

    for key in ["API_KEYS", "REQUIRE_API_AUTH", "COMMUNITY_ADMIN_KEYS"]:
        os.environ.pop(key, None)
    get_settings.cache_clear()


@pytest.fixture
def client(app_scoped_auth: FastAPI) -> TestClient:
    return TestClient(app_scoped_auth)


class TestAuthScope:
    """Tests for AuthScope dataclass."""

    def test_admin_can_access_any_community(self):
        scope = AuthScope(role="admin")
        assert scope.can_access_community("hed") is True
        assert scope.can_access_community("eeglab") is True
        assert scope.can_access_community("anything") is True

    def test_community_scope_can_access_own(self):
        scope = AuthScope(role="community", community_id="hed")
        assert scope.can_access_community("hed") is True

    def test_community_scope_cannot_access_other(self):
        scope = AuthScope(role="community", community_id="hed")
        assert scope.can_access_community("eeglab") is False

    def test_community_role_requires_community_id(self):
        with pytest.raises(ValueError, match="community role requires a community_id"):
            AuthScope(role="community")

    def test_admin_role_rejects_community_id(self):
        with pytest.raises(ValueError, match="admin role must not have a community_id"):
            AuthScope(role="admin", community_id="hed")


class TestVerifyScopedAdminKey:
    """Tests for verify_scoped_admin_key dependency."""

    def test_global_admin_key_returns_admin_role(self, client: TestClient):
        resp = client.get("/scoped", headers={"X-API-Key": "global-admin-key"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "admin"
        assert data["community_id"] is None

    def test_community_key_returns_community_role(self, client: TestClient):
        resp = client.get("/scoped", headers={"X-API-Key": "hed-key-1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "community"
        assert data["community_id"] == "hed"

    def test_second_community_key_works(self, client: TestClient):
        resp = client.get("/scoped", headers={"X-API-Key": "hed-key-2"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "community"
        assert data["community_id"] == "hed"

    def test_eeglab_community_key(self, client: TestClient):
        resp = client.get("/scoped", headers={"X-API-Key": "eeglab-key-1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "community"
        assert data["community_id"] == "eeglab"

    def test_no_key_returns_401(self, client: TestClient):
        resp = client.get("/scoped")
        assert resp.status_code == 401

    def test_invalid_key_returns_403(self, client: TestClient):
        resp = client.get("/scoped", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 403


class TestScopedCommunityAccess:
    """Tests for per-community access control via scoped auth."""

    def test_admin_can_access_any_community_metrics(self, client: TestClient):
        resp = client.get("/metrics/hed", headers={"X-API-Key": "global-admin-key"})
        assert resp.status_code == 200
        assert resp.json()["community_id"] == "hed"

        resp = client.get("/metrics/eeglab", headers={"X-API-Key": "global-admin-key"})
        assert resp.status_code == 200
        assert resp.json()["community_id"] == "eeglab"

    def test_community_key_can_access_own_metrics(self, client: TestClient):
        resp = client.get("/metrics/hed", headers={"X-API-Key": "hed-key-1"})
        assert resp.status_code == 200
        assert resp.json()["community_id"] == "hed"

    def test_community_key_cannot_access_other_metrics(self, client: TestClient):
        resp = client.get("/metrics/eeglab", headers={"X-API-Key": "hed-key-1"})
        assert resp.status_code == 403


class TestScopedAuthDisabled:
    """Tests for scoped auth when authentication is disabled."""

    def test_no_auth_required_returns_admin_scope(self):
        os.environ["REQUIRE_API_AUTH"] = "false"
        os.environ.pop("API_KEYS", None)
        os.environ.pop("COMMUNITY_ADMIN_KEYS", None)
        get_settings.cache_clear()

        app = FastAPI()

        @app.get("/scoped")
        async def scoped_route(auth: RequireScopedAuth) -> dict:
            return {"role": auth.role, "community_id": auth.community_id}

        client = TestClient(app)
        resp = client.get("/scoped")
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "admin"
        assert data["community_id"] is None

        os.environ.pop("REQUIRE_API_AUTH", None)
        get_settings.cache_clear()


class TestParseAdminKeys:
    """Tests for Settings.parse_community_admin_keys()."""

    def test_parse_single_key(self):
        s = Settings(community_admin_keys="hed:abc123")
        result = s.parse_community_admin_keys()
        assert result == {"hed": {"abc123"}}

    def test_parse_multiple_communities(self):
        s = Settings(community_admin_keys="hed:key1,eeglab:key2")
        result = s.parse_community_admin_keys()
        assert result == {"hed": {"key1"}, "eeglab": {"key2"}}

    def test_parse_multiple_keys_same_community(self):
        s = Settings(community_admin_keys="hed:key1,hed:key2")
        result = s.parse_community_admin_keys()
        assert result == {"hed": {"key1", "key2"}}

    def test_parse_empty(self):
        s = Settings(community_admin_keys=None)
        result = s.parse_community_admin_keys()
        assert result == {}

    def test_parse_with_spaces(self):
        s = Settings(community_admin_keys=" hed : key1 , eeglab : key2 ")
        result = s.parse_community_admin_keys()
        assert result == {"hed": {"key1"}, "eeglab": {"key2"}}

    def test_parse_ignores_malformed_entries(self):
        s = Settings(community_admin_keys="hed:key1,badentry,eeglab:key2")
        result = s.parse_community_admin_keys()
        assert result == {"hed": {"key1"}, "eeglab": {"key2"}}
