"""Tests for document registry structure and organization.

These tests verify:
- Document registry structure from YAML config
- Preloaded vs on-demand document handling
- Description availability for agent discovery
- Document validation (URLs, categories, descriptions)

Tests are dynamic - they verify behavior and consistency rather than
hardcoding specific document counts or titles.
"""

import pytest

from src.assistants import discover_assistants, registry
from src.tools.base import DocRegistry

# Ensure assistants are discovered
discover_assistants()


@pytest.fixture
def hed_registry() -> DocRegistry:
    """Get the HED document registry."""
    info = registry.get("hed")
    assert info is not None, "HED assistant not found in registry"
    assert info.community_config is not None, "HED has no community config"
    return info.community_config.get_doc_registry()


class TestDocumentRegistryStructure:
    """Tests for document registry structure and organization."""

    def test_hed_registry_exists(self, hed_registry: DocRegistry) -> None:
        """Test that HED registry is properly initialized."""
        assert hed_registry is not None
        assert hed_registry.name == "hed"
        assert len(hed_registry.docs) > 0

    def test_preloaded_plus_ondemand_equals_total(self, hed_registry: DocRegistry) -> None:
        """Test that preloaded + on-demand equals total documents."""
        preloaded = hed_registry.get_preloaded()
        on_demand = hed_registry.get_on_demand()

        assert len(preloaded) + len(on_demand) == len(hed_registry.docs)

    def test_preloaded_docs_have_preload_flag_true(self, hed_registry: DocRegistry) -> None:
        """Test that all docs returned by get_preloaded() have preload=True."""
        preloaded = hed_registry.get_preloaded()

        for doc in preloaded:
            assert doc.preload is True, f"Doc '{doc.title}' in preloaded but preload={doc.preload}"

    def test_ondemand_docs_have_preload_flag_false(self, hed_registry: DocRegistry) -> None:
        """Test that all docs returned by get_on_demand() have preload=False."""
        on_demand = hed_registry.get_on_demand()

        for doc in on_demand:
            assert doc.preload is False, f"Doc '{doc.title}' in on_demand but preload={doc.preload}"

    def test_preloaded_is_subset_of_total(self, hed_registry: DocRegistry) -> None:
        """Test that preloaded docs are a proper subset when not all docs are preloaded."""
        preloaded = hed_registry.get_preloaded()

        # Preloaded should be smaller than total (we don't preload everything)
        assert len(preloaded) < len(hed_registry.docs), "Expected some docs to be on-demand"
        # But we should have at least 1 preloaded doc
        assert len(preloaded) >= 1, "Expected at least 1 preloaded doc"

    def test_no_duplicate_documents(self, hed_registry: DocRegistry) -> None:
        """Test that no document appears in both preloaded and on-demand."""
        preloaded_urls = {doc.url for doc in hed_registry.get_preloaded()}
        ondemand_urls = {doc.url for doc in hed_registry.get_on_demand()}

        overlap = preloaded_urls & ondemand_urls
        assert len(overlap) == 0, f"Documents in both preloaded and on-demand: {overlap}"


