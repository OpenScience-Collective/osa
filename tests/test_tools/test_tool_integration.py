"""Integration tests for documentation tool usage.

These tests verify that the tool functions work correctly for:
- Retrieving preloaded documents
- Retrieving on-demand documents
- Discovery via descriptions
- Error handling
- Tool docstring generation
"""

import pytest
from langchain_core.language_models import FakeListChatModel

from src.assistants import discover_assistants, registry
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
