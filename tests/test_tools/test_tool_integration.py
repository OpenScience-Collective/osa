"""Integration tests for HED documentation tool usage.

These tests verify that the tool functions work correctly for:
- Retrieving preloaded documents
- Retrieving on-demand documents
- Discovery via descriptions
- Error handling
- Tool docstring generation
"""

from src.tools.base import DocPage
from src.tools.fetcher import DocumentFetcher
from src.tools.hed import (
    HED_DOCS,
    format_hed_doc_list,
    get_preloaded_hed_content,
    retrieve_hed_doc,
    retrieve_hed_docs,
    retrieve_hed_docs_by_category,
)


class TestRetrieveHedDocs:
    """Tests for the main retrieve_hed_docs tool function."""

    def test_retrieve_preloaded_doc_success(self):
        """Test retrieving a preloaded document."""
        url = "https://www.hedtags.org/hed-resources/IntroductionToHed.html"
        result = retrieve_hed_docs(url)

        # Should return formatted content
        assert "# Introduction to HED" in result
        assert "Source:" in result
        assert url in result
        assert not result.startswith("Error")

    def test_retrieve_ondemand_doc_success(self):
        """Test retrieving an on-demand document."""
        url = "https://www.hedtags.org/hed-resources/HedAnnotationQuickstart.html"
        result = retrieve_hed_docs(url)

        # Should return formatted content
        assert "# HED annotation quickstart" in result
        assert "Source:" in result
        assert url in result
        assert not result.startswith("Error")

    def test_retrieve_unknown_url(self):
        """Test retrieving document with unknown URL."""
        url = "https://example.com/nonexistent.html"
        result = retrieve_hed_docs(url)

        # Should return error message
        assert result.startswith("Error retrieving")
        assert url in result

    def test_tool_docstring_includes_doc_list(self):
        """Test that tool docstring includes available documents."""
        docstring = retrieve_hed_docs.__doc__

        # Should include formatted doc list
        assert "Available documents:" in docstring
        assert "Introduction to HED" in docstring
        assert "HED annotation semantics" in docstring

        # Should include preloaded section
        assert "Preloaded" in docstring

        # Should include category sections
        assert "Quickstart" in docstring or "Quick Start" in docstring


class TestRetrieveHedDoc:
    """Tests for the underlying retrieve_hed_doc function."""

    def test_retrieve_doc_returns_retrieved_doc(self):
        """Test that retrieve_hed_doc returns RetrievedDoc object."""
        url = "https://www.hedtags.org/hed-resources/IntroductionToHed.html"
        doc = HED_DOCS.find_by_url(url)
        assert doc is not None

        fetcher = DocumentFetcher()  # Memory-only for tests
        result = retrieve_hed_doc(url, fetcher)

        assert result.title == "Introduction to HED"
        assert result.url == url
        assert result.success
        assert len(result.content) > 0

    def test_retrieve_doc_with_unknown_url(self):
        """Test error handling for unknown URL."""
        url = "https://example.com/unknown.html"
        fetcher = DocumentFetcher()
        result = retrieve_hed_doc(url, fetcher)

        assert not result.success
        assert result.error is not None
        assert "not found in HED registry" in result.error


class TestRetrieveByCategory:
    """Tests for category-based retrieval."""

    def test_retrieve_quickstart_category(self):
        """Test retrieving all quickstart documents."""
        fetcher = DocumentFetcher()
        results = retrieve_hed_docs_by_category("quickstart", fetcher)

        # Should have 3 quickstart docs
        assert len(results) == 3

        # Check that all are quickstart docs
        titles = {r.title for r in results}
        assert "HED annotation quickstart" in titles
        assert "BIDS annotation quickstart" in titles
        assert "HED annotation in NWB" in titles

        # At least some should be successful (network may be flaky)
        success_count = sum(1 for r in results if r.success)
        assert success_count >= 1, f"Expected at least 1 success, got {success_count}"

    def test_retrieve_core_category(self):
        """Test retrieving all core documents."""
        fetcher = DocumentFetcher()
        results = retrieve_hed_docs_by_category("core", fetcher)

        # Should have multiple core docs
        assert len(results) > 0

        # At least some should be successful (network may be flaky)
        success_count = sum(1 for r in results if r.success)
        assert success_count >= 1, f"Expected at least 1 success, got {success_count}"

    def test_retrieve_empty_category(self):
        """Test retrieving from a category with no documents."""
        fetcher = DocumentFetcher()
        results = retrieve_hed_docs_by_category("nonexistent", fetcher)

        # Should return empty list
        assert len(results) == 0


