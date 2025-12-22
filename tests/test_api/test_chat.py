"""Tests for chat API router."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routers.chat import (
    ChatSession,
    _sessions,
    get_or_create_session,
    get_session,
)


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_sessions():
    """Clear sessions before and after each test."""
    _sessions.clear()
    yield
    _sessions.clear()


class TestSessionManagement:
    """Tests for session management functions."""

    def test_create_new_session(self) -> None:
        """get_or_create_session should create a new session."""
        session = get_or_create_session(None, "hed")

        assert session.session_id is not None
        assert session.assistant == "hed"
        assert len(session.messages) == 0

    def test_create_session_with_custom_id(self) -> None:
        """get_or_create_session should use provided session ID."""
        session = get_or_create_session("custom-123", "hed")

        assert session.session_id == "custom-123"

    def test_get_existing_session(self) -> None:
        """get_or_create_session should return existing session."""
        session1 = get_or_create_session("test-session", "hed")
        session1.add_user_message("Hello")

        session2 = get_or_create_session("test-session", "hed")

        assert session1 is session2
        assert len(session2.messages) == 1

    def test_get_session_not_found(self) -> None:
        """get_session should return None for unknown session."""
        session = get_session("nonexistent")
        assert session is None

    def test_session_message_history(self) -> None:
        """Session should track message history."""
        session = ChatSession("test", "hed")

        session.add_user_message("Hello")
        session.add_assistant_message("Hi there!")
        session.add_user_message("How are you?")

        assert len(session.messages) == 3
        assert session.messages[0].content == "Hello"
        assert session.messages[1].content == "Hi there!"

    def test_session_to_info(self) -> None:
        """Session should convert to SessionInfo."""
        session = ChatSession("test-id", "hed")
        session.add_user_message("Hello")

        info = session.to_info()

        assert info.session_id == "test-id"
        assert info.assistant == "hed"
        assert info.message_count == 1


class TestChatEndpoint:
    """Tests for POST /chat endpoint."""

    def test_chat_without_api_key_fails(self, client) -> None:
        """Chat should fail without OpenRouter API key (when no key is configured)."""
        import os

        from src.api.config import get_settings

        settings = get_settings()

        # Skip if an API key is configured (in .env or environment)
        if settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY"):
            pytest.skip("API key is configured, cannot test missing key scenario")

        response = client.post(
            "/chat",
            json={"message": "Hello", "stream": False},
        )

        # Should fail because no API key is configured
        assert response.status_code == 500

    @patch("src.api.routers.chat.create_assistant")
    def test_chat_non_streaming(self, mock_create, client) -> None:
        """Chat should return non-streaming response."""
        # Mock the assistant
        mock_assistant = MagicMock()
        mock_assistant.ainvoke = AsyncMock(
            return_value={"messages": [MagicMock(content="Hello! How can I help with HED?")]}
        )
        mock_create.return_value = mock_assistant

        response = client.post(
            "/chat",
            json={"message": "Hello", "stream": False},
            headers={"X-OpenRouter-Key": "test-key"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["message"]["role"] == "assistant"
        assert data["assistant"] == "hed"

    @patch("src.api.routers.chat.create_assistant")
    def test_chat_with_session_id(self, mock_create, client) -> None:
        """Chat should reuse provided session ID."""
        mock_assistant = MagicMock()
        mock_assistant.ainvoke = AsyncMock(
            return_value={"messages": [MagicMock(content="Response")]}
        )
        mock_create.return_value = mock_assistant

        response = client.post(
            "/chat",
            json={"message": "Hello", "session_id": "my-session", "stream": False},
            headers={"X-OpenRouter-Key": "test-key"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "my-session"

    def test_chat_streaming_returns_event_stream(self, client) -> None:
        """Streaming chat should return text/event-stream."""
        # This test just verifies the response type, not full streaming
        with patch("src.api.routers.chat.create_assistant") as mock_create:
            mock_assistant = MagicMock()
            mock_create.return_value = mock_assistant

            response = client.post(
                "/chat",
                json={"message": "Hello", "stream": True},
                headers={"X-OpenRouter-Key": "test-key"},
            )

            # Should return streaming response
            assert response.headers.get("content-type").startswith("text/event-stream")


class TestSessionEndpoints:
    """Tests for session management endpoints."""

    @patch("src.api.routers.chat.create_assistant")
    def test_get_session_info(self, mock_create, client) -> None:
        """GET /chat/sessions/{id} should return session info."""
        # Create a session first
        mock_assistant = MagicMock()
        mock_assistant.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Hi")]})
        mock_create.return_value = mock_assistant

        create_response = client.post(
            "/chat",
            json={"message": "Hello", "stream": False},
            headers={"X-OpenRouter-Key": "test-key"},
        )
        session_id = create_response.json()["session_id"]

        # Get session info
        response = client.get(f"/chat/sessions/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["message_count"] == 2  # User + assistant message

    def test_get_session_not_found(self, client) -> None:
        """GET /chat/sessions/{id} should return 404 for unknown session."""
        response = client.get("/chat/sessions/nonexistent")
        assert response.status_code == 404

    def test_delete_session(self, client) -> None:
        """DELETE /chat/sessions/{id} should delete session."""
        # Create a session
        get_or_create_session("to-delete", "hed")

        response = client.delete("/chat/sessions/to-delete")

        assert response.status_code == 200
        assert get_session("to-delete") is None

    def test_delete_session_not_found(self, client) -> None:
        """DELETE /chat/sessions/{id} should return 404 for unknown session."""
        response = client.delete("/chat/sessions/nonexistent")
        assert response.status_code == 404

    def test_list_sessions(self, client) -> None:
        """GET /chat/sessions should list all sessions."""
        # Create some sessions
        get_or_create_session("session-1", "hed")
        get_or_create_session("session-2", "bids")

        response = client.get("/chat/sessions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        session_ids = [s["session_id"] for s in data]
        assert "session-1" in session_ids
        assert "session-2" in session_ids
