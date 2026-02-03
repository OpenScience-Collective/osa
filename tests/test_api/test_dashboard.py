"""Tests for the dashboard static HTML page.

The dashboard is a standalone static site in dashboard/osa/index.html,
deployed separately to Cloudflare Pages. These tests verify the HTML
contains the expected structure and API references.
"""

from pathlib import Path

DASHBOARD_HTML_PATH = Path(__file__).parent.parent.parent / "dashboard" / "osa" / "index.html"


class TestDashboardHTML:
    """Tests for dashboard/osa/index.html static file."""

    def test_file_exists(self) -> None:
        assert DASHBOARD_HTML_PATH.exists(), "dashboard/osa/index.html must exist"

    def test_is_valid_html(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "<!DOCTYPE html>" in content
        assert "<html" in content
        assert "</html>" in content

    def test_contains_page_title(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "Open Science Assistant" in content

    def test_contains_chart_js_cdn(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "chart.js" in content

    def test_references_public_overview_api(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "/metrics/public/overview" in content

    def test_references_community_public_metrics_api(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        # Should use /{community}/metrics/public pattern
        assert "/metrics/public" in content
        assert "/metrics/public/usage" in content

    def test_has_client_side_routing(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "getRoute" in content
        assert "window.location.pathname" in content

    def test_has_aggregate_view(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "renderAggregateView" in content
        assert "Questions Answered" in content

    def test_has_community_view(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "loadCommunityView" in content

    def test_has_tab_bar(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "tabBar" in content
        assert "tab-link" in content
        assert "renderTabs" in content

    def test_has_admin_key_input(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "adminKeyInput" in content
        assert "Admin Access" in content

    def test_admin_section_hidden_by_default(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "admin-section" in content
        assert "display: none" in content or "display:none" in content

    def test_has_period_toggle(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "changePeriod" in content
        assert "daily" in content
        assert "weekly" in content
        assert "monthly" in content

    def test_api_base_configurable(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        # Should support ?api= query param or window.OSA_API_BASE override
        assert "OSA_API_BASE" in content

    def test_has_base_path_constant(self) -> None:
        content = DASHBOARD_HTML_PATH.read_text()
        assert "BASE_PATH" in content
        assert "const BASE_PATH = '/osa'" in content

    def test_cloudflare_redirects_file_exists(self) -> None:
        redirects_path = DASHBOARD_HTML_PATH.parent.parent / "_redirects"
        assert redirects_path.exists(), "_redirects needed for Cloudflare Pages SPA routing"
