"""Tests for DocRegistryProtocol implementation.

These tests verify that all document registries implement the protocol correctly.
Tests are parameterized and run against all registries defined in conftest.py.
"""

import pytest

from src.interfaces import DocPageProtocol, DocRegistryProtocol, RetrievedDocProtocol
from src.tools.base import RetrievedDoc


class TestRegistryProtocolCompliance:
    """Tests that verify protocol compliance."""

    def test_registry_implements_protocol(self, registry) -> None:
        """Registry should implement DocRegistryProtocol."""
        assert isinstance(registry, DocRegistryProtocol)

    def test_registry_has_name(self, registry) -> None:
        """Registry should have a non-empty name."""
        assert registry.name
        assert isinstance(registry.name, str)

    def test_registry_has_docs(self, registry) -> None:
        """Registry should have a docs list."""
        assert hasattr(registry, "docs")
        assert isinstance(registry.docs, list)


class TestRegistryPartitioning:
    """Tests for preload/on-demand partitioning.

    These are the core dynamic tests that verify consistency
    rather than specific counts.
    """

    def test_preloaded_plus_ondemand_equals_total(self, registry) -> None:
        """Preloaded + on-demand should equal total documents (no overlap, no gaps)."""
        preloaded = registry.get_preloaded()
        ondemand = registry.get_on_demand()

        assert len(preloaded) + len(ondemand) == len(registry.docs)

    def test_no_overlap_between_preloaded_and_ondemand(self, registry) -> None:
        """Preloaded and on-demand sets should not overlap."""
        preloaded_urls = {d.url for d in registry.get_preloaded()}
        ondemand_urls = {d.url for d in registry.get_on_demand()}

        overlap = preloaded_urls & ondemand_urls
        assert len(overlap) == 0, f"Found overlap: {overlap}"

    def test_preloaded_docs_have_preload_flag_true(self, registry) -> None:
        """All docs returned by get_preloaded() should have preload=True."""
        for doc in registry.get_preloaded():
            assert doc.preload is True, f"Doc '{doc.title}' has preload={doc.preload}"

    def test_ondemand_docs_have_preload_flag_false(self, registry) -> None:
        """All docs returned by get_on_demand() should have preload=False."""
        for doc in registry.get_on_demand():
            assert doc.preload is False, f"Doc '{doc.title}' has preload={doc.preload}"


class TestDocPageProtocol:
    """Tests that verify DocPage entries implement their protocol."""

    def test_all_docs_implement_protocol(self, registry) -> None:
        """All docs in registry should implement DocPageProtocol."""
        for doc in registry.docs:
            assert isinstance(doc, DocPageProtocol)

    def test_all_docs_have_required_fields(self, registry) -> None:
        """All documents should have required fields."""
        for doc in registry.docs:
            assert doc.title, "Doc missing title"
            assert doc.url, f"Doc '{doc.title}' missing url"
            assert doc.source_url, f"Doc '{doc.title}' missing source_url"
            assert doc.category, f"Doc '{doc.title}' missing category"
            assert doc.description, f"Doc '{doc.title}' missing description"

    def test_all_docs_have_valid_urls(self, registry) -> None:
        """All documents should have valid URL structure."""
        for doc in registry.docs:
            assert doc.url.startswith("https://"), f"Invalid url: {doc.url}"
            assert doc.source_url.startswith("https://"), f"Invalid source_url: {doc.source_url}"

    def test_all_docs_have_to_dict(self, registry) -> None:
        """All docs should implement to_dict method."""
        for doc in registry.docs:
            result = doc.to_dict()
            assert isinstance(result, dict)
            assert "title" in result
            assert "url" in result


class TestRegistryLookup:
    """Tests for document lookup functionality."""

    def test_find_by_url_returns_correct_doc(self, registry) -> None:
        """find_by_url should return the matching document."""
        if not registry.docs:
            pytest.skip("Registry has no docs")

        # Pick first doc and verify lookup
        test_doc = registry.docs[0]
        found = registry.find_by_url(test_doc.url)

        assert found is not None
        assert found.url == test_doc.url
        assert found.title == test_doc.title

    def test_find_by_url_returns_none_for_unknown(self, registry) -> None:
        """find_by_url should return None for unknown URLs."""
        found = registry.find_by_url("https://example.com/nonexistent.html")
        assert found is None

    def test_find_preloaded_doc_by_url(self, registry) -> None:
        """Should be able to find any preloaded doc by URL."""
        preloaded = registry.get_preloaded()
        if not preloaded:
            pytest.skip("Registry has no preloaded docs")

        for doc in preloaded:
            found = registry.find_by_url(doc.url)
            assert found is not None
            assert found.preload is True


