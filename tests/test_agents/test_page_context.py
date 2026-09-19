"""Tests for page context awareness feature."""

from unittest.mock import MagicMock, patch

import httpx
import respx

from src.assistants import discover_assistants, registry
from src.assistants.community import PageContext
from src.utils.page_fetcher import (
    MAX_PAGE_CONTENT_LENGTH,
    PageFetchResult,
    fetch_page,
    fetch_page_content,
    is_safe_url,
)

# Discover assistants at module load
discover_assistants()


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
    """Tests for fetch_page_content function."""

    def test_rejects_invalid_url(self):
        """Should reject URLs that don't start with http/https."""
        result = fetch_page_content("ftp://example.com")
        assert "Error" in result
        assert "http://" in result.lower() or "https://" in result.lower()

    def test_rejects_empty_url(self):
        """Should reject empty URLs."""
        result = fetch_page_content("")
        assert "Error" in result

    def test_rejects_none_url(self):
        """Should handle None-like URL."""
        # Test with empty string since None is not a valid type
        result = fetch_page_content("")
        assert "Error" in result

    def test_rejects_localhost(self):
        """Should reject localhost URLs."""
        result = fetch_page_content("http://localhost:8080")
        assert "Error" in result
        assert "not allowed" in result.lower()

    def test_rejects_private_ip(self):
        """Should reject private IP addresses."""
        result = fetch_page_content("http://192.168.1.1")
        assert "Error" in result
        assert "private" in result.lower()

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_successful_fetch(self, mock_is_safe):
        """Should fetch and convert HTML to markdown."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body><h1>Test</h1><p>Hello world</p></body></html>",
            )
        )

        result = fetch_page_content("https://example.com")
        assert "Content from https://example.com" in result
        assert "Test" in result
        assert "Hello world" in result

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_rejects_non_html_content(self, mock_is_safe):
        """Should reject non-HTML content types."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com/api").mock(
            return_value=httpx.Response(200, headers={"content-type": "application/json"}, json={})
        )

        result = fetch_page_content("https://example.com/api")
        assert "Error" in result
        assert "non-HTML" in result

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_handles_redirect_to_safe_url(self, mock_is_safe):
        """Should follow redirects to safe URLs."""
        # First call safe, redirect also safe
        mock_is_safe.side_effect = [
            (True, "", "93.184.216.34"),  # Original URL
            (True, "", "93.184.216.34"),  # Redirect URL
        ]
        # Anchored with a trailing slash: a bare "https://example.com" route
        # matches any path on the host in respx, which would shadow the
        # more specific /page route registered below.
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(302, headers={"location": "https://example.com/page"})
        )
        respx.get("https://example.com/page").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body>Final page</body></html>",
            )
        )

        result = fetch_page_content("https://example.com")
        assert "Final page" in result

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_blocks_redirect_to_unsafe_url(self, mock_is_safe):
        """Should block redirects to unsafe URLs (SSRF protection)."""
        # Original safe, redirect unsafe
        mock_is_safe.side_effect = [
            (True, "", "93.184.216.34"),  # Original URL safe
            (False, "Access to private IP ranges is not allowed", None),  # Redirect unsafe
        ]
        respx.get("https://example.com").mock(
            return_value=httpx.Response(302, headers={"location": "http://192.168.1.1/internal"})
        )

        result = fetch_page_content("https://example.com")
        assert "Error" in result
        assert "Redirect to unsafe URL" in result

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_handles_too_many_redirects(self, mock_is_safe):
        """Should limit redirect count."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        # Always redirect, including back to itself, to exercise the cap.
        # Anchored with a trailing slash: see test_handles_redirect_to_safe_url.
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(302, headers={"location": "https://example.com/loop"})
        )
        respx.get("https://example.com/loop").mock(
            return_value=httpx.Response(302, headers={"location": "https://example.com/loop"})
        )

        result = fetch_page_content("https://example.com")
        assert "Error" in result
        assert "Too many redirects" in result

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_handles_relative_redirect(self, mock_is_safe):
        """Should handle relative redirects properly."""
        mock_is_safe.side_effect = [
            (True, "", "93.184.216.34"),  # Original
            (True, "", "93.184.216.34"),  # Relative redirect converted to absolute
        ]
        # Anchored with a trailing slash: see test_handles_redirect_to_safe_url.
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(302, headers={"location": "/new-page"})
        )
        respx.get("https://example.com/new-page").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body>New page</body></html>",
            )
        )

        result = fetch_page_content("https://example.com")
        assert "New page" in result

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_truncates_large_content(self, mock_is_safe):
        """Should truncate content that exceeds MAX_PAGE_CONTENT_LENGTH."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        large_content = "x" * (MAX_PAGE_CONTENT_LENGTH + 10000)
        respx.get("https://example.com").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=f"<html><body>{large_content}</body></html>",
            )
        )

        result = fetch_page_content("https://example.com")
        assert "[content truncated]" in result
        # Content should be limited
        assert len(result) < MAX_PAGE_CONTENT_LENGTH + 1000  # Account for header/footer

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_handles_http_error(self, mock_is_safe):
        """Should handle HTTP errors gracefully."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))

        result = fetch_page_content("https://example.com/missing")
        assert "Error" in result
        assert "404" in result

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_handles_timeout(self, mock_is_safe):
        """Should handle request timeouts gracefully."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com").mock(
            side_effect=httpx.TimeoutException("Connection timed out")
        )

        result = fetch_page_content("https://example.com")
        assert "Error" in result
        assert "timed out" in result.lower()

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_handles_request_error(self, mock_is_safe):
        """Should handle generic request errors gracefully."""
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com").mock(side_effect=httpx.RequestError("Connection refused"))

        result = fetch_page_content("https://example.com")
        assert "Error" in result


