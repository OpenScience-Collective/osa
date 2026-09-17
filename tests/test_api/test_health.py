"""Tests for API health endpoints.

These tests use real HTTP requests against the actual FastAPI application,
not mocks. They verify the actual behavior of the health check endpoint.
"""

import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routers.health import compute_community_health
from src.assistants import discover_assistants, registry
from src.version import __version__

discover_assistants()


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI application."""
    # Disable auth requirement for health endpoint tests
    os.environ["REQUIRE_API_AUTH"] = "false"

    # Clear settings cache to pick up new env var
    from src.api.config import get_settings

    get_settings.cache_clear()

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


class TestCommunitiesHealthEndpoint:
    """Tests for the /health/communities endpoint."""

    def test_communities_health_endpoint_exists(self, client: TestClient) -> None:
        """Should respond to GET /health/communities."""
        response = client.get("/health/communities")
        assert response.status_code == 200

    def test_returns_dict_of_communities(self, client: TestClient) -> None:
        """Should return dictionary with community IDs as keys."""
        response = client.get("/health/communities")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, dict)

        # Should have at least one community (HED from test fixtures)
        assert len(data) > 0

    def test_community_status_structure(self, client: TestClient) -> None:
        """Should return correct structure for each community."""
        response = client.get("/health/communities")
        data = response.json()

        # Check structure of first community
        community_id = list(data.keys())[0]
        community_health = data[community_id]

        # Required fields
        assert "status" in community_health
        assert "api_key" in community_health
        assert "cors_origins" in community_health
        assert "documents" in community_health
        assert "sync_age_hours" in community_health

        # Status should be one of the valid values
        assert community_health["status"] in ["healthy", "degraded", "error"]

        # API key should be one of the valid values
        assert community_health["api_key"] in [
            "configured",
            "using_platform",
            "missing",
        ]

        # Counts should be non-negative integers
        assert isinstance(community_health["cors_origins"], int)
        assert community_health["cors_origins"] >= 0
        assert isinstance(community_health["documents"], int)
        assert community_health["documents"] >= 0

        # Sync age can be None or float
        assert community_health["sync_age_hours"] is None or isinstance(
            community_health["sync_age_hours"], (int, float)
        )

    def test_status_reflects_configuration(self, client: TestClient) -> None:
        """Status should reflect actual community configuration."""
        response = client.get("/health/communities")
        data = response.json()

        for community_id, health in data.items():
            # Error status if no documents or missing API key
            if health["documents"] == 0:
                assert health["status"] == "error", f"{community_id} should be error with no docs"

            # Error status if API key is missing (configured but env var not set)
            elif health["api_key"] == "missing":
                assert health["status"] == "error", (
                    f"{community_id} should be error with missing API key"
                )

            # Degraded if using platform key
            elif health["api_key"] == "using_platform":
                assert health["status"] == "degraded", (
                    f"{community_id} should be degraded with platform key"
                )

            # Healthy if has docs and own API key
            else:
                assert health["status"] == "healthy", (
                    f"{community_id} should be healthy with docs and own key"
                )

    def test_handles_missing_api_key_env_var(self, client: TestClient) -> None:
        """Should show error status when API key env var is configured but not set."""
        response = client.get("/health/communities")
        assert response.status_code == 200

        data = response.json()
        # If any community has missing API key (configured but env var not set),
        # it should show error status
        for _community_id, health in data.items():
            if health["api_key"] == "missing":
                assert health["status"] == "error"

    def test_handles_env_var_state_changes(self, client: TestClient) -> None:
        """Should reflect current env var state on each health check."""
        # First check - get baseline
        response1 = client.get("/health/communities")
        assert response1.status_code == 200
        response1.json()

        # Set a test env var that might be checked
        test_var_name = "OPENROUTER_API_KEY_TEST_COMMUNITY"
        original_value = os.environ.get(test_var_name)

        try:
            # Set the env var
            os.environ[test_var_name] = "sk-or-v1-test"

            # Second check - should reflect new state
            response2 = client.get("/health/communities")
            assert response2.status_code == 200
            # Response should still be valid even with env var changes
            data2 = response2.json()
            assert isinstance(data2, dict)

            # Remove the env var
            del os.environ[test_var_name]

            # Third check - should reflect removed state
            response3 = client.get("/health/communities")
            assert response3.status_code == 200
            data3 = response3.json()
            assert isinstance(data3, dict)

        finally:
            # Cleanup - restore original state
            if original_value is not None:
                os.environ[test_var_name] = original_value
            elif test_var_name in os.environ:
                del os.environ[test_var_name]

    def test_handles_malformed_assistant_info(self, client: TestClient) -> None:
        """Should handle assistant info with missing attributes gracefully."""
        # This test verifies the error handling at lines 65-90 in health.py
        # that catches AttributeError, KeyError, TypeError
        # The test relies on the existing behavior where the endpoint
        # returns error status for communities with missing attributes

        response = client.get("/health/communities")
        assert response.status_code == 200

        data = response.json()
        # The endpoint should still work even if some assistant infos are malformed
        assert isinstance(data, dict)

        # Check for communities with error status from malformed data
        for _community_id, health in data.items():
            if health.get("status") == "error" and any(
                "Failed to process" in w for w in health.get("warnings", [])
            ):
                assert health["api_key"] == "unknown"
                assert health["cors_origins"] == 0
                assert health["documents"] == 0
                assert health["sync_age_hours"] is None

    def test_communities_health_includes_warnings(self, client: TestClient) -> None:
        """Each community health entry should include a warnings list."""
        response = client.get("/health/communities")
        data = response.json()

        for community_id, health in data.items():
            assert "warnings" in health, f"{community_id} missing warnings field"
            assert isinstance(health["warnings"], list)


class TestComputeCommunityHealth:
    """Tests for the compute_community_health helper function."""

    def test_with_real_community_config(self) -> None:
        """Should compute health from a real community config."""
        assistants = registry.list_all()
        assert len(assistants) > 0

        config = assistants[0].community_config
        assert config is not None

        result = compute_community_health(config)
        assert result["status"] in ["healthy", "degraded", "error"]
        assert result["api_key"] in ["configured", "using_platform", "missing"]
        assert isinstance(result["cors_origins"], int)
        assert isinstance(result["documents"], int)
        assert isinstance(result["warnings"], list)

    def test_missing_openrouter_api_key_env_var_produces_warning(self, monkeypatch) -> None:
        """Should warn when the OpenRouter env var is configured but not set.

        No shipped community sets openrouter_api_key_env_var any more
        (issue #363: the four that used to are now platform-funded by
        default), but the field is still supported, so this monkeypatches
        it onto a real, registered CommunityConfig rather than searching
        the registry for a shipped one that no longer exists. Also clears
        anthropic_api_key_env_var, which compute_community_health now
        checks first, so the OpenRouter branch under test is actually
        reached.
        """
        info = registry.get("hed")
        assert info is not None and info.community_config is not None
        config = info.community_config
        env_var = "OPENROUTER_API_KEY_TEST_HED_HEALTH"
        monkeypatch.setattr(config, "anthropic_api_key_env_var", None)
        monkeypatch.setattr(config, "openrouter_api_key_env_var", env_var)
        monkeypatch.delenv(env_var, raising=False)

        result = compute_community_health(config)
        assert result["api_key"] == "missing"
        assert result["status"] == "error"
        assert any(env_var in w for w in result["warnings"])
        assert any("not sustainable" in w for w in result["warnings"])

    def test_set_openrouter_api_key_env_var_is_healthy(self, monkeypatch) -> None:
        """Should be healthy when the OpenRouter env var is set and docs exist."""
        info = registry.get("hed")
        assert info is not None and info.community_config is not None
        config = info.community_config
        assert config.documentation, "hed is expected to have documentation configured"
        env_var = "OPENROUTER_API_KEY_TEST_HED_HEALTH"
        monkeypatch.setattr(config, "anthropic_api_key_env_var", None)
        monkeypatch.setattr(config, "openrouter_api_key_env_var", env_var)
        monkeypatch.setenv(env_var, "sk-or-v1-test")

        result = compute_community_health(config)
        assert result["api_key"] == "configured"
        assert result["status"] == "healthy"
        assert not any(env_var in w for w in result["warnings"])

    def test_both_env_vars_configured_anthropic_wins(self, monkeypatch) -> None:
        """Anthropic wins when both key env vars are configured on the same config.

        Mirrors the same precedence gap fixed in test_authorization.py's
        test_authorized_origin_uses_community_anthropic_key: setting only
        the Anthropic field would not prove precedence, because hed's
        openrouter_api_key_env_var defaults to None, and `A or B` picks A
        regardless of check order whenever B is falsy.

        The Anthropic env var is left unset (missing) while the OpenRouter
        one is set to a real value. compute_community_health mirrors
        _resolve_provider and commits to whichever env var name it selects
        first, regardless of whether that var is actually set -- it never
        falls through to the other one. So correct (Anthropic-first)
        precedence reports "missing" here (Anthropic's name was selected,
        and it is unset), even though a populated OpenRouter var was
        available. An implementation that checked OpenRouter first would
        select the OpenRouter name instead, find it set, and incorrectly
        report "configured"/"healthy" -- which is exactly what this test
        would catch.

        Both underlying values are deliberately not "populated" in the
        sense of both holding a truthy key: if they were, the two
        precedence orders would produce identical output (either name
        picked, either found configured), and the test would not be able
        to tell them apart.
        """
        info = registry.get("hed")
        assert info is not None and info.community_config is not None
        config = info.community_config
        anthropic_env_var = "ANTHROPIC_API_KEY_TEST_HED_HEALTH_BOTH"
        openrouter_env_var = "OPENROUTER_API_KEY_TEST_HED_HEALTH_BOTH"
        monkeypatch.setattr(config, "anthropic_api_key_env_var", anthropic_env_var)
        monkeypatch.setattr(config, "openrouter_api_key_env_var", openrouter_env_var)
        monkeypatch.delenv(anthropic_env_var, raising=False)
        monkeypatch.setenv(openrouter_env_var, "sk-or-v1-test")

        result = compute_community_health(config)
        assert result["api_key"] == "missing"
        assert result["status"] == "error"
        assert any(anthropic_env_var in w for w in result["warnings"])
        assert not any(openrouter_env_var in w for w in result["warnings"])

    def test_missing_anthropic_api_key_env_var_produces_warning(self, monkeypatch) -> None:
        """A configured but unset anthropic_api_key_env_var reports missing/error.

        Regression coverage for the bug where compute_community_health
        consulted only openrouter_api_key_env_var: _resolve_provider checks
        the Anthropic env var first, so a community funded by Anthropic was
        reporting api_key="using_platform"/status="degraded" and a
        misleading "no community-specific key configured" warning even
        though a key env var was genuinely configured (just unset).
        """
        info = registry.get("hed")
        assert info is not None and info.community_config is not None
        config = info.community_config
        env_var = "ANTHROPIC_API_KEY_TEST_HED_HEALTH"
        monkeypatch.setattr(config, "anthropic_api_key_env_var", env_var)
        monkeypatch.delenv(env_var, raising=False)

        result = compute_community_health(config)
        assert result["api_key"] == "missing"
        assert result["status"] == "error"
        assert any(env_var in w for w in result["warnings"])
        assert any("not sustainable" in w for w in result["warnings"])

    def test_set_anthropic_api_key_env_var_is_healthy(self, monkeypatch) -> None:
        """A configured and set anthropic_api_key_env_var reports configured/healthy.

        Regression coverage for the same bug: before the fix this reported
        api_key="using_platform" regardless of whether the Anthropic key
        was actually configured and set.
        """
        info = registry.get("hed")
        assert info is not None and info.community_config is not None
        config = info.community_config
        assert config.documentation, "hed is expected to have documentation configured"
        env_var = "ANTHROPIC_API_KEY_TEST_HED_HEALTH"
        monkeypatch.setattr(config, "anthropic_api_key_env_var", env_var)
        monkeypatch.setenv(env_var, "sk-ant-test")

        result = compute_community_health(config)
        assert result["api_key"] == "configured"
        assert result["status"] == "healthy"
        assert not any(env_var in w for w in result["warnings"])
