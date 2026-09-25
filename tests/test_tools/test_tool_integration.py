"""Integration tests for documentation tool usage.

These tests verify that the tool functions work correctly for:
- Retrieving preloaded documents
- Retrieving on-demand documents
- Discovery via descriptions
- Error handling
- Tool docstring generation

Several tests here fetch a real, live documentation URL (no mocked HTTP
boundary) rather than a registry-only lookup or a respx-mocked response;
those carry ``@pytest.mark.network`` so the unit job's `-m "not network"`
leaves them to the network job instead of spending the local suite's time
on a real request (#397).
"""

import logging

import httpx
import pytest
import respx
from langchain_core.language_models import FakeListChatModel

from src.assistants import discover_assistants, registry
from src.assistants.community import _create_retrieve_docs_tool
from src.tools.base import DocPage, DocRegistry
from src.tools.fetcher import DocumentFetcher

# Ensure assistants are discovered
discover_assistants()


class TestRetrieveDocsTool:
    """Tests for the retrieve_docs tool function."""

    @pytest.fixture
    def hed_assistant(self):
        """Create HED assistant for testing."""
        model = FakeListChatModel(responses=["Test response"])
        return registry.create_assistant("hed", model=model, preload_docs=False)

    @pytest.fixture
    def retrieve_tool(self, hed_assistant):
        """Get the retrieve_hed_docs tool."""
        tools = {t.name: t for t in hed_assistant.tools}
        return tools.get("retrieve_hed_docs")

    def test_retrieve_tool_exists(self, hed_assistant) -> None:
        """HED assistant should have retrieve_hed_docs tool."""
        tool_names = [t.name for t in hed_assistant.tools]
        assert "retrieve_hed_docs" in tool_names

    @pytest.mark.network
    def test_retrieve_preloaded_doc_success(self, retrieve_tool) -> None:
        """Test retrieving a preloaded document returns content."""
        assert retrieve_tool is not None

        # Get a preloaded doc URL from the registry
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        preloaded = [d for d in info.community_config.documentation if d.preload]
        assert len(preloaded) > 0, "Need preloaded docs for this test"

        # Convert HttpUrl to string
        url = str(preloaded[0].url)
        result = retrieve_tool.invoke({"url": url})

        # Should return formatted content (not an error)
        assert not result.startswith("Error")
        assert not result.startswith("Document not found")
        assert "Source:" in result or len(result) > 100

    @pytest.mark.network
    def test_retrieve_ondemand_doc_success(self, retrieve_tool) -> None:
        """Test retrieving an on-demand document returns content."""
        assert retrieve_tool is not None

        # Get an on-demand doc URL from the registry
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        on_demand = [d for d in info.community_config.documentation if not d.preload]
        assert len(on_demand) > 0, "Need on-demand docs for this test"

        # Convert HttpUrl to string
        url = str(on_demand[0].url)
        result = retrieve_tool.invoke({"url": url})

        # Should return formatted content (not an error about registry)
        assert "Document not found" not in result
        # May have network errors, but not registry errors
        if result.startswith("Error"):
            assert "registry" not in result.lower()

    def test_retrieve_unknown_url_returns_error(self, retrieve_tool) -> None:
        """Test retrieving document with unknown URL returns helpful error."""
        assert retrieve_tool is not None

        url = "https://example.com/nonexistent.html"
        result = retrieve_tool.invoke({"url": url})

        # Should return error message mentioning the URL
        assert "not found" in result.lower() or "error" in result.lower()
        assert url in result or "example.com" in result

    def test_tool_description_includes_doc_list(self, retrieve_tool) -> None:
        """Test that tool description includes available documents."""
        assert retrieve_tool is not None

        description = retrieve_tool.description

        # Should include info about available docs
        assert "Available" in description or "documentation" in description.lower()

        # Should include at least some document titles
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        docs = info.community_config.documentation
        titles_found = sum(1 for d in docs[:5] if d.title in description)
        assert titles_found >= 1, "Expected at least 1 document title in description"


