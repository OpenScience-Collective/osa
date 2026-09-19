"""Tests for mirror endpoint authentication.

These use real HTTP requests against the actual FastAPI application, hitting
only the read-only GET /mirrors endpoint so a run against a real environment
never creates or mutates an actual mirror. Full mirror CRUD behavior is
covered at the unit level in tests/test_knowledge/test_mirror.py and
tests/test_cli/test_mirror.py; every mirror route (including the
state-changing ones) is checked structurally, with no HTTP calls, by
tests/test_api/test_security.py's
TestEndpointsThatDoNotSpendByokRequireAdminAuth.
"""

from fastapi.testclient import TestClient

from src.api.config import get_settings
from src.api.main import app


class TestMirrorEndpointsRequireAdminAuth:
    def _client(self) -> TestClient:
        return TestClient(app)

    def test_list_mirrors_byok_does_not_bypass_admin_auth(self) -> None:
        settings = get_settings()
        response = self._client().get("/mirrors", headers={"X-Anthropic-API-Key": "byok-attempt"})
        # If admin auth is actually enforced, BYOK must not bypass it
        # (issue #393's bug class, reintroduced by reusing RequireAuth on
        # this router -- see the module docstring on mirrors.py).
        if settings.api_keys and settings.require_api_auth:
            assert response.status_code == 401
        else:
            assert response.status_code == 200

    def test_list_mirrors_without_any_key_matches_configured_auth(self) -> None:
        settings = get_settings()
        response = self._client().get("/mirrors")
        if settings.api_keys and settings.require_api_auth:
            assert response.status_code == 401
        else:
            assert response.status_code == 200
