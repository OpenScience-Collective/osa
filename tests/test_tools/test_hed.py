"""Tests for HED documentation retrieval tools.

These tests use real HTTP requests to verify the HED document
retrieval functionality.
"""

import pytest

from src.tools.base import DocRegistry
from src.tools.fetcher import DocumentFetcher
from src.tools.hed import (
    HED_DOCS,
    format_hed_doc_list,
    get_hed_registry,
    get_preloaded_hed_content,
    retrieve_hed_doc,
    retrieve_hed_docs,
    retrieve_hed_docs_by_category,
)


class TestHEDRegistry:
    """Tests for the HED documentation registry."""

    def test_hed_docs_is_registry(self) -> None:
        """Test that HED_DOCS is a valid registry."""
        assert isinstance(HED_DOCS, DocRegistry)
        assert HED_DOCS.name == "hed"
        assert len(HED_DOCS.docs) > 0

    def test_get_hed_registry(self) -> None:
        """Test getting the HED registry."""
        registry = get_hed_registry()
        assert registry is HED_DOCS

    def test_has_preloaded_docs(self) -> None:
        """Test that registry has preloaded documents."""
        preloaded = HED_DOCS.get_preloaded()
        # Should have at least 1 preloaded doc
        assert len(preloaded) >= 1

        # All preloaded docs should have preload=True
        for doc in preloaded:
            assert doc.preload is True

    def test_has_on_demand_docs(self) -> None:
        """Test that registry has on-demand documents."""
        on_demand = HED_DOCS.get_on_demand()
        # Should have on-demand docs (not everything is preloaded)
        assert len(on_demand) >= 1

        # All on-demand docs should have preload=False
        for doc in on_demand:
            assert doc.preload is False

    def test_has_expected_categories(self) -> None:
        """Test that registry has expected categories."""
        categories = HED_DOCS.get_categories()
        assert "core" in categories
        assert "specification" in categories
        assert "tools" in categories

    def test_all_docs_have_valid_urls(self) -> None:
        """Test that all documents have valid URL structure."""
        for doc in HED_DOCS.docs:
            assert doc.url.startswith("https://")
            assert doc.source_url.startswith("https://")
            # Source URLs should be raw content URLs
            assert "raw.githubusercontent.com" in doc.source_url


class TestRetrieveHEDDoc:
    """Tests for retrieving individual HED documents."""

    @pytest.fixture
    def fetcher(self, tmp_path) -> DocumentFetcher:
        """Create a fetcher with temporary cache."""
        return DocumentFetcher(cache_dir=tmp_path / "cache")

    def test_retrieve_known_doc(self, fetcher: DocumentFetcher) -> None:
        """Test retrieving a known HED document."""
        # Use the terminology URL from the registry (preloaded)
        url = "https://www.hedtags.org/hed-specification/02_Terminology.html"

        result = retrieve_hed_doc(url, fetcher)

        assert result.success is True
        assert result.title == "HED terminology"
        assert len(result.content) > 0
        # Content should mention HED
        assert "HED" in result.content or "Hierarchical" in result.content

    def test_retrieve_unknown_doc(self, fetcher: DocumentFetcher) -> None:
        """Test retrieving an unknown URL."""
        url = "https://example.com/not-in-registry.html"

        result = retrieve_hed_doc(url, fetcher)

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_retrieve_uses_default_fetcher(self) -> None:
        """Test that retrieve_hed_doc works without explicit fetcher."""
        url = "https://www.hedtags.org/hed-specification/02_Terminology.html"

        # Should work with default fetcher
        result = retrieve_hed_doc(url)

        assert result.success is True


class TestRetrieveHEDDocsByCategory:
    """Tests for retrieving HED documents by category."""

    @pytest.fixture
    def fetcher(self, tmp_path) -> DocumentFetcher:
        """Create a fetcher with temporary cache."""
        return DocumentFetcher(cache_dir=tmp_path / "cache")

    def test_retrieve_core_category(self, fetcher: DocumentFetcher) -> None:
        """Test retrieving core category."""
        results = retrieve_hed_docs_by_category("core", fetcher)

        # Should have 4 docs in core category (1 preloaded + 3 on-demand)
        assert len(results) >= 1
        # At least one should succeed
        successes = [r for r in results if r.success]
        assert len(successes) >= 1

    def test_retrieve_empty_category(self, fetcher: DocumentFetcher) -> None:
        """Test retrieving from non-existent category."""
        results = retrieve_hed_docs_by_category("nonexistent", fetcher)
        assert len(results) == 0


class TestGetPreloadedHEDContent:
    """Tests for preloading HED documentation."""

    @pytest.fixture
    def fetcher(self, tmp_path) -> DocumentFetcher:
        """Create a fetcher with temporary cache."""
        return DocumentFetcher(cache_dir=tmp_path / "cache")

    def test_preload_returns_content(self, fetcher: DocumentFetcher) -> None:
        """Test that preloading returns document content."""
        content = get_preloaded_hed_content(fetcher)

        assert isinstance(content, dict)
        # Should have 6 preloaded docs
        assert len(content) >= 1

        # Content should be keyed by URL
        for url, text in content.items():
            assert url.startswith("https://")
            assert len(text) > 0

    def test_preload_returns_all_preloaded_docs(self, fetcher: DocumentFetcher) -> None:
        """Test that preload returns content for all docs marked as preloaded."""
        content = get_preloaded_hed_content(fetcher)
        preloaded_docs = HED_DOCS.get_preloaded()

        # Should return content keyed by URL for all preloaded docs
        for doc in preloaded_docs:
            assert doc.url in content, f"Missing preloaded doc: {doc.title}"
            assert len(content[doc.url]) > 0, f"Empty content for: {doc.title}"


class TestFormatHEDDocList:
    """Tests for formatting HED document list."""

    def test_format_includes_sections(self) -> None:
        """Test that formatted list includes expected sections."""
        formatted = format_hed_doc_list()

        assert "Preloaded Documents" in formatted
        # Should have category sections for on-demand docs
        assert "Specification:" in formatted
        assert "Tools:" in formatted

    def test_format_includes_urls(self) -> None:
        """Test that formatted list includes document URLs."""
        formatted = format_hed_doc_list()

        assert "https://" in formatted
        assert "hedtags.org" in formatted.lower()


class TestRetrieveHEDDocsTool:
    """Tests for the LangChain-compatible tool function."""

    def test_tool_has_docstring(self) -> None:
        """Test that the tool has proper documentation."""
        assert retrieve_hed_docs.__doc__ is not None
        assert "HED" in retrieve_hed_docs.__doc__
        # Should include the doc list
        assert "https://" in retrieve_hed_docs.__doc__

    def test_tool_retrieves_valid_doc(self) -> None:
        """Test retrieving a document through the tool interface."""
        url = "https://www.hedtags.org/hed-specification/02_Terminology.html"
        result = retrieve_hed_docs(url)

        # Should return formatted content
        assert isinstance(result, str)
        assert "HED terminology" in result
        assert "Source:" in result

    def test_tool_handles_invalid_url(self) -> None:
        """Test tool handles invalid URL gracefully."""
        url = "https://example.com/invalid.html"
        result = retrieve_hed_docs(url)

        assert isinstance(result, str)
        assert "Error" in result or "error" in result