class TestRetrieveDocsToolCitations:
    """Tests for retrieve_docs when the assistant is built with citations=True.

    Uses the same real-fetch pattern as TestRetrieveDocsTool (no mocked
    business logic): a live document fetch, but asserting the
    search_result block shape instead of the formatted string.
    """

    @pytest.fixture
    def hed_assistant_citable(self):
        """Create a HED assistant with native citations enabled."""
        model = FakeListChatModel(responses=["Test response"])
        return registry.create_assistant("hed", model=model, preload_docs=False, citations=True)

    @pytest.fixture
    def retrieve_tool_citable(self, hed_assistant_citable):
        """Get the citable retrieve_hed_docs tool."""
        tools = {t.name: t for t in hed_assistant_citable.tools}
        return tools.get("retrieve_hed_docs")

    @pytest.mark.network
    def test_returns_search_result_block_on_success(self, retrieve_tool_citable) -> None:
        """A successful fetch with citations=True returns one search_result block."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        docs = info.community_config.documentation
        assert len(docs) > 0

        url = str(docs[0].url)
        result = retrieve_tool_citable.invoke({"url": url})

        if isinstance(result, str):
            # Network fetch failed for this doc; not what this test targets,
            # and covered separately by the plain-string error-path test.
            pytest.skip(f"Live fetch did not succeed for {url}: {result[:200]}")

        assert isinstance(result, list)
        assert len(result) == 1
        block = result[0]
        assert block["type"] == "search_result"
        assert block["source"] == url
        assert block["citations"] == {"enabled": True}
        assert block["content"][0]["type"] == "text"
        assert len(block["content"][0]["text"]) > 0

    def test_unknown_url_still_returns_plain_error_string(self, retrieve_tool_citable) -> None:
        """A registry-lookup failure has nothing to cite, so it stays a string."""
        result = retrieve_tool_citable.invoke({"url": "https://example.com/nonexistent.html"})

        assert isinstance(result, str)
        assert "not found" in result.lower()


class TestRetrieveDocsCitationsWhenThePageHasNoText:
    """A fetch that succeeds but yields no text must not fail the whole request.

    ``RetrievedDoc.success`` is ``error is None``; it says nothing about
    there being content. A page that is entirely navigation chrome reduces
    to an empty string once the HTML is converted and cleaned, and arrives
    here as a success. ``build_search_result`` refuses empty text, and that
    ValueError escapes LangGraph's ToolNode into the router, which catches
    ValueError as a malformed request and answers the user with a 400. So
    the citable path has to notice the empty content itself and fall back
    to the string the OpenRouter path already returns.

    The HTTP layer is a respx fixture (allowed for exercising specific
    responses), while the fetching, HTML conversion, markdown cleaning and
    tool logic under test are all real.
    """

    PAGE_URL = "https://example.com/chrome-only.html"
    CHROME_ONLY_HTML = '<!DOCTYPE html><html><body><nav><a href="/">Home</a></nav></body></html>'

    def _tool(self, *, citations: bool, source_url: str):
        doc_registry = DocRegistry(
            name="test",
            docs=[
                DocPage(
                    title="Chrome Only Page",
                    url=self.PAGE_URL,
                    source_url=source_url,
                )
            ],
        )
        return _create_retrieve_docs_tool("test", "Test", doc_registry, citations=citations)

    @respx.mock
    def test_falls_back_to_a_string_instead_of_raising(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A distinct source_url per test: the fetcher is a process-wide
        # singleton with a live cache, so sharing one would let the first
        # test's body answer the second one's request.
        source_url = "https://example.com/chrome-only-citable.md"
        respx.get(source_url).mock(
            return_value=httpx.Response(
                200, text=self.CHROME_ONLY_HTML, headers={"content-type": "text/html"}
            )
        )
        tool = self._tool(citations=True, source_url=source_url)

        with caplog.at_level(logging.WARNING, logger="src.assistants.community"):
            result = tool.invoke({"url": self.PAGE_URL})

        assert isinstance(result, str), (
            "An empty page cannot become a search_result block, so the "
            "citable path must return the plain string rather than let "
            "build_search_result's ValueError reach the router"
        )
        assert self.PAGE_URL in result
        assert "cannot be cited" in caplog.text

    @respx.mock
    def test_both_provider_paths_return_a_string_naming_the_source(self) -> None:
        """The fallback keeps the two paths comparable, which is the point of it."""
        citable_url = "https://example.com/chrome-only-parity-citable.md"
        plain_url = "https://example.com/chrome-only-parity-plain.md"
        for url in (citable_url, plain_url):
            respx.get(url).mock(
                return_value=httpx.Response(
                    200, text=self.CHROME_ONLY_HTML, headers={"content-type": "text/html"}
                )
            )

        citable = self._tool(citations=True, source_url=citable_url).invoke({"url": self.PAGE_URL})
        plain = self._tool(citations=False, source_url=plain_url).invoke({"url": self.PAGE_URL})

        assert isinstance(citable, str) and isinstance(plain, str)
        for result in (citable, plain):
            assert "Chrome Only Page" in result
            assert self.PAGE_URL in result


class TestPreloadedContent:
    """Tests for preloaded document functionality."""

    def test_assistant_has_preloaded_docs_in_prompt(self) -> None:
        """Preloaded docs should appear in system prompt."""
        model = FakeListChatModel(responses=["Test"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=True)

        prompt = assistant.get_system_prompt()

        # Check that preloaded content marker or content appears
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        preloaded = [d for d in info.community_config.documentation if d.preload]

        # At least mention of preloaded docs should be in prompt
        assert "preloaded" in prompt.lower() or any(d.title in prompt for d in preloaded)

    def test_preload_false_skips_content(self) -> None:
        """preload_docs=False should not embed doc content."""
        model = FakeListChatModel(responses=["Test"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        prompt = assistant.get_system_prompt()

        # Prompt should be shorter without preloaded content
        # (exact check would be comparing with preload=True)
        assert len(prompt) < 100000, "Prompt too large, preloaded content may be included"


# Every test below calls DocumentFetcher.fetch() against a real URL (a
# registry doc or example.com), so the unit job's `-m "not network"` leaves
# them to the network job.
@pytest.mark.network
class TestDocumentFetcher:
    """Tests for DocumentFetcher functionality."""

    def test_fetcher_returns_content_for_known_doc(self) -> None:
        """Fetcher should return content for valid document."""
        fetcher = DocumentFetcher()

        # Get a doc registry
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        doc_registry = info.community_config.get_doc_registry()
        docs = doc_registry.docs
        assert len(docs) > 0

        # Try to fetch first doc (DocPage object)
        doc = docs[0]
        result = fetcher.fetch(doc)

        # Should get content or graceful error
        assert result is not None
        if result.success:
            assert len(result.content) > 0
        else:
            # Network error is OK, but should have error message
            assert result.error is not None

    def test_fetcher_handles_invalid_doc(self) -> None:
        """Fetcher should handle invalid URLs gracefully."""
        from src.tools.base import DocPage

        fetcher = DocumentFetcher()

        # Create a DocPage with an invalid URL
        invalid_doc = DocPage(
            title="Nonexistent",
            url="https://example.com/nonexistent.html",
            source_url="https://example.com/nonexistent-page-12345.md",
        )
        result = fetcher.fetch(invalid_doc)

        # Should not raise exception
        assert result is not None
        # Will likely fail, but gracefully
        if not result.success:
            assert result.error is not None

    def test_fetcher_caches_results(self) -> None:
        """Fetcher should cache successful fetches."""
        fetcher = DocumentFetcher()

        # Get a doc from registry
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        doc_registry = info.community_config.get_doc_registry()
        doc = doc_registry.docs[0]

        # Fetch twice
        result1 = fetcher.fetch(doc)
        result2 = fetcher.fetch(doc)

        # Results should be same
        assert result1.content == result2.content


class TestToolNaming:
    """Tests for tool naming conventions."""

    def test_hed_tools_have_hed_prefix(self) -> None:
        """HED tools should have 'hed' in their names."""
        model = FakeListChatModel(responses=["Test"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        tool_names = [t.name for t in assistant.tools]

        # Auto-generated tools should have community ID
        assert "retrieve_hed_docs" in tool_names
        assert "search_hed_discussions" in tool_names
        assert "search_hed_papers" in tool_names
        assert "list_hed_recent" in tool_names

    def test_specialized_tools_present(self) -> None:
        """Specialized HED tools from plugins should be present."""
        model = FakeListChatModel(responses=["Test"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        tool_names = [t.name for t in assistant.tools]

        # HED-specific tools from Python plugin
        assert "validate_hed_string" in tool_names
        assert "suggest_hed_tags" in tool_names
        assert "get_hed_schema_versions" in tool_names


class TestErrorHandling:
    """Tests for error handling in tools."""

    @pytest.mark.network
    def test_retrieve_tool_handles_network_error_gracefully(self) -> None:
        """Retrieve tool should handle network errors without crashing."""
        model = FakeListChatModel(responses=["Test"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        tools = {t.name: t for t in assistant.tools}
        retrieve_tool = tools.get("retrieve_hed_docs")

        # Use a URL that's in the registry but may fail to fetch
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        docs = [d for d in info.community_config.documentation if not d.preload]

        if docs:
            # Convert HttpUrl to string
            url = str(docs[0].url)
            # Should not raise exception
            result = retrieve_tool.invoke({"url": url})
            assert isinstance(result, str)

    def test_knowledge_tool_handles_missing_db(self) -> None:
        """Knowledge tools should handle missing database gracefully."""
        model = FakeListChatModel(responses=["Test"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        tools = {t.name: t for t in assistant.tools}
        search_tool = tools.get("search_hed_discussions")

        # Should return helpful message, not crash
        result = search_tool.invoke({"query": "validation error"})
        assert isinstance(result, str)
        # Either shows results or shows init message
        assert len(result) > 0
