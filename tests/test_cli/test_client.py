"""Tests for CLI HTTP client.

These tests use real HTTP requests against a test server.
"""

import httpx
import pytest

from src.cli.client import OSAClient
from src.cli.config import CLIConfig


class TestOSAClientHeaders:
    """Tests for OSAClient header generation."""

    def test_headers_include_content_type(self) -> None:
        """Headers should include Content-Type."""
        config = CLIConfig()
        client = OSAClient(config)
        headers = client._get_headers()
        assert headers["Content-Type"] == "application/json"

    def test_headers_include_api_key_when_set(self) -> None:
        """Headers should include X-API-Key when configured."""
        config = CLIConfig(api_key="test-key")
        client = OSAClient(config)
        headers = client._get_headers()
        assert headers["X-API-Key"] == "test-key"

    def test_headers_exclude_api_key_when_not_set(self) -> None:
        """Headers should not include X-API-Key when not configured."""
        config = CLIConfig()
        client = OSAClient(config)
        headers = client._get_headers()
        assert "X-API-Key" not in headers

    def test_headers_include_openai_key_when_set(self) -> None:
        """Headers should include X-OpenAI-API-Key when configured."""
        config = CLIConfig(openai_api_key="sk-test")
        client = OSAClient(config)
        headers = client._get_headers()
        assert headers["X-OpenAI-API-Key"] == "sk-test"

    def test_headers_include_anthropic_key_when_set(self) -> None:
        """Headers should include X-Anthropic-API-Key when configured."""
        config = CLIConfig(anthropic_api_key="sk-ant-test")
        client = OSAClient(config)
        headers = client._get_headers()
        assert headers["X-Anthropic-API-Key"] == "sk-ant-test"

    def test_headers_include_openrouter_key_when_set(self) -> None:
        """Headers should include X-OpenRouter-API-Key when configured."""
        config = CLIConfig(openrouter_api_key="sk-or-test")
        client = OSAClient(config)
        headers = client._get_headers()
        assert headers["X-OpenRouter-API-Key"] == "sk-or-test"

    def test_headers_include_multiple_byok_keys(self) -> None:
        """Headers should include all configured BYOK keys."""
        config = CLIConfig(
            openai_api_key="sk-openai",
            anthropic_api_key="sk-anthropic",
        )
        client = OSAClient(config)
        headers = client._get_headers()
        assert headers["X-OpenAI-API-Key"] == "sk-openai"
        assert headers["X-Anthropic-API-Key"] == "sk-anthropic"
        assert "X-OpenRouter-API-Key" not in headers


class TestOSAClientBaseUrl:
    """Tests for OSAClient URL handling."""

    def test_base_url_strips_trailing_slash(self) -> None:
        """Base URL should strip trailing slash."""
        config = CLIConfig(api_url="http://localhost:8000/")
        client = OSAClient(config)
        assert client.base_url == "http://localhost:8000"

    def test_base_url_preserves_path(self) -> None:
        """Base URL should preserve any path component."""
        config = CLIConfig(api_url="http://localhost:8000/api/v1")
        client = OSAClient(config)
        assert client.base_url == "http://localhost:8000/api/v1"


class TestOSAClientHealthCheck:
    """Tests for health_check method.

    These tests verify error handling when the server is unavailable.
    """

    def test_health_check_raises_on_connection_error(self) -> None:
        """health_check should raise on connection error."""
        config = CLIConfig(api_url="http://localhost:99999")
        client = OSAClient(config)

        with pytest.raises(httpx.ConnectError):
            client.health_check()


class TestOSAClientGetInfo:
    """Tests for get_info method."""

    def test_get_info_raises_on_connection_error(self) -> None:
        """get_info should raise on connection error."""
        config = CLIConfig(api_url="http://localhost:99999")
        client = OSAClient(config)

        with pytest.raises(httpx.ConnectError):
            client.get_info()
