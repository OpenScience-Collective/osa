"""Tests for generic community router factory.

Tests cover:
- Router factory creates valid routers
- Dynamic endpoint registration
- Session isolation between communities
- Backward compatibility with HED endpoints
- Public health status in config and metrics endpoints
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.community import (
    ChatSession,
    SessionInfo,
    create_community_router,
    delete_session,
    get_or_create_session,
    get_session,
    list_sessions,
)
from src.assistants import discover_assistants

# Discover assistants to populate registry
discover_assistants()


class TestRouterFactory:
    """Tests for create_community_router factory function."""

    def test_creates_router_for_registered_community(self) -> None:
        """Should create a router for a registered community."""
        router = create_community_router("hed")
        assert router is not None
        assert router.prefix == "/hed"

    def test_router_has_correct_tags(self) -> None:
        """Router should have tags based on community name."""
        router = create_community_router("hed")
        # HED community has name "HED" in config
        assert any("HED" in tag for tag in router.tags)

    def test_raises_for_unknown_community(self) -> None:
        """Should raise ValueError for unknown community."""
        with pytest.raises(ValueError, match="Unknown community"):
            create_community_router("nonexistent")

    def test_router_has_expected_routes(self) -> None:
        """Router should have ask, chat, and session endpoints."""
        router = create_community_router("hed")
        route_paths = [r.path for r in router.routes]

        # Routes include the prefix
        assert "/hed/ask" in route_paths
        assert "/hed/chat" in route_paths
        assert "/hed/sessions" in route_paths
        assert "/hed/sessions/{session_id}" in route_paths


class TestSessionManagement:
    """Tests for session management functions."""

    def test_create_new_session(self) -> None:
        """Should create a new session with generated ID."""
        session = get_or_create_session("test_community", None)
        assert session is not None
        assert session.community_id == "test_community"
        assert len(session.session_id) > 0

    def test_get_existing_session(self) -> None:
        """Should return existing session by ID."""
        session1 = get_or_create_session("test_community2", "test-session-123")
        session2 = get_or_create_session("test_community2", "test-session-123")
        assert session1 is session2

    def test_session_isolation_between_communities(self) -> None:
        """Sessions should be isolated between communities."""
        session_hed = get_or_create_session("hed_test", "shared-id")
        session_bids = get_or_create_session("bids_test", "shared-id")

        # Same session ID, different communities = different sessions
        assert session_hed is not session_bids
        assert session_hed.community_id == "hed_test"
        assert session_bids.community_id == "bids_test"

    def test_get_nonexistent_session(self) -> None:
        """Should return None for nonexistent session."""
        session = get_session("nonexistent_community", "nonexistent-id")
        assert session is None

    def test_delete_session(self) -> None:
        """Should delete existing session."""
        get_or_create_session("delete_test", "to-delete")
        assert delete_session("delete_test", "to-delete") is True
        assert get_session("delete_test", "to-delete") is None

    def test_delete_nonexistent_session(self) -> None:
        """Should return False for nonexistent session."""
        assert delete_session("nonexistent", "nonexistent") is False

    def test_list_sessions(self) -> None:
        """Should list all sessions for a community."""
        # Create some sessions
        get_or_create_session("list_test", "session-1")
        get_or_create_session("list_test", "session-2")

        sessions = list_sessions("list_test")
        assert len(sessions) >= 2
        session_ids = [s.session_id for s in sessions]
        assert "session-1" in session_ids
        assert "session-2" in session_ids


class TestChatSession:
    """Tests for ChatSession class."""

    def test_session_tracks_messages(self) -> None:
        """Session should track user and assistant messages."""
        session = ChatSession("test-id", "test-community")
        session.add_user_message("Hello")
        session.add_assistant_message("Hi there!")

        assert len(session.messages) == 2
        assert session.messages[0].content == "Hello"
        assert session.messages[1].content == "Hi there!"

    def test_session_to_info(self) -> None:
        """Session should convert to SessionInfo model."""
        session = ChatSession("test-id", "test-community")
        session.add_user_message("Test")

        info = session.to_info()
        assert isinstance(info, SessionInfo)
        assert info.session_id == "test-id"
        assert info.community_id == "test-community"
        assert info.message_count == 1


class TestRouterIntegration:
    """Integration tests for mounted community router."""

    @pytest.fixture
    def app_with_hed_router(self) -> FastAPI:
        """Create a FastAPI app with HED router mounted."""
        app = FastAPI()
        router = create_community_router("hed")
        app.include_router(router)
        return app

    def test_ask_endpoint_exists(self, app_with_hed_router: FastAPI) -> None:
        """HED ask endpoint should be accessible."""
        client = TestClient(app_with_hed_router)
        # Without auth, should get 401/403, not 404
        response = client.post("/hed/ask", json={"question": "test"})
        assert response.status_code != 404

    def test_chat_endpoint_exists(self, app_with_hed_router: FastAPI) -> None:
        """HED chat endpoint should be accessible."""
        client = TestClient(app_with_hed_router)
        # Without auth, should get 401/403, not 404
        response = client.post("/hed/chat", json={"message": "test"})
        assert response.status_code != 404

    def test_sessions_endpoint_exists(self, app_with_hed_router: FastAPI) -> None:
        """HED sessions endpoint should be accessible (requires auth)."""
        client = TestClient(app_with_hed_router)
        response = client.get("/hed/sessions")
        # Sessions endpoint requires auth, so without auth we get 401/403, not 404
        assert response.status_code != 404


class TestMainAppIntegration:
    """Tests for main app with auto-mounted community routers."""

    @pytest.fixture
    def app(self) -> FastAPI:
        """Get the main FastAPI app."""
        from src.api.main import app

        return app

    def test_hed_routes_mounted(self, app: FastAPI) -> None:
        """HED routes should be mounted on main app."""
        route_paths = [r.path for r in app.routes]
        assert "/hed/ask" in route_paths
        assert "/hed/chat" in route_paths
        assert "/hed/sessions" in route_paths

    def test_root_shows_communities(self, app: FastAPI) -> None:
        """Root endpoint should list registered communities."""
        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "communities" in data
        assert "hed" in data["communities"]

    def test_root_shows_dynamic_endpoints(self, app: FastAPI) -> None:
        """Root endpoint should show endpoints for all communities."""
        client = TestClient(app)
        response = client.get("/")
        data = response.json()
        assert "endpoints" in data
        assert "POST /hed/ask" in data["endpoints"]
        assert "POST /hed/chat" in data["endpoints"]


class TestCacheUserIdDerivation:
    """Tests for prompt caching user ID derivation."""

    def test_derive_user_id(self) -> None:
        """Should derive a stable 16-char hex ID from API key."""
        from src.api.routers.community import _derive_user_id

        api_key = "sk-or-v1-test-key-12345"
        user_id = _derive_user_id(api_key)

        # Should be 16 hex chars
        assert len(user_id) == 16
        assert all(c in "0123456789abcdef" for c in user_id)

    def test_derive_user_id_consistency(self) -> None:
        """Same API key should always produce same user ID."""
        from src.api.routers.community import _derive_user_id

        api_key = "sk-or-v1-consistent-key"
        user_id1 = _derive_user_id(api_key)
        user_id2 = _derive_user_id(api_key)

        assert user_id1 == user_id2

    def test_derive_user_id_uniqueness(self) -> None:
        """Different API keys should produce different user IDs."""
        from src.api.routers.community import _derive_user_id

        user_id1 = _derive_user_id("key1")
        user_id2 = _derive_user_id("key2")

        assert user_id1 != user_id2

    def test_get_cache_user_id_byok_with_explicit_user_id(self) -> None:
        """BYOK user with explicit user_id should use that ID."""
        from src.api.routers.community import _get_cache_user_id

        result = _get_cache_user_id("hed", "my-api-key", "my-user-id")
        assert result == "my-user-id"

    def test_get_cache_user_id_byok_derives_from_key(self) -> None:
        """BYOK user without explicit user_id should derive from API key."""
        from src.api.routers.community import _derive_user_id, _get_cache_user_id

        api_key = "sk-or-v1-byok-key"
        result = _get_cache_user_id("hed", api_key, None)
        expected = _derive_user_id(api_key)

        assert result == expected

    def test_get_cache_user_id_platform_uses_shared_id(self) -> None:
        """Platform/widget users should get shared ID per community."""
        from src.api.routers.community import _get_cache_user_id

        result_hed = _get_cache_user_id("hed", None, None)
        result_bids = _get_cache_user_id("bids", None, None)

        assert result_hed == "hed_widget"
        assert result_bids == "bids_widget"

    def test_get_cache_user_id_platform_ignores_user_id(self) -> None:
        """Platform users ignore user_id since they share cache."""
        from src.api.routers.community import _get_cache_user_id

        # Even with user_id, platform users get shared ID
        result = _get_cache_user_id("hed", None, "should-be-ignored")
        assert result == "hed_widget"


class TestCreateCommunityAssistant:
    """Tests for create_community_assistant factory function."""

    def test_raises_for_unknown_community(self) -> None:
        """Should raise ValueError for unknown community ID."""
        from src.api.routers.community import create_community_assistant

        with pytest.raises(ValueError, match="Unknown community: fake_community"):
            create_community_assistant("fake_community")


class TestSessionEndpointBehavior:
    """Tests for session endpoint behavior using unit-level functions."""

    def test_get_session_returns_none_for_nonexistent(self) -> None:
        """get_session should return None for nonexistent session."""
        session = get_session("test_community", "nonexistent-session-id")
        assert session is None

    def test_delete_session_returns_false_for_nonexistent(self) -> None:
        """delete_session should return False for nonexistent session."""
        result = delete_session("test_community", "nonexistent-session-id")
        assert result is False

    def test_session_list_endpoint_exists(self) -> None:
        """Session list endpoint should exist and be routable."""
        app = FastAPI()
        router = create_community_router("hed")
        app.include_router(router)
        client = TestClient(app)

        # List endpoint should return 200 (empty list) or 401/403 (no auth)
        # Never 404 since it's a valid route
        response = client.get("/hed/sessions")
        assert response.status_code in (200, 401, 403)

    def test_session_get_endpoint_exists(self) -> None:
        """Session get endpoint should exist and be routable."""
        app = FastAPI()
        router = create_community_router("hed")
        app.include_router(router)
        client = TestClient(app)

        # Get endpoint with nonexistent session:
        # - Returns 404 with "Session not found" if authenticated (route exists)
        # - Returns 401/403 if not authenticated (route exists)
        response = client.get("/hed/sessions/nonexistent-id")
        if response.status_code == 404:
            # Route exists, session not found - check the message
            assert response.json().get("detail") == "Session not found"
        else:
            # Auth required
            assert response.status_code in (401, 403)

    def test_session_delete_endpoint_exists(self) -> None:
        """Session delete endpoint should exist and be routable."""
        app = FastAPI()
        router = create_community_router("hed")
        app.include_router(router)
        client = TestClient(app)

        # Delete endpoint with nonexistent session:
        # - Returns 404 with "Session not found" if authenticated (route exists)
        # - Returns 401/403 if not authenticated (route exists)
        response = client.delete("/hed/sessions/nonexistent-id")
        if response.status_code == 404:
            # Route exists, session not found - check the message
            assert response.json().get("detail") == "Session not found"
        else:
            # Auth required
            assert response.status_code in (401, 403)


class TestCommunityConfigHealthStatus:
    """Tests for health status in community config and public metrics."""

    @pytest.fixture
    def client(self, tmp_path) -> TestClient:
        """Create a test client with auth disabled and metrics DB initialized."""
        os.environ["REQUIRE_API_AUTH"] = "false"
        from src.api.config import get_settings

        get_settings.cache_clear()

        # Initialize a temp metrics DB so /metrics/public doesn't 503
        from unittest.mock import patch

        from src.metrics.db import init_metrics_db

        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)

        from src.api.main import app

        with patch("src.metrics.db.get_metrics_db_path", return_value=db_path):
            yield TestClient(app)

    def test_config_response_includes_status(self, client: TestClient) -> None:
        """GET /{community_id}/ should include a status field."""
        response = client.get("/hed/")
        assert response.status_code == 200

        data = response.json()
        assert "status" in data
        assert data["status"] in ["healthy", "degraded", "error"]

    def test_config_status_does_not_leak_details(self, client: TestClient) -> None:
        """Public config should not expose api_key details or warnings."""
        response = client.get("/hed/")
        data = response.json()

        assert "warnings" not in data
        assert "api_key" not in data
        assert "config_health" not in data

    def test_public_metrics_includes_config_health(self, client: TestClient) -> None:
        """GET /{community_id}/metrics/public should include config_health."""
        response = client.get("/hed/metrics/public")
        assert response.status_code == 200

        data = response.json()
        assert "config_health" in data

        health = data["config_health"]
        assert "status" in health
        assert health["status"] in ["healthy", "degraded", "error"]
        assert "api_key" in health
        assert health["api_key"] in ["configured", "using_platform", "missing"]
        assert "documents" in health
        assert isinstance(health["documents"], int)
        assert "warnings" in health
        assert isinstance(health["warnings"], list)

    def test_public_metrics_config_health_has_warnings_for_missing_key(
        self, client: TestClient
    ) -> None:
        """config_health should include warnings when API key env var is not set."""
        from src.assistants import registry

        # Find a community with openrouter_api_key_env_var
        for assistant in registry.list_all():
            config = assistant.community_config
            if config and config.openrouter_api_key_env_var:
                env_var = config.openrouter_api_key_env_var
                original = os.environ.pop(env_var, None)
                try:
                    response = client.get(f"/{assistant.id}/metrics/public")
                    assert response.status_code == 200
                    health = response.json()["config_health"]
                    assert health["api_key"] == "missing"
                    assert len(health["warnings"]) > 0
                    assert any("not sustainable" in w for w in health["warnings"])
                    # Env var names must not leak to public endpoint
                    assert not any(env_var in w for w in health["warnings"])
                finally:
                    if original is not None:
                        os.environ[env_var] = original
                return

        pytest.skip("No community with openrouter_api_key_env_var configured")