class TestFetchPageReportsFailureStructurally:
    """Every failure branch must come back with ``success`` false.

    The point of the flag is that a caller which treats content and errors
    differently, such as the citable page tool, never has to guess from the
    message. It cannot guess correctly: these branches do not share a
    prefix, and the two below that read "Error fetching ..." are exactly the
    ones a `startswith("Error:")` check missed.
    """

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_http_status_error_is_a_failure(self, mock_is_safe):
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))

        result = fetch_page("https://example.com/missing")

        assert result.success is False
        assert "404" in result.content

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_network_error_is_a_failure(self, mock_is_safe):
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com").mock(side_effect=httpx.ConnectError("refused"))

        result = fetch_page("https://example.com")

        assert result.success is False

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_non_html_content_is_a_failure(self, mock_is_safe):
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com/data.json").mock(
            return_value=httpx.Response(200, json={"not": "html"})
        )

        result = fetch_page("https://example.com/data.json")

        assert result.success is False

    def test_invalid_url_is_a_failure(self):
        result = fetch_page("ftp://example.com")

        assert result.success is False

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_fetched_page_is_a_success(self, mock_is_safe):
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><body><h1>Docs</h1><p>Body text</p></body></html>",
            )
        )

        result = fetch_page("https://example.com")

        assert result.success is True
        assert "Body text" in result.content

    @patch("src.utils.page_fetcher.is_safe_url")
    @respx.mock
    def test_no_failure_branch_is_recognizable_by_prefix_alone(self, mock_is_safe):
        """The wordings genuinely differ, which is why the flag exists.

        If a future change normalized every message to one prefix, this
        would fail and whoever wrote it could decide knowingly whether to
        keep the flag. It must not be deleted just because the strings
        happen to line up on some particular day.
        """
        mock_is_safe.return_value = (True, "", "93.184.216.34")
        respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))

        by_prefix = fetch_page("https://example.com/missing").content.startswith("Error:")

        assert by_prefix is False


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


