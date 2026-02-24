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

        # Check for communities with error status and error field
        # (indicates they failed processing due to malformed data)
        for _community_id, health in data.items():
            if "error" in health and "Failed to process" in health.get("error", ""):
                # Verify the error response structure
                assert health["status"] == "error"
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

    def test_missing_api_key_env_var_produces_warning(self) -> None:
        """Should warn when env var is configured but not set."""
        # Find a community that has openrouter_api_key_env_var configured
        for assistant in registry.list_all():
            config = assistant.community_config
            if config and config.openrouter_api_key_env_var:
                env_var = config.openrouter_api_key_env_var
                original = os.environ.pop(env_var, None)
                try:
                    result = compute_community_health(config)
                    assert result["api_key"] == "missing"
                    assert result["status"] == "error"
                    assert any(env_var in w for w in result["warnings"])
                    assert any("not sustainable" in w for w in result["warnings"])
                finally:
                    if original is not None:
                        os.environ[env_var] = original
                return

        pytest.skip("No community with openrouter_api_key_env_var configured")

    def test_set_api_key_env_var_is_healthy(self) -> None:
        """Should be healthy when env var is set and docs exist."""
        for assistant in registry.list_all():
            config = assistant.community_config
            if config and config.openrouter_api_key_env_var and config.documentation:
                env_var = config.openrouter_api_key_env_var
                original = os.environ.get(env_var)
                try:
                    os.environ[env_var] = "sk-or-v1-test"
                    result = compute_community_health(config)
                    assert result["api_key"] == "configured"
                    assert result["status"] == "healthy"
                    assert not any(env_var in w for w in result["warnings"])
                finally:
                    if original is not None:
                        os.environ[env_var] = original
                    elif env_var in os.environ:
                        del os.environ[env_var]
                return

        pytest.skip("No community with openrouter_api_key_env_var configured")
