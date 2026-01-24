"""Tests for CORS configuration and origin aggregation.

Tests cover:
- Wildcard origin to regex conversion
- CORS origin aggregation from community configs
- CORS headers in API responses for configured origins
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.api.main import _collect_cors_config, _wildcard_origin_to_regex, app


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI application."""
    return TestClient(app)


class TestWildcardOriginToRegex:
    """Tests for _wildcard_origin_to_regex helper."""

    def test_simple_wildcard_subdomain(self) -> None:
        """Should match any subdomain for *.example.com."""
        regex = _wildcard_origin_to_regex("https://*.example.com")
        pattern = re.compile(regex)
        assert pattern.match("https://app.example.com")
        assert pattern.match("https://my-app.example.com")
        assert pattern.match("https://a.example.com")
        assert not pattern.match("https://example.com")
        assert not pattern.match("https://.example.com")

    def test_pages_dev_wildcard(self) -> None:
        """Should match subdomains of osa-demo.pages.dev."""
        regex = _wildcard_origin_to_regex("https://*.osa-demo.pages.dev")
        pattern = re.compile(regex)
        assert pattern.match("https://develop.osa-demo.pages.dev")
        assert pattern.match("https://feature-branch.osa-demo.pages.dev")
        assert pattern.match("https://abc123.osa-demo.pages.dev")
        assert not pattern.match("https://osa-demo.pages.dev")
        assert not pattern.match("https://.osa-demo.pages.dev")

    def test_preserves_scheme(self) -> None:
        """Should only match the specified scheme."""
        regex = _wildcard_origin_to_regex("https://*.example.com")
        pattern = re.compile(regex)
        assert pattern.match("https://app.example.com")
        assert not pattern.match("http://app.example.com")

    def test_with_port(self) -> None:
        """Should handle origins with port numbers."""
        regex = _wildcard_origin_to_regex("http://*.localhost:3000")
        pattern = re.compile(regex)
        assert pattern.match("http://app.localhost:3000")
        assert not pattern.match("http://app.localhost:4000")
        assert not pattern.match("http://app.localhost")

    def test_exact_origin_escaping(self) -> None:
        """Should properly escape special regex characters in the domain."""
        regex = _wildcard_origin_to_regex("https://*.my-site.pages.dev")
        pattern = re.compile(regex)
        assert pattern.match("https://app.my-site.pages.dev")
        # The dot should be literal, not regex wildcard
        assert not pattern.match("https://app.my-siteXpages.dev")

    def test_single_char_subdomain(self) -> None:
        """Should match single-character subdomains."""
        regex = _wildcard_origin_to_regex("https://*.example.com")
        pattern = re.compile(regex)
        assert pattern.match("https://a.example.com")
        assert pattern.match("https://1.example.com")

    def test_rejects_hyphen_only_subdomain(self) -> None:
        """Should reject subdomain that is just a hyphen."""
        regex = _wildcard_origin_to_regex("https://*.example.com")
        pattern = re.compile(regex)
        assert not pattern.match("https://-.example.com")


class TestCollectCorsConfig:
    """Tests for _collect_cors_config aggregation."""

    def test_includes_settings_origins(self) -> None:
        """Should include platform-level origins from settings."""
        exact_origins, _ = _collect_cors_config()
        # Settings defaults include localhost and osc.earth domains
        assert "http://localhost:3000" in exact_origins
        assert "https://osc.earth" in exact_origins

    def test_includes_default_wildcard(self) -> None:
        """Should always include *.osa-demo.pages.dev wildcard."""
        _, origin_regex = _collect_cors_config()
        assert origin_regex is not None
        # The regex should match osa-demo.pages.dev subdomains
        pattern = re.compile(origin_regex)
        assert pattern.match("https://develop.osa-demo.pages.dev")
        assert pattern.match("https://feature-branch.osa-demo.pages.dev")

    def test_includes_main_demo_origin(self) -> None:
        """Should include main demo page without subdomain."""
        exact_origins, _ = _collect_cors_config()
        assert "https://osa-demo.pages.dev" in exact_origins

    def test_includes_community_exact_origins(self) -> None:
        """Should include exact origins from community configs (e.g., HED)."""
        exact_origins, _ = _collect_cors_config()
        # HED config has hedtags.org origins
        assert "https://www.hedtags.org" in exact_origins
        assert "https://hedtags.org" in exact_origins

    def test_no_duplicates_in_exact_origins(self) -> None:
        """Should not have duplicate entries in exact origins."""
        exact_origins, _ = _collect_cors_config()
        assert len(exact_origins) == len(set(exact_origins))

    def test_regex_is_anchored(self) -> None:
        """Should produce anchored regex (^...$) for security."""
        _, origin_regex = _collect_cors_config()
        assert origin_regex is not None
        assert origin_regex.startswith("^(")
        assert origin_regex.endswith(")$")


class TestCorsHeaders:
    """Tests for CORS headers in actual API responses."""

    def test_cors_allowed_for_settings_origin(self, client: TestClient) -> None:
        """Should return CORS headers for platform-level origins."""
        response = client.get(
            "/health",
            headers={"Origin": "https://osc.earth"},
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "https://osc.earth"

    def test_cors_allowed_for_community_origin(self, client: TestClient) -> None:
        """Should return CORS headers for community-configured origins."""
        response = client.get(
            "/health",
            headers={"Origin": "https://www.hedtags.org"},
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "https://www.hedtags.org"

    def test_cors_allowed_for_wildcard_origin(self, client: TestClient) -> None:
        """Should return CORS headers for wildcard-matched origins."""
        response = client.get(
            "/health",
            headers={"Origin": "https://develop.osa-demo.pages.dev"},
        )
        assert response.status_code == 200
        assert (
            response.headers.get("access-control-allow-origin")
            == "https://develop.osa-demo.pages.dev"
        )

    def test_cors_denied_for_unknown_origin(self, client: TestClient) -> None:
        """Should not return CORS headers for unconfigured origins."""
        response = client.get(
            "/health",
            headers={"Origin": "https://evil-site.example.com"},
        )
        assert response.status_code == 200
        # No access-control-allow-origin header for denied origins
        assert "access-control-allow-origin" not in response.headers

    def test_cors_preflight_allowed(self, client: TestClient) -> None:
        """Should handle CORS preflight (OPTIONS) requests correctly."""
        response = client.options(
            "/health",
            headers={
                "Origin": "https://www.hedtags.org",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "https://www.hedtags.org"
        assert "POST" in response.headers.get("access-control-allow-methods", "")

    def test_cors_credentials_allowed(self, client: TestClient) -> None:
        """Should include credentials header for allowed origins."""
        response = client.get(
            "/health",
            headers={"Origin": "https://osc.earth"},
        )
        assert response.headers.get("access-control-allow-credentials") == "true"
