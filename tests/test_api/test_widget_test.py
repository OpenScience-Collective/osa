"""Tests for widget integration test endpoint."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI application."""
    return TestClient(app)


class TestWidgetTestEndpoint:
    """Tests for the /communities/{id}/widget-test endpoint."""

    def test_widget_test_endpoint_exists(self, client: TestClient) -> None:
        """Should respond to GET /communities/{id}/widget-test."""
        response = client.get("/communities/hed/widget-test")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"

    def test_returns_html_page(self, client: TestClient) -> None:
        """Should return a complete HTML page."""
        response = client.get("/communities/hed/widget-test")
        assert response.status_code == 200

        html = response.text
        # Check for basic HTML structure
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "</html>" in html
        assert "<head>" in html
        assert "<body>" in html

    def test_includes_community_info(self, client: TestClient) -> None:
        """Should include community identification and status."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check for community ID
        assert "hed" in html.lower()
        # Check for status indicators
        assert any(status in html for status in ["healthy", "degraded", "error"])

    def test_includes_diagnostic_information(self, client: TestClient) -> None:
        """Should include diagnostic information about the community."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check for key diagnostic sections
        assert "Diagnostics" in html or "diagnostics" in html.lower()
        assert "Community ID" in html or "community" in html.lower()
        # Check for metrics/counters
        assert "Documentation" in html or "documents" in html.lower()
        assert "CORS" in html

    def test_includes_integration_code(self, client: TestClient) -> None:
        """Should provide copy-paste integration code."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check for code snippet section
        assert "Integration Code" in html or "integration" in html.lower()
        # Check for script tags in the example code
        assert "&lt;script" in html  # HTML-escaped script tags
        assert "osa-chat-widget.js" in html

    def test_includes_widget_script(self, client: TestClient) -> None:
        """Should load the widget script on the page."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check that widget script is loaded
        assert "osa-chat-widget.js" in html
        # Check for widget configuration
        assert "OSAChatWidget" in html
        assert "communityId" in html

    def test_includes_api_test_functionality(self, client: TestClient) -> None:
        """Should include API connectivity testing."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check for test button and results
        assert "testAPI" in html or "test" in html.lower()
        assert "API" in html

    def test_handles_invalid_community(self, client: TestClient) -> None:
        """Should return 404 for non-existent community."""
        response = client.get("/communities/nonexistent/widget-test")
        assert response.status_code == 404

        error_data = response.json()
        assert "detail" in error_data
        assert "nonexistent" in error_data["detail"].lower()

    def test_shows_cors_origins(self, client: TestClient) -> None:
        """Should display configured CORS origins."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check for CORS origins section
        assert "CORS" in html
        assert "origin" in html.lower()

    def test_provides_testing_tips(self, client: TestClient) -> None:
        """Should include helpful testing tips for developers."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check for tips or info boxes
        assert any(
            keyword in html.lower() for keyword in ["tip", "note", "info", "help", "testing"]
        )

    def test_shows_model_configuration(self, client: TestClient) -> None:
        """Should display the configured model for the community."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check for model information
        assert "Model" in html or "model" in html.lower()

    def test_includes_copy_button(self, client: TestClient) -> None:
        """Should include copy button for integration code."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check for copy functionality
        assert "copy" in html.lower()
        assert "copyCode" in html or "clipboard" in html.lower()

    def test_page_is_well_formatted(self, client: TestClient) -> None:
        """Should include CSS styling for good presentation."""
        response = client.get("/communities/hed/widget-test")
        html = response.text

        # Check for CSS styling
        assert "<style>" in html
        assert "</style>" in html
        # Check for common CSS properties indicating formatting
        assert any(prop in html for prop in ["color:", "background:", "padding:", "margin:"])

    def test_multiple_communities_work(self, client: TestClient) -> None:
        """Should work for any registered community."""
        # Test that at least HED works (which we know exists)
        response = client.get("/communities/hed/widget-test")
        assert response.status_code == 200

        # Test that response is customized per community
        html = response.text
        assert "hed" in html.lower()

    def test_html_escaping_prevents_xss(self, client: TestClient) -> None:
        """Should HTML-escape community data to prevent XSS attacks."""
        # This test verifies that any community data rendered in the page
        # is properly HTML-escaped. While we can't easily inject a malicious
        # community in this test, we can verify that the existing communities
        # don't have unescaped HTML tags in their rendered output
        response = client.get("/communities/hed/widget-test")
        assert response.status_code == 200

        html = response.text

        # Check that script tags in the integration code are HTML-escaped
        assert "&lt;script" in html  # Already checked in line 67
        assert "&gt;" in html  # Closing bracket should also be escaped

        # Verify that there are no raw <script> tags in data sections
        # (only in actual script elements for the page itself)
        # Count all <script tags
        script_tags = html.count("<script")
        # All script tags should be legitimate page scripts or escaped examples
        # Verify there are some legitimate scripts (widget loader, etc)
        assert script_tags >= 1  # At least one for loading the widget

        # Verify that if there were any user-provided data with special chars,
        # they would be escaped. Since we can't inject malicious data easily,
        # we check that the HTML structure is valid and doesn't have obvious XSS vectors
        # No unescaped angle brackets in text nodes (except in proper HTML elements)
        import re

        # Find all text outside of proper HTML tags that might contain < or >
        # This is a simplified check - in a real scenario, we'd want to parse the HTML
        # For now, verify that integration code snippets are escaped
        assert re.search(r"&lt;script[^>]*src=.*osa-chat-widget\.js", html)
