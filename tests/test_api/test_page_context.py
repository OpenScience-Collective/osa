"""Tests for API page context models and validation."""

import pytest
from pydantic import ValidationError

from src.api.routers.hed import AskRequest, PageContext


class TestPageContextModel:
    """Tests for PageContext Pydantic model."""

    def test_valid_https_url(self):
        """Should accept valid HTTPS URL."""
        ctx = PageContext(url="https://example.com", title="Test")
        assert ctx.url == "https://example.com"
        assert ctx.title == "Test"

    def test_valid_http_url(self):
        """Should accept valid HTTP URL."""
        ctx = PageContext(url="http://example.com", title="Test")
        assert ctx.url == "http://example.com"

    def test_none_url(self):
        """Should accept None URL."""
        ctx = PageContext(url=None, title="Test")
        assert ctx.url is None

    def test_invalid_scheme_becomes_none(self):
        """Should convert invalid URL schemes to None."""
        ctx = PageContext(url="ftp://example.com", title="Test")
        assert ctx.url is None

    def test_file_scheme_becomes_none(self):
        """Should convert file:// URLs to None."""
        ctx = PageContext(url="file:///etc/passwd", title="Test")
        assert ctx.url is None

    def test_javascript_scheme_becomes_none(self):
        """Should convert javascript: URLs to None."""
        ctx = PageContext(url="javascript:alert(1)", title="Test")
        assert ctx.url is None

    def test_title_max_length(self):
        """Should enforce title max length."""
        long_title = "x" * 501
        with pytest.raises(ValidationError):
            PageContext(url="https://example.com", title=long_title)

    def test_url_max_length(self):
        """Should enforce URL max length."""
        long_url = "https://example.com/" + "x" * 2048
        with pytest.raises(ValidationError):
            PageContext(url=long_url, title="Test")

    def test_empty_values(self):
        """Should accept empty PageContext."""
        ctx = PageContext()
        assert ctx.url is None
        assert ctx.title is None


class TestAskRequestWithPageContext:
    """Tests for AskRequest with page context."""

    def test_request_without_page_context(self):
        """Should work without page context."""
        req = AskRequest(question="What is HED?")
        assert req.question == "What is HED?"
        assert req.page_context is None

    def test_request_with_page_context(self):
        """Should accept page context."""
        req = AskRequest(
            question="What is HED?",
            page_context=PageContext(url="https://hedtags.org", title="HED Tags"),
        )
        assert req.question == "What is HED?"
        assert req.page_context is not None
        assert req.page_context.url == "https://hedtags.org"

    def test_request_with_nested_dict_page_context(self):
        """Should parse nested dict as PageContext."""
        req = AskRequest(
            question="What is HED?",
            page_context={"url": "https://hedtags.org", "title": "HED Tags"},
        )
        assert req.page_context is not None
        assert req.page_context.url == "https://hedtags.org"
        assert req.page_context.title == "HED Tags"

    def test_request_with_invalid_url_in_page_context(self):
        """Should handle invalid URL in page context."""
        req = AskRequest(
            question="What is HED?",
            page_context={"url": "ftp://invalid", "title": "Test"},
        )
        # Invalid URL should be converted to None
        assert req.page_context.url is None
        assert req.page_context.title == "Test"
