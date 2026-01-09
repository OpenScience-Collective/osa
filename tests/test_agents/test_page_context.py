"""Tests for page context awareness feature."""

import pytest

from src.agents.hed import (
    HEDAssistant,
    PageContext,
    _fetch_page_content_impl,
    is_safe_url,
)


class TestSsrfProtection:
    """Tests for SSRF (Server-Side Request Forgery) protection."""

    def test_blocks_localhost(self):
        """Should reject localhost URLs."""
        is_safe, error = is_safe_url("http://localhost:8080")
        assert not is_safe
        assert "not allowed" in error.lower()

    def test_blocks_localhost_127(self):
        """Should reject 127.0.0.1."""
        is_safe, error = is_safe_url("http://127.0.0.1:8080")
        assert not is_safe
        assert "not allowed" in error.lower()

    def test_blocks_private_ip_10(self):
        """Should reject 10.x.x.x private IPs."""
        is_safe, error = is_safe_url("http://10.0.0.1")
        assert not is_safe
        assert "private" in error.lower()

    def test_blocks_private_ip_192(self):
        """Should reject 192.168.x.x private IPs."""
        is_safe, error = is_safe_url("http://192.168.1.1")
        assert not is_safe
        assert "private" in error.lower()

    def test_blocks_private_ip_172(self):
        """Should reject 172.16-31.x.x private IPs."""
        is_safe, error = is_safe_url("http://172.16.0.1")
        assert not is_safe
        assert "private" in error.lower()

    def test_blocks_link_local(self):
        """Should reject link-local addresses (169.254.x.x)."""
        is_safe, error = is_safe_url("http://169.254.169.254")  # AWS metadata
        assert not is_safe
        assert "not allowed" in error.lower()

    def test_blocks_non_http_scheme(self):
        """Should reject non-HTTP schemes."""
        is_safe, error = is_safe_url("ftp://example.com")
        assert not is_safe
        assert "HTTP" in error

    def test_blocks_file_scheme(self):
        """Should reject file:// URLs."""
        is_safe, error = is_safe_url("file:///etc/passwd")
        assert not is_safe
        assert "HTTP" in error

    def test_allows_public_url(self):
        """Should allow public URLs."""
        is_safe, error = is_safe_url("https://hedtags.org")
        assert is_safe
        assert error == ""

    def test_allows_https(self):
        """Should allow HTTPS URLs."""
        is_safe, error = is_safe_url("https://example.com")
        assert is_safe
        assert error == ""

    def test_handles_invalid_url(self):
        """Should handle invalid URLs gracefully."""
        is_safe, error = is_safe_url("not-a-url")
        assert not is_safe

    def test_handles_empty_url(self):
        """Should handle empty hostname."""
        is_safe, error = is_safe_url("http://")
        assert not is_safe
        assert "hostname" in error.lower()


class TestFetchPageContentImpl:
    """Tests for _fetch_page_content_impl function."""

    def test_rejects_invalid_url(self):
        """Should reject URLs that don't start with http/https."""
        result = _fetch_page_content_impl("ftp://example.com")
        assert "Error" in result
        assert "http://" in result.lower() or "https://" in result.lower()

    def test_rejects_empty_url(self):
        """Should reject empty URLs."""
        result = _fetch_page_content_impl("")
        assert "Error" in result

    def test_rejects_none_url(self):
        """Should handle None URL."""
        result = _fetch_page_content_impl(None)
        assert "Error" in result

    def test_rejects_localhost(self):
        """Should reject localhost URLs."""
        result = _fetch_page_content_impl("http://localhost:8080")
        assert "Error" in result
        assert "loopback" in result.lower() or "not allowed" in result.lower()


class TestPageContextDataclass:
    """Tests for PageContext dataclass."""

    def test_default_values(self):
        """Should have None defaults."""
        ctx = PageContext()
        assert ctx.url is None
        assert ctx.title is None

    def test_with_values(self):
        """Should accept URL and title."""
        ctx = PageContext(url="https://example.com", title="Test Page")
        assert ctx.url == "https://example.com"
        assert ctx.title == "Test Page"

    def test_with_only_url(self):
        """Should work with only URL."""
        ctx = PageContext(url="https://example.com")
        assert ctx.url == "https://example.com"
        assert ctx.title is None


class TestHEDAssistantWithPageContext:
    """Tests for HEDAssistant with page context."""

    def test_assistant_without_page_context(self):
        """Should work without page context."""
        from unittest.mock import MagicMock

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        assistant = HEDAssistant(model=model, preload_docs=False)

        # Should have 4 base tools, not 5
        assert len(assistant.tools) == 4
        tool_names = [t.name for t in assistant.tools]
        assert "fetch_current_page" not in tool_names

    def test_assistant_with_page_context(self):
        """Should add fetch_current_page tool when page context is provided."""
        from unittest.mock import MagicMock

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org", title="HED Tags")
        assistant = HEDAssistant(model=model, preload_docs=False, page_context=page_context)

        # Should have 5 tools including fetch_current_page
        assert len(assistant.tools) == 5
        tool_names = [t.name for t in assistant.tools]
        assert "fetch_current_page" in tool_names

    def test_assistant_with_empty_page_context_url(self):
        """Should not add tool when page context URL is empty."""
        from unittest.mock import MagicMock

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url=None, title="No URL")
        assistant = HEDAssistant(model=model, preload_docs=False, page_context=page_context)

        # Should have 4 base tools, not 5
        assert len(assistant.tools) == 4
        tool_names = [t.name for t in assistant.tools]
        assert "fetch_current_page" not in tool_names

    def test_system_prompt_includes_page_context(self):
        """Should include page context in system prompt."""
        from unittest.mock import MagicMock

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org/docs", title="HED Docs")
        assistant = HEDAssistant(model=model, preload_docs=False, page_context=page_context)

        prompt = assistant.get_system_prompt()
        assert "https://hedtags.org/docs" in prompt
        assert "HED Docs" in prompt
        assert "Page Context" in prompt
        assert "fetch_current_page" in prompt

    def test_system_prompt_without_page_context(self):
        """Should not include page context section without page context."""
        from unittest.mock import MagicMock

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        assistant = HEDAssistant(model=model, preload_docs=False)

        prompt = assistant.get_system_prompt()
        assert "Page Context" not in prompt