class TestCommunityAssistantWithPageContext:
    """Tests for CommunityAssistant with page context."""

    def test_assistant_without_page_context(self):
        """Should work without page context."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        # Should have tools but no fetch_current_page
        tool_names = [t.name for t in assistant.tools]
        assert "fetch_current_page" not in tool_names

    def test_assistant_with_page_context(self):
        """Should add fetch_current_page tool when page context is provided."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org", title="HED Tags")
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        tool_names = [t.name for t in assistant.tools]
        assert "fetch_current_page" in tool_names

    def test_assistant_with_empty_page_context_url(self):
        """Should not add tool when page context URL is empty."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url=None, title="No URL")
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        tool_names = [t.name for t in assistant.tools]
        assert "fetch_current_page" not in tool_names

    def test_system_prompt_includes_page_context(self):
        """Should include page context in system prompt."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org/docs", title="HED Docs")
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        prompt = assistant.get_system_prompt()
        assert "https://hedtags.org/docs" in prompt
        assert "HED Docs" in prompt
        assert "Page Context" in prompt
        assert "fetch_current_page" in prompt

    def test_system_prompt_without_page_context(self):
        """Should not include page context section without page context."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        prompt = assistant.get_system_prompt()
        assert "Page Context" not in prompt

    def test_system_prompt_with_no_title(self):
        """Should show '(No title)' when title is None."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org/docs", title=None)
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        prompt = assistant.get_system_prompt()
        assert "(No title)" in prompt

    @patch("src.assistants.community.fetch_page")
    def test_fetch_current_page_tool_calls_impl(self, mock_fetch):
        """Should call the page fetch with the bound URL."""
        mock_fetch.return_value = PageFetchResult(
            True, "# Content from https://hedtags.org\n\nTest content"
        )

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org", title="HED Tags")
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        # Find and invoke the fetch_current_page tool
        fetch_tool = next(t for t in assistant.tools if t.name == "fetch_current_page")
        result = fetch_tool.invoke({})

        # Verify the tool called the impl with the bound URL
        mock_fetch.assert_called_once_with("https://hedtags.org")
        assert "Test content" in result

    @patch("src.assistants.community.fetch_page")
    def test_fetch_current_page_tool_bound_to_specific_url(self, mock_fetch):
        """Should only fetch the bound URL, not allow arbitrary URLs."""
        mock_fetch.return_value = PageFetchResult(True, "Content")

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://specific-page.com/doc", title="Doc")
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        fetch_tool = next(t for t in assistant.tools if t.name == "fetch_current_page")

        # The tool takes no arguments - it's bound to the page URL
        fetch_tool.invoke({})

        # Should be called with the bound URL
        mock_fetch.assert_called_once_with("https://specific-page.com/doc")

    @patch("src.assistants.community.fetch_page")
    def test_fetch_current_page_returns_plain_string_when_citations_disabled(self, mock_fetch):
        """citations=False (default) must keep today's plain-string return, unchanged."""
        mock_fetch.return_value = PageFetchResult(True, "# Page\n\nSome fetched content")

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org/docs", title="Docs")
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        fetch_tool = next(t for t in assistant.tools if t.name == "fetch_current_page")
        result = fetch_tool.invoke({})

        assert result == "# Page\n\nSome fetched content"

    @patch("src.assistants.community.fetch_page")
    def test_fetch_current_page_returns_citation_block_when_enabled(self, mock_fetch):
        """citations=True on a successful fetch returns one search_result block."""
        mock_fetch.return_value = PageFetchResult(True, "# Page\n\nSome fetched content")

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org/docs", title="Docs")
        assistant = registry.create_assistant(
            "hed",
            model=model,
            preload_docs=False,
            page_context=page_context,
            citations=True,
        )

        fetch_tool = next(t for t in assistant.tools if t.name == "fetch_current_page")
        result = fetch_tool.invoke({})

        assert isinstance(result, list)
        assert len(result) == 1
        block = result[0]
        assert block["type"] == "search_result"
        assert block["source"] == "https://hedtags.org/docs"
        assert block["citations"] == {"enabled": True}
        assert block["content"] == [{"type": "text", "text": "# Page\n\nSome fetched content"}]

    @patch("src.assistants.community.fetch_page")
    def test_fetch_current_page_citations_enabled_but_fetch_errors(self, mock_fetch):
        """citations=True on a failed fetch still returns the plain error string."""
        mock_fetch.return_value = PageFetchResult(
            False, "Error: Invalid URL 'https://hedtags.org/docs'"
        )

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org/docs", title="Docs")
        assistant = registry.create_assistant(
            "hed",
            model=model,
            preload_docs=False,
            page_context=page_context,
            citations=True,
        )

        fetch_tool = next(t for t in assistant.tools if t.name == "fetch_current_page")
        result = fetch_tool.invoke({})

        assert isinstance(result, str)
        assert result.startswith("Error:")

    @patch("src.assistants.community.fetch_page")
    def test_fetch_current_page_gates_on_the_flag_not_the_error_wording(self, mock_fetch):
        """A failure that does not announce itself as "Error:" is still a failure.

        The gate used to read `content.startswith("Error:")`, which two of
        fetch_page's own failure branches do not match (both HTTP-status and
        network errors read "Error fetching <url>: ..."). A failed fetch was
        then handed to Claude as a citable search_result whose entire content
        was the error text, so the widget could render a numbered source with
        a working link and the error message as its tooltip: a fetch failure
        presented as page content the model had read.
        """
        mock_fetch.return_value = PageFetchResult(False, "the page could not be reached")

        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org/docs", title="Docs")
        assistant = registry.create_assistant(
            "hed",
            model=model,
            preload_docs=False,
            page_context=page_context,
            citations=True,
        )

        fetch_tool = next(t for t in assistant.tools if t.name == "fetch_current_page")
        result = fetch_tool.invoke({})

        assert result == "the page could not be reached"

    def test_page_context_properties(self):
        """Should have correct preloaded and available doc counts."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        # Without preloading, should have 0 preloaded docs
        assert assistant.preloaded_doc_count == 0
        # Should still know about available docs
        assert assistant.available_doc_count > 0

    def test_system_prompt_includes_widget_instructions(self):
        """Should include widget instructions in system prompt with guardrails."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(
            url="https://hedtags.org/tools",
            title="HED Tools",
            widget_instructions="Focus on online validation tools.",
        )
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        prompt = assistant.get_system_prompt()
        assert "Widget Page Context" in prompt
        assert "Focus on online validation tools." in prompt
        # Should include prompt injection guardrails
        assert "untrusted content" in prompt

    def test_system_prompt_widget_instructions_only(self):
        """Should include widget instructions even without URL."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(
            url=None,
            widget_instructions="This page is about HED online tools.",
        )
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        prompt = assistant.get_system_prompt()
        assert "Widget Page Context" in prompt
        assert "This page is about HED online tools." in prompt
        # Should NOT include page URL section
        assert "Page URL" not in prompt

    def test_system_prompt_no_widget_instructions(self):
        """Should not include widget instructions section when not provided."""
        model = MagicMock()
        model.bind_tools = MagicMock(return_value=model)
        page_context = PageContext(url="https://hedtags.org/docs", title="HED Docs")
        assistant = registry.create_assistant(
            "hed", model=model, preload_docs=False, page_context=page_context
        )

        prompt = assistant.get_system_prompt()
        assert "Widget Page Context" not in prompt
        assert "Page Context" in prompt
