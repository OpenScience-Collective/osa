"""Tests for the dashboard HTML page."""

import os

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client() -> TestClient:
    """Create test client with auth disabled."""
    from src.api.config import get_settings

    os.environ["REQUIRE_API_AUTH"] = "false"
    get_settings.cache_clear()
    yield TestClient(app)
    del os.environ["REQUIRE_API_AUTH"]
    get_settings.cache_clear()


class TestDashboardPage:
    """Tests for GET /dashboard."""

    def test_returns_200(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert response.status_code == 200

    def test_returns_html_content_type(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert "text/html" in response.headers["content-type"]

    def test_contains_page_title(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert "Open Science Assistant" in response.text
        assert "Community Dashboard" in response.text

    def test_contains_chart_js_cdn(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert "chart.js" in response.text

    def test_contains_overview_section(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert "overviewContent" in response.text
        assert "Questions Answered" in response.text or "Loading metrics" in response.text

    def test_contains_community_tabs(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert "tabBar" in response.text

    def test_contains_admin_input(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert "adminKeyInput" in response.text
        assert "Admin Access" in response.text

    def test_contains_period_toggle_logic(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert "changePeriod" in response.text
        assert "daily" in response.text
        assert "weekly" in response.text
        assert "monthly" in response.text

    def test_contains_public_metrics_api_calls(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert "/metrics/public/overview" in response.text
        assert "/metrics/public/" in response.text

    def test_admin_section_hidden_by_default(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        # Admin section has display:none by default, shown via JS
        assert "admin-section" in response.text
        assert "display: none" in response.text or "display:none" in response.text
