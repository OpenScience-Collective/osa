"""Tests for CLI HTTP client.

Tests cover client construction, header generation, and error handling.
Connection tests use unreachable ports to verify error propagation.
"""

import httpx
import pytest

from src.cli.client import APIError, OSAClient


class TestOSAClientHeaders:
    """Tests for OSAClient header generation."""

    def test_headers_include_content_type(self) -> None:
        """Headers should include Content-Type."""
        client = OSAClient(api_url="http://localhost:8000")
        headers = client._get_headers()
        assert headers["Content-Type"] == "application/json"

    def test_headers_include_user_agent(self) -> None:
        """Headers should include User-Agent."""
        client = OSAClient(api_url="http://localhost:8000")
        headers = client._get_headers()
        assert headers["User-Agent"] == "osa-cli"

    def test_headers_include_user_id(self) -> None:
        """Headers should include X-User-ID."""
        client = OSAClient(api_url="http://localhost:8000", user_id="abc123")
        headers = client._get_headers()
        assert headers["X-User-ID"] == "abc123"

    def test_headers_include_openrouter_key_when_set(self) -> None:
        """Headers should include X-OpenRouter-Key when configured."""
        client = OSAClient(
            api_url="http://localhost:8000",
            openrouter_api_key="sk-or-test",
        )
        headers = client._get_headers()
        assert headers["X-OpenRouter-Key"] == "sk-or-test"
        assert headers["X-OpenRouter-API-Key"] == "sk-or-test"

    def test_headers_exclude_openrouter_key_when_not_set(self) -> None:
        """Headers should not include X-OpenRouter-Key when not configured."""
        client = OSAClient(api_url="http://localhost:8000")
        headers = client._get_headers()
        assert "X-OpenRouter-Key" not in headers
        assert "X-OpenRouter-API-Key" not in headers


class TestOSAClientBaseUrl:
    """Tests for OSAClient URL handling."""

    def test_base_url_strips_trailing_slash(self) -> None:
        """Base URL should strip trailing slash."""
        client = OSAClient(api_url="http://localhost:8000/")
        assert client.api_url == "http://localhost:8000"

    def test_base_url_preserves_path(self) -> None:
        """Base URL should preserve any path component."""
        client = OSAClient(api_url="http://localhost:8000/api/v1")
        assert client.api_url == "http://localhost:8000/api/v1"


class TestOSAClientHealthCheck:
    """Tests for health_check method."""

    def test_health_check_raises_on_connection_error(self) -> None:
        """health_check should raise on connection error."""
        client = OSAClient(api_url="http://localhost:99999")
        with pytest.raises(httpx.ConnectError):
            client.health_check()


class TestOSAClientGetInfo:
    """Tests for get_info method."""

    def test_get_info_raises_on_connection_error(self) -> None:
        """get_info should raise on connection error."""
        client = OSAClient(api_url="http://localhost:99999")
        with pytest.raises(httpx.ConnectError):
            client.get_info()


class TestAPIError:
    """Tests for APIError exception."""

    def test_api_error_attributes(self) -> None:
        """APIError should carry status_code and detail."""
        err = APIError("test error", status_code=403, detail="forbidden")
        assert str(err) == "test error"
        assert err.status_code == 403
        assert err.detail == "forbidden"

    def test_api_error_defaults(self) -> None:
        """APIError should default to None for optional fields."""
        err = APIError("test error")
        assert err.status_code is None
        assert err.detail is None