class TestDocumentValidation:
    """Tests for document field validation."""

    def test_all_documents_have_descriptions(self, hed_registry: DocRegistry) -> None:
        """Test that all documents have non-empty descriptions."""
        for doc in hed_registry.docs:
            assert doc.description, f"Document '{doc.title}' has no description"
            assert len(doc.description) > 10, f"Description for '{doc.title}' too short"

    def test_all_documents_have_categories(self, hed_registry: DocRegistry) -> None:
        """Test that all documents have valid categories."""
        valid_categories = {
            "core",
            "specification",
            "introductory",
            "quickstart",
            "tools",
            "advanced",
            "integration",
            "reference",
            "examples",
        }

        for doc in hed_registry.docs:
            assert doc.category, f"Document '{doc.title}' has no category"
            assert doc.category in valid_categories, (
                f"Document '{doc.title}' has invalid category: {doc.category}"
            )

    def test_all_documents_have_urls(self, hed_registry: DocRegistry) -> None:
        """Test that all documents have both HTML and source URLs."""
        for doc in hed_registry.docs:
            assert doc.url, f"Document '{doc.title}' has no HTML URL"
            assert doc.source_url, f"Document '{doc.title}' has no source URL"
            # URLs should be different (one is HTML, one is markdown/raw)
            # Exception: JSON files may have same URL
            if ".json" not in doc.url:
                assert doc.url != doc.source_url, (
                    f"Document '{doc.title}' has same URL for HTML and source"
                )

    def test_no_duplicate_source_urls(self, hed_registry: DocRegistry) -> None:
        """Test that no two documents share the same source URL."""
        source_urls: dict[str, str] = {}
        for doc in hed_registry.docs:
            if doc.source_url in source_urls:
                pytest.fail(
                    f"Duplicate source_url: '{doc.title}' and '{source_urls[doc.source_url]}' "
                    f"both use {doc.source_url}"
                )
            source_urls[doc.source_url] = doc.title


class TestDocumentLookup:
    """Tests for document lookup functionality."""

    def test_categories_exist(self, hed_registry: DocRegistry) -> None:
        """Test that registry has multiple categories."""
        categories = hed_registry.get_categories()
        assert len(categories) >= 5, f"Expected at least 5 categories, got {len(categories)}"

    def test_find_by_url_html(self, hed_registry: DocRegistry) -> None:
        """Test finding document by HTML URL."""
        # Dynamically pick any preloaded document
        preloaded = hed_registry.get_preloaded()
        assert len(preloaded) > 0, "Need at least one preloaded doc for this test"

        test_doc = preloaded[0]
        found = hed_registry.find_by_url(test_doc.url)

        assert found is not None
        assert found.title == test_doc.title
        assert found.preload is True

    def test_find_by_url_notfound(self, hed_registry: DocRegistry) -> None:
        """Test finding document with non-existent URL."""
        doc = hed_registry.find_by_url("https://example.com/nonexistent")
        assert doc is None

    def test_get_by_category(self, hed_registry: DocRegistry) -> None:
        """Test getting documents by category."""
        core_docs = hed_registry.get_by_category("core")
        assert len(core_docs) > 0

        # Check that all returned docs are in the correct category
        for doc in core_docs:
            assert doc.category == "core"

    def test_quickstart_category_has_expected_docs(self, hed_registry: DocRegistry) -> None:
        """Test that quickstart category has expected documents."""
        quickstart_docs = hed_registry.get_by_category("quickstart")
        quickstart_titles = [doc.title for doc in quickstart_docs]

        expected_in_quickstart = [
            "HED annotation quickstart",
            "BIDS annotation quickstart",
        ]

        for expected_title in expected_in_quickstart:
            assert expected_title in quickstart_titles, (
                f"Expected '{expected_title}' in quickstart category"
            )


class TestFormatDocList:
    """Tests for formatted doc list generation."""

    def test_format_includes_all_sections(self, hed_registry: DocRegistry) -> None:
        """Test that formatted list includes all expected sections."""
        formatted = hed_registry.format_doc_list()

        # Should include preloaded section
        assert "Preloaded" in formatted

        # Should include various categories (case-insensitive)
        formatted_lower = formatted.lower()
        assert "core" in formatted_lower
        assert "specification" in formatted_lower or "spec" in formatted_lower

    def test_format_is_deterministic(self, hed_registry: DocRegistry) -> None:
        """Test that formatting is deterministic."""
        formatted1 = hed_registry.format_doc_list()
        formatted2 = hed_registry.format_doc_list()
        assert formatted1 == formatted2

    def test_format_includes_document_titles(self, hed_registry: DocRegistry) -> None:
        """Test that formatted list includes document titles."""
        formatted = hed_registry.format_doc_list()

        # At least some document titles should appear
        titles_found = 0
        for doc in hed_registry.docs[:5]:  # Check first 5 docs
            if doc.title in formatted:
                titles_found += 1

        assert titles_found >= 3, "Expected at least 3 document titles in formatted list"