class TestRegistryCategories:
    """Tests for category functionality."""

    def test_categories_are_consistent(self, registry) -> None:
        """Categories returned should match categories in docs."""
        categories = registry.get_categories()
        doc_categories = {doc.category for doc in registry.docs}

        # get_categories returns unique categories
        assert set(categories) == doc_categories

    def test_get_by_category_returns_matching_docs(self, registry) -> None:
        """get_by_category should return only docs in that category."""
        categories = registry.get_categories()
        if not categories:
            pytest.skip("Registry has no categories")

        for category in categories:
            docs = registry.get_by_category(category)
            for doc in docs:
                assert doc.category == category

    def test_all_docs_accounted_for_by_category(self, registry) -> None:
        """All docs should be accounted for via category queries."""
        all_via_category = []
        for category in registry.get_categories():
            all_via_category.extend(registry.get_by_category(category))

        assert len(all_via_category) == len(registry.docs)


class TestRegistryFormatting:
    """Tests for doc list formatting."""

    def test_format_doc_list_returns_string(self, registry) -> None:
        """format_doc_list should return a string."""
        formatted = registry.format_doc_list()
        assert isinstance(formatted, str)

    def test_format_doc_list_includes_urls(self, registry) -> None:
        """Formatted list should include document URLs."""
        if not registry.docs:
            pytest.skip("Registry has no docs")

        formatted = registry.format_doc_list()
        # Should include at least one URL
        assert "https://" in formatted

    def test_format_doc_list_is_deterministic(self, registry) -> None:
        """Formatting should produce same output on repeated calls."""
        formatted1 = registry.format_doc_list()
        formatted2 = registry.format_doc_list()
        assert formatted1 == formatted2


class TestRetrievedDocProtocol:
    """Tests that verify RetrievedDoc implements RetrievedDocProtocol."""

    def test_retrieved_doc_implements_protocol(self) -> None:
        """RetrievedDoc should implement RetrievedDocProtocol."""
        doc = RetrievedDoc(
            title="Test Doc",
            url="https://example.com/test.html",
            content="Test content",
        )
        assert isinstance(doc, RetrievedDocProtocol)

    def test_success_property_true_when_no_error(self) -> None:
        """success should be True when error is None."""
        doc = RetrievedDoc(
            title="Test Doc",
            url="https://example.com/test.html",
            content="Test content",
            error=None,
        )
        assert doc.success is True

    def test_success_property_false_when_error(self) -> None:
        """success should be False when error is set."""
        doc = RetrievedDoc(
            title="Test Doc",
            url="https://example.com/test.html",
            content="",
            error="Failed to fetch",
        )
        assert doc.success is False

    def test_to_dict_includes_required_fields(self) -> None:
        """to_dict should include title, url, and content."""
        doc = RetrievedDoc(
            title="Test Doc",
            url="https://example.com/test.html",
            content="Test content",
        )
        result = doc.to_dict()

        assert isinstance(result, dict)
        assert result["title"] == "Test Doc"
        assert result["url"] == "https://example.com/test.html"
        assert result["content"] == "Test content"

    def test_to_dict_includes_error_when_present(self) -> None:
        """to_dict should include error field when set."""
        doc = RetrievedDoc(
            title="Test Doc",
            url="https://example.com/test.html",
            content="",
            error="Failed to fetch",
        )
        result = doc.to_dict()

        assert "error" in result
        assert result["error"] == "Failed to fetch"

    def test_to_dict_excludes_error_when_none(self) -> None:
        """to_dict should not include error field when None."""
        doc = RetrievedDoc(
            title="Test Doc",
            url="https://example.com/test.html",
            content="Test content",
            error=None,
        )
        result = doc.to_dict()

        assert "error" not in result