class TestPreloadedContent:
    """Tests for preloaded document functionality.

    These tests dynamically discover what should be preloaded from the registry
    rather than hardcoding expected documents.
    """

    def test_get_preloaded_hed_content(self):
        """Test fetching all preloaded HED documentation."""
        fetcher = DocumentFetcher()
        preloaded_content = get_preloaded_hed_content(fetcher)
        preloaded_docs = HED_DOCS.get_preloaded()

        # Should attempt to fetch all docs marked as preloaded
        # (some may fail due to network issues)
        assert len(preloaded_content) >= 1, "Expected at least 1 preloaded doc"

        # All returned content should be keyed by valid URLs
        for url in preloaded_content:
            assert url.startswith("https://"), f"Invalid URL key: {url}"

        # All content should be non-empty
        assert all(len(content) > 0 for content in preloaded_content.values())

        # URLs in result should match docs marked preload=True
        expected_urls = {doc.url for doc in preloaded_docs}
        for url in preloaded_content:
            assert url in expected_urls, f"Unexpected URL in preloaded: {url}"

    def test_preloaded_content_is_cleaned(self):
        """Test that preloaded content is cleaned markdown."""
        fetcher = DocumentFetcher(clean_markdown_content=True)
        preloaded = get_preloaded_hed_content(fetcher)

        # Check one document for cleaned content
        intro_url = "https://www.hedtags.org/hed-resources/IntroductionToHed.html"
        if intro_url in preloaded:
            content = preloaded[intro_url]
            # Should not contain HTML tags
            assert "<div" not in content
            assert "<p>" not in content


class TestFormatDocList:
    """Tests for formatted doc list generation."""

    def test_format_includes_all_sections(self):
        """Test that formatted list includes all expected sections."""
        formatted = format_hed_doc_list()

        # Should include preloaded section
        assert "Preloaded" in formatted

        # Should include various categories
        assert "Core" in formatted or "core" in formatted
        assert "Specification" in formatted or "specification" in formatted

    def test_format_is_deterministic(self):
        """Test that formatting is deterministic."""
        formatted1 = format_hed_doc_list()
        formatted2 = format_hed_doc_list()

        assert formatted1 == formatted2

    def test_format_includes_descriptions_by_default(self):
        """Test that format includes descriptions by default."""
        formatted = format_hed_doc_list()

        # Should include at least one description
        assert "Outlines the fundamental principles" in formatted
        assert "Provides an overview" in formatted


