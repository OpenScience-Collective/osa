"""Tests for community logo serving.

Tests cover:
- find_logo_file convention-based detection
- convention_logo_url helper
- GET /{community_id}/logo endpoint (404, SVG CSP header)
- Logo URL in /communities and /{community_id} config responses
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.community import (
    _LOGO_MEDIA_TYPES,
    convention_logo_url,
    create_community_router,
    find_logo_file,
)
from src.assistants import discover_assistants, registry
from src.core.config.community import WidgetConfig

# Discover assistants to populate registry
discover_assistants()


class TestFindLogoFile:
    """Tests for find_logo_file function."""

    def test_returns_none_for_nonexistent_community(self) -> None:
        """Should return None for a community directory that doesn't exist."""
        result = find_logo_file("nonexistent-community-xyz")
        assert result is None

    def test_returns_none_when_no_logo_exists(self) -> None:
        """Should return None for real communities without logo files."""
        # Check all registered communities; unless someone has added a logo
        # file, they should all return None
        for info in registry.list_available():
            result = find_logo_file(info.id)
            if result is not None:
                # A logo file exists; that's fine, just verify it's a valid path
                assert result.is_file()
                assert result.suffix in _LOGO_MEDIA_TYPES

    def test_finds_logo_in_temp_dir(self, tmp_path: Path) -> None:
        """Should find a logo file when one exists in the community folder."""
        from src.api.routers import community as community_module

        original_dir = community_module._ASSISTANTS_DIR
        try:
            # Create a fake community directory with a logo
            community_dir = tmp_path / "test-community"
            community_dir.mkdir()
            logo_file = community_dir / "logo.png"
            logo_file.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes

            community_module._ASSISTANTS_DIR = tmp_path
            result = find_logo_file("test-community")
            assert result is not None
            assert result.name == "logo.png"
        finally:
            community_module._ASSISTANTS_DIR = original_dir

    def test_prefers_svg_over_png(self, tmp_path: Path) -> None:
        """Should prefer SVG over PNG when both exist."""
        from src.api.routers import community as community_module

        original_dir = community_module._ASSISTANTS_DIR
        try:
            community_dir = tmp_path / "test-community"
            community_dir.mkdir()
            (community_dir / "logo.svg").write_text("<svg></svg>")
            (community_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

            community_module._ASSISTANTS_DIR = tmp_path
            result = find_logo_file("test-community")
            assert result is not None
            assert result.suffix == ".svg"
        finally:
            community_module._ASSISTANTS_DIR = original_dir


class TestConventionLogoUrl:
    """Tests for convention_logo_url helper."""

    def test_returns_none_when_explicit_logo_url_set(self) -> None:
        """Should return None when widget already has an explicit logo_url."""
        widget = WidgetConfig(logo_url="https://example.com/logo.png")
        result = convention_logo_url("hed", widget)
        assert result is None

    def test_returns_none_when_no_logo_file(self) -> None:
        """Should return None for communities without logo files."""
        widget = WidgetConfig()
        # Use a non-existent community to ensure no file is found
        result = convention_logo_url("nonexistent-community-xyz", widget)
        assert result is None

    def test_returns_url_when_logo_file_exists(self, tmp_path: Path) -> None:
        """Should return convention URL when logo file exists."""
        from src.api.routers import community as community_module

        original_dir = community_module._ASSISTANTS_DIR
        try:
            community_dir = tmp_path / "test-community"
            community_dir.mkdir()
            (community_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

            community_module._ASSISTANTS_DIR = tmp_path
            widget = WidgetConfig()
            result = convention_logo_url("test-community", widget)
            assert result == "/test-community/logo"
        finally:
            community_module._ASSISTANTS_DIR = original_dir


class TestLogoEndpoint:
    """Tests for GET /{community_id}/logo endpoint."""

    def test_returns_404_when_no_logo(self) -> None:
        """Should return 404 for communities without logo files."""
        # Use a real community that doesn't have a logo file
        for info in registry.list_available():
            if find_logo_file(info.id) is None:
                app = FastAPI()
                app.include_router(create_community_router(info.id))
                client = TestClient(app)
                response = client.get(f"/{info.id}/logo")
                assert response.status_code == 404
                return
        pytest.skip("All communities have logo files")

    def test_serves_logo_with_correct_content_type(self, tmp_path: Path) -> None:
        """Should serve logo with correct media type and cache headers."""
        from src.api.routers import community as community_module

        original_dir = community_module._ASSISTANTS_DIR
        try:
            # Create a fake community with a logo file
            community_dir = tmp_path / "hed"
            community_dir.mkdir()
            png_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
            (community_dir / "logo.png").write_bytes(png_content)

            community_module._ASSISTANTS_DIR = tmp_path

            app = FastAPI()
            app.include_router(create_community_router("hed"))
            client = TestClient(app)
            response = client.get("/hed/logo")
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"
            assert "max-age=86400" in response.headers["cache-control"]
        finally:
            community_module._ASSISTANTS_DIR = original_dir

    def test_svg_gets_csp_header(self, tmp_path: Path) -> None:
        """SVG logos should include Content-Security-Policy to prevent XSS."""
        from src.api.routers import community as community_module

        original_dir = community_module._ASSISTANTS_DIR
        try:
            community_dir = tmp_path / "hed"
            community_dir.mkdir()
            (community_dir / "logo.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
            )

            community_module._ASSISTANTS_DIR = tmp_path

            app = FastAPI()
            app.include_router(create_community_router("hed"))
            client = TestClient(app)
            response = client.get("/hed/logo")
            assert response.status_code == 200
            assert "image/svg+xml" in response.headers["content-type"]
            assert "default-src 'none'" in response.headers["content-security-policy"]
        finally:
            community_module._ASSISTANTS_DIR = original_dir

    def test_png_does_not_get_csp_header(self, tmp_path: Path) -> None:
        """Non-SVG logos should not get CSP header."""
        from src.api.routers import community as community_module

        original_dir = community_module._ASSISTANTS_DIR
        try:
            community_dir = tmp_path / "hed"
            community_dir.mkdir()
            (community_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

            community_module._ASSISTANTS_DIR = tmp_path

            app = FastAPI()
            app.include_router(create_community_router("hed"))
            client = TestClient(app)
            response = client.get("/hed/logo")
            assert response.status_code == 200
            assert "content-security-policy" not in response.headers
        finally:
            community_module._ASSISTANTS_DIR = original_dir


class TestLogoInCommunityConfig:
    """Tests that logo_url appears in community config responses."""

    def test_communities_endpoint_includes_logo_url(self) -> None:
        """GET /communities should include logo_url in widget config."""
        from src.api.routers.communities import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/communities")
        assert response.status_code == 200
        data = response.json()
        for community in data:
            assert "logo_url" in community["widget"]
