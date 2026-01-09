"""Tests for page context awareness feature."""

from unittest.mock import MagicMock, patch

import httpx

from src.agents.hed import (
    MAX_PAGE_CONTENT_LENGTH,
    HEDAssistant,
    PageContext,
    _fetch_page_content_impl,
    is_safe_url,
)


class TestSsrfProtection:
    """Tests for SSRF (Server-Side Request Forgery) protection."""

    def test_blocks_localhost(self):
        """Should reject localhost URLs."""
        is_safe, error, resolved_ip = is_safe_url("http://localhost:8080")
        assert not is_safe
        # 127.0.0.1 is caught by is_private check (private includes loopback in Python)
        assert "not allowed" in error.lower()
        assert resolved_ip is None

    def test_blocks_localhost_127(self):
        """Should reject 127.0.0.1."""
        is_safe, error, resolved_ip = is_safe_url("http://127.0.0.1:8080")
        assert not is_safe
        # 127.0.0.1 is caught by is_private check
        assert "not allowed" in error.lower()
        assert resolved_ip is None

    def test_blocks_private_ip_10(self):
        """Should reject 10.x.x.x private IPs."""
        is_safe, error, resolved_ip = is_safe_url("http://10.0.0.1")
        assert not is_safe
        assert "private" in error.lower()
        assert resolved_ip is None

    def test_blocks_private_ip_192(self):
        """Should reject 192.168.x.x private IPs."""
        is_safe, error, resolved_ip = is_safe_url("http://192.168.1.1")
        assert not is_safe
        assert "private" in error.lower()
        assert resolved_ip is None

    def test_blocks_private_ip_172(self):
        """Should reject 172.16-31.x.x private IPs."""
        is_safe, error, resolved_ip = is_safe_url("http://172.16.0.1")
        assert not is_safe
        assert "private" in error.lower()
        assert resolved_ip is None

    def test_blocks_link_local(self):
        """Should reject link-local addresses (169.254.x.x)."""
        is_safe, error, resolved_ip = is_safe_url("http://169.254.169.254")  # AWS metadata
        assert not is_safe
        # Link-local IPs are caught by is_private check in Python's ipaddress module
        assert "not allowed" in error.lower()
        assert resolved_ip is None

    def test_blocks_non_http_scheme(self):
        """Should reject non-HTTP schemes."""
        is_safe, error, resolved_ip = is_safe_url("ftp://example.com")
        assert not is_safe
        assert "HTTP" in error
        assert resolved_ip is None

    def test_blocks_file_scheme(self):
        """Should reject file:// URLs."""
        is_safe, error, resolved_ip = is_safe_url("file:///etc/passwd")
        assert not is_safe
        assert "HTTP" in error
        assert resolved_ip is None

    def test_allows_public_url(self):
        """Should allow public URLs and return resolved IP."""
        is_safe, error, resolved_ip = is_safe_url("https://hedtags.org")
        assert is_safe
        assert error == ""
        assert resolved_ip is not None

    def test_allows_https(self):
        """Should allow HTTPS URLs and return resolved IP."""
        is_safe, error, resolved_ip = is_safe_url("https://example.com")
        assert is_safe
        assert error == ""
        assert resolved_ip is not None

    def test_handles_invalid_url(self):
        """Should handle invalid URLs gracefully."""
        is_safe, error, resolved_ip = is_safe_url("not-a-url")
        assert not is_safe
        assert resolved_ip is None

    def test_handles_empty_url(self):
        """Should handle empty hostname."""
        is_safe, error, resolved_ip = is_safe_url("http://")
        assert not is_safe
        assert "hostname" in error.lower()
        assert resolved_ip is None

    def test_dns_resolution_failure(self):
        """Should return error for unresolvable hostnames."""
        is_safe, error, resolved_ip = is_safe_url("http://this-domain-does-not-exist-12345.invalid")
        assert not is_safe
        assert "DNS resolution failed" in error or "Host error" in error
        assert resolved_ip is None

    def test_reserved_ip_range(self):
        """Should block reserved IP ranges (0.0.0.0)."""
        is_safe, error, resolved_ip = is_safe_url("http://0.0.0.0")
        assert not is_safe
        assert resolved_ip is None


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
        """Should handle None-like URL."""
        # Test with empty string since None is not a valid type
        result = _fetch_page_content_impl("")
        assert "Error" in result

    def test_rejects_localhost(self):
        """Should reject localhost URLs."""
        result = _fetch_page_content_impl("http://localhost:8080")
        assert "Error" in result
        assert "not allowed" in result.lower()

    def test_rejects_private_ip(self):
        """Should reject private IP addresses."""
        result = _fetch_page_content_impl("http://192.168.1.1")
        assert "Error" in result
        assert "private" in result.lower()

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_successful_fetch(self, mock_client_class, mock_is_safe):
        """Should fetch and convert HTML to markdown."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")

        # Create mock response
        mock_response = MagicMock()
        mock_response.is_redirect = False
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html><body><h1>Test</h1><p>Hello world</p></body></html>"

        # Set up the context manager
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com")
        assert "Content from https://example.com" in result
        assert "Test" in result
        assert "Hello world" in result

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_rejects_non_html_content(self, mock_client_class, mock_is_safe):
        """Should reject non-HTML content types."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")

        mock_response = MagicMock()
        mock_response.is_redirect = False
        mock_response.headers = {"content-type": "application/json"}

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com/api")
        assert "Error" in result
        assert "non-HTML" in result

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_handles_redirect_to_safe_url(self, mock_client_class, mock_is_safe):
        """Should follow redirects to safe URLs."""
        # First call safe, redirect also safe
        mock_is_safe.side_effect = [
            (True, "", "93.184.216.34"),  # Original URL
            (True, "", "93.184.216.34"),  # Redirect URL
        ]

        # Create redirect and final responses
        redirect_response = MagicMock()
        redirect_response.is_redirect = True
        redirect_response.headers = {"location": "https://example.com/page"}

        final_response = MagicMock()
        final_response.is_redirect = False
        final_response.headers = {"content-type": "text/html"}
        final_response.text = "<html><body>Final page</body></html>"

        mock_client = MagicMock()
        mock_client.get.side_effect = [redirect_response, final_response]
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com")
        assert "Final page" in result

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_blocks_redirect_to_unsafe_url(self, mock_client_class, mock_is_safe):
        """Should block redirects to unsafe URLs (SSRF protection)."""
        # Original safe, redirect unsafe
        mock_is_safe.side_effect = [
            (True, "", "93.184.216.34"),  # Original URL safe
            (False, "Access to private IP ranges is not allowed", None),  # Redirect unsafe
        ]

        redirect_response = MagicMock()
        redirect_response.is_redirect = True
        redirect_response.headers = {"location": "http://192.168.1.1/internal"}

        mock_client = MagicMock()
        mock_client.get.return_value = redirect_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com")
        assert "Error" in result
        assert "Redirect to unsafe URL" in result

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_handles_too_many_redirects(self, mock_client_class, mock_is_safe):
        """Should limit redirect count."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")

        # Always redirect
        redirect_response = MagicMock()
        redirect_response.is_redirect = True
        redirect_response.headers = {"location": "https://example.com/loop"}

        mock_client = MagicMock()
        mock_client.get.return_value = redirect_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com")
        assert "Error" in result
        assert "Too many redirects" in result

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_handles_relative_redirect(self, mock_client_class, mock_is_safe):
        """Should handle relative redirects properly."""
        mock_is_safe.side_effect = [
            (True, "", "93.184.216.34"),  # Original
            (True, "", "93.184.216.34"),  # Relative redirect converted to absolute
        ]

        redirect_response = MagicMock()
        redirect_response.is_redirect = True
        redirect_response.headers = {"location": "/new-page"}  # Relative URL

        final_response = MagicMock()
        final_response.is_redirect = False
        final_response.headers = {"content-type": "text/html"}
        final_response.text = "<html><body>New page</body></html>"

        mock_client = MagicMock()
        mock_client.get.side_effect = [redirect_response, final_response]
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com")
        assert "New page" in result

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_truncates_large_content(self, mock_client_class, mock_is_safe):
        """Should truncate content that exceeds MAX_PAGE_CONTENT_LENGTH."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")

        # Create response with very large content
        large_content = "x" * (MAX_PAGE_CONTENT_LENGTH + 10000)
        mock_response = MagicMock()
        mock_response.is_redirect = False
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = f"<html><body>{large_content}</body></html>"

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com")
        assert "[content truncated]" in result
        # Content should be limited
        assert len(result) < MAX_PAGE_CONTENT_LENGTH + 1000  # Account for header/footer

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_handles_http_error(self, mock_client_class, mock_is_safe):
        """Should handle HTTP errors gracefully."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")

        mock_response = MagicMock()
        mock_response.is_redirect = False
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        )

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com/missing")
        assert "Error" in result
        assert "404" in result

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_handles_timeout(self, mock_client_class, mock_is_safe):
        """Should handle request timeouts gracefully."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")

        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.TimeoutException("Connection timed out")
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com")
        assert "Error" in result
        assert "timed out" in result.lower()

    @patch("src.agents.hed.is_safe_url")
    @patch("src.agents.hed.httpx.Client")
    def test_handles_request_error(self, mock_client_class, mock_is_safe):
        """Should handle generic request errors gracefully."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")

        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.RequestError("Connection refused")
        mock_client_class.return_value.__enter__.return_value = mock_client

        result = _fetch_page_content_impl("https://example.com")
        assert "Error" in result


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
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        assistant = HEDAssistant(model=model, preload_docs=False)

        # Should have 4 base tools, not 5
        assert len(assistant.tools) == 4
        tool_names = [t.name for t in assistant.tools]
        assert "fetch_current_page" not in tool_names

    def test_assistant_with_page_context(self):
        """Should add fetch_current_page tool when page context is provided."""
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
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        assistant = HEDAssistant(model=model, preload_docs=False)

        prompt = assistant.get_system_prompt()
        assert "Page Context" not in prompt

    def test_system_prompt_with_no_title(self):
        """Should show '(No title)' when title is None."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org/docs", title=None)
        assistant = HEDAssistant(model=model, preload_docs=False, page_context=page_context)

        prompt = assistant.get_system_prompt()
        assert "(No title)" in prompt

    @patch("src.agents.hed._fetch_page_content_impl")
    def test_fetch_current_page_tool_calls_impl(self, mock_fetch):
        """Should call _fetch_page_content_impl with bound URL."""
        mock_fetch.return_value = "# Content from https://hedtags.org\n\nTest content"

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org", title="HED Tags")
        assistant = HEDAssistant(model=model, preload_docs=False, page_context=page_context)

        # Find and invoke the fetch_current_page tool
        fetch_tool = next(t for t in assistant.tools if t.name == "fetch_current_page")
        result = fetch_tool.invoke({})

        # Verify the tool called the impl with the bound URL
        mock_fetch.assert_called_once_with("https://hedtags.org")
        assert "Test content" in result

    @patch("src.agents.hed._fetch_page_content_impl")
    def test_fetch_current_page_tool_bound_to_specific_url(self, mock_fetch):
        """Should only fetch the bound URL, not allow arbitrary URLs."""
        mock_fetch.return_value = "Content"

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://specific-page.com/doc", title="Doc")
        assistant = HEDAssistant(model=model, preload_docs=False, page_context=page_context)

        fetch_tool = next(t for t in assistant.tools if t.name == "fetch_current_page")

        # The tool takes no arguments - it's bound to the page URL
        fetch_tool.invoke({})

        # Should be called with the bound URL
        mock_fetch.assert_called_once_with("https://specific-page.com/doc")

    def test_page_context_properties(self):
        """Should have correct preloaded and available doc counts."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        assistant = HEDAssistant(model=model, preload_docs=False)

        # Without preloading, should have 0 preloaded docs
        assert assistant.preloaded_doc_count == 0
        # Should still know about available docs
        assert assistant.available_doc_count > 0