class TestDocumentDiscoveryByDescription:
    """Tests for finding documents based on descriptions."""

    def test_find_validation_doc_by_description(self):
        """Test finding validation doc using description keywords."""
        # Simulate agent searching for validation documentation
        validation_docs = [
            doc
            for doc in HED_DOCS.docs
            if "validat" in doc.description.lower() or "validat" in doc.title.lower()
        ]

        assert len(validation_docs) > 0

        # Should find the validation guide
        validation_titles = {doc.title for doc in validation_docs}
        assert "HED validation guide" in validation_titles

    def test_find_quickstart_docs_by_description(self):
        """Test finding quickstart docs using description keywords."""
        # Simulate agent searching for getting started resources
        quickstart_docs = [
            doc
            for doc in HED_DOCS.docs
            if any(
                keyword in doc.description.lower()
                for keyword in ["quick", "step-by-step", "tutorial", "concise"]
            )
        ]

        assert len(quickstart_docs) >= 3

        # Should include quickstart category docs
        quickstart_titles = {doc.title for doc in quickstart_docs}
        assert any("quickstart" in title.lower() for title in quickstart_titles)

    def test_find_tools_docs_by_description(self):
        """Test finding tools docs using description keywords."""
        # Simulate agent searching for tool documentation
        tools_docs = [
            doc
            for doc in HED_DOCS.docs
            if any(
                keyword in doc.description.lower()
                for keyword in ["python", "matlab", "javascript", "online"]
            )
        ]

        assert len(tools_docs) >= 4

        # Should find the tools category
        tools_titles = {doc.title for doc in tools_docs}
        assert any("python" in title.lower() for title in tools_titles)
        assert any("matlab" in title.lower() for title in tools_titles)

    def test_find_schema_docs_by_description(self):
        """Test finding schema docs using description keywords."""
        # Simulate agent searching for schema information
        schema_docs = [
            doc
            for doc in HED_DOCS.docs
            if "schema" in doc.description.lower() or "schema" in doc.title.lower()
        ]

        assert len(schema_docs) >= 2

        # Should include schema documentation
        # Note: actual schema is too large (~890KB), use hed-lsp tool for schema lookups instead
        schema_titles = {doc.title for doc in schema_docs}
        assert "HED schemas" in schema_titles or "Library schemas" in schema_titles


class TestFetcherCaching:
    """Tests for document fetcher caching behavior."""

    def test_fetcher_caches_documents(self):
        """Test that fetcher caches documents for reuse."""
        fetcher = DocumentFetcher()

        url = "https://www.hedtags.org/hed-resources/IntroductionToHed.html"
        doc = HED_DOCS.find_by_url(url)
        assert doc is not None

        # First fetch
        result1 = fetcher.fetch(doc)
        assert result1.success

        # Second fetch should use cache
        result2 = fetcher.fetch(doc)
        assert result2.success
        assert result2.content == result1.content

        # Verify cache is populated
        cached = fetcher.get_cached(doc.source_url)
        assert cached is not None

    def test_fetcher_cache_stats(self):
        """Test that cache statistics are tracked."""
        fetcher = DocumentFetcher()

        # Initially empty
        stats = fetcher.cache_stats()
        assert stats["memory_entries"] == 0

        # Fetch a document
        url = "https://www.hedtags.org/hed-resources/IntroductionToHed.html"
        doc = HED_DOCS.find_by_url(url)
        assert doc is not None
        fetcher.fetch(doc)

        # Should have cache entry
        stats = fetcher.cache_stats()
        assert stats["memory_entries"] >= 1


class TestToolErrorHandling:
    """Tests for error handling in tool functions."""

    def test_handle_network_error_gracefully(self):
        """Test that network errors are handled gracefully."""
        # Create a doc with invalid source URL
        invalid_doc = DocPage(
            title="Invalid Doc",
            url="https://example.com/test.html",
            source_url="https://invalid-domain-that-does-not-exist-12345.com/test.md",
            category="test",
            description="Test document",
        )

        fetcher = DocumentFetcher(timeout_seconds=2.0)
        result = fetcher.fetch(invalid_doc)

        # Should return error, not raise exception
        assert not result.success
        assert result.error is not None
        assert len(result.content) == 0

    def test_handle_http_404_gracefully(self):
        """Test that 404 errors are handled gracefully."""
        # Create a doc with URL that will 404
        notfound_doc = DocPage(
            title="Not Found Doc",
            url="https://www.hedtags.org/nonexistent.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/nonexistent.md",
            category="test",
            description="Test document",
        )

        fetcher = DocumentFetcher()
        result = fetcher.fetch(notfound_doc)

        # Should return error, not raise exception
        assert not result.success
        assert result.error is not None
        assert "404" in result.error or "Not Found" in result.error
