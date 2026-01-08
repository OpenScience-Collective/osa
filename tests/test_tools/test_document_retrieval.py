"""Tests for document retrieval system.

These tests verify:
- Document registry structure and organization
- Preloaded vs on-demand document handling
- Description availability for agent discovery
- Document fetching and caching
"""

from src.tools.base import DocPage
from src.tools.hed import HED_DOCS


class TestDocumentRegistry:
    """Tests for document registry structure and organization.

    These tests are designed to be dynamic - they verify behavior and consistency
    rather than hardcoding specific document counts or titles. This makes them
    resilient to changes in which documents are preloaded vs on-demand.
    """

    def test_hed_registry_exists(self):
        """Test that HED registry is properly initialized."""
        assert HED_DOCS is not None
        assert HED_DOCS.name == "hed"
        assert len(HED_DOCS.docs) > 0

    def test_preloaded_plus_ondemand_equals_total(self):
        """Test that preloaded + on-demand equals total documents."""
        preloaded = HED_DOCS.get_preloaded()
        on_demand = HED_DOCS.get_on_demand()

        assert len(preloaded) + len(on_demand) == len(HED_DOCS.docs)

    def test_preloaded_docs_have_preload_flag_true(self):
        """Test that all docs returned by get_preloaded() have preload=True."""
        preloaded = HED_DOCS.get_preloaded()

        for doc in preloaded:
            assert doc.preload is True, f"Doc '{doc.title}' in preloaded but preload={doc.preload}"

    def test_ondemand_docs_have_preload_flag_false(self):
        """Test that all docs returned by get_on_demand() have preload=False."""
        on_demand = HED_DOCS.get_on_demand()

        for doc in on_demand:
            assert doc.preload is False, f"Doc '{doc.title}' in on_demand but preload={doc.preload}"

    def test_preloaded_is_subset_of_total(self):
        """Test that preloaded docs are a proper subset when not all docs are preloaded."""
        preloaded = HED_DOCS.get_preloaded()

        # Preloaded should be smaller than total (we don't preload everything)
        assert len(preloaded) < len(HED_DOCS.docs), "Expected some docs to be on-demand"
        # But we should have at least 1 preloaded doc
        assert len(preloaded) >= 1, "Expected at least 1 preloaded doc"

    def test_no_duplicate_documents(self):
        """Test that no document appears in both preloaded and on-demand."""
        preloaded_urls = {doc.url for doc in HED_DOCS.get_preloaded()}
        ondemand_urls = {doc.url for doc in HED_DOCS.get_on_demand()}

        overlap = preloaded_urls & ondemand_urls
        assert len(overlap) == 0, f"Documents in both preloaded and on-demand: {overlap}"

    def test_all_documents_have_descriptions(self):
        """Test that all documents have non-empty descriptions."""
        for doc in HED_DOCS.docs:
            assert doc.description, f"Document '{doc.title}' has no description"
            assert len(doc.description) > 10, f"Description for '{doc.title}' too short"

    def test_all_documents_have_categories(self):
        """Test that all documents have categories."""
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

        for doc in HED_DOCS.docs:
            assert doc.category, f"Document '{doc.title}' has no category"
            assert doc.category in valid_categories, (
                f"Document '{doc.title}' has invalid category: {doc.category}"
            )

    def test_all_documents_have_urls(self):
        """Test that all documents have both HTML and source URLs."""
        for doc in HED_DOCS.docs:
            assert doc.url, f"Document '{doc.title}' has no HTML URL"
            assert doc.source_url, f"Document '{doc.title}' has no source URL"
            # URLs should be different (one is HTML, one is markdown)
            assert doc.url != doc.source_url or ".json" in doc.url

    def test_categories_exist(self):
        """Test that registry has multiple categories."""
        categories = HED_DOCS.get_categories()
        assert len(categories) >= 5  # Should have at least 5 different categories

    def test_find_by_url_html(self):
        """Test finding document by HTML URL."""
        # Dynamically pick any preloaded document
        preloaded = HED_DOCS.get_preloaded()
        assert len(preloaded) > 0, "Need at least one preloaded doc for this test"

        test_doc = preloaded[0]
        found = HED_DOCS.find_by_url(test_doc.url)

        assert found is not None
        assert found.title == test_doc.title
        assert found.preload is True

    def test_find_by_url_notfound(self):
        """Test finding document with non-existent URL."""
        doc = HED_DOCS.find_by_url("https://example.com/nonexistent")
        assert doc is None

    def test_get_by_category(self):
        """Test getting documents by category."""
        core_docs = HED_DOCS.get_by_category("core")
        assert len(core_docs) > 0

        # Check that all returned docs are in the correct category
        for doc in core_docs:
            assert doc.category == "core"

    def test_quickstart_category_exists(self):
        """Test that quickstart category has expected documents."""
        quickstart_docs = HED_DOCS.get_by_category("quickstart")
        quickstart_titles = [doc.title for doc in quickstart_docs]

        expected_in_quickstart = [
            "HED annotation quickstart",
            "BIDS annotation quickstart",
            "HED annotation in NWB",
        ]

        for expected_title in expected_in_quickstart:
            assert expected_title in quickstart_titles


class TestDocPageStructure:
    """Tests for DocPage data structure."""

    def test_docpage_creation(self):
        """Test creating a DocPage."""
        doc = DocPage(
            title="Test Doc",
            url="https://example.com/test.html",
            source_url="https://example.com/test.md",
            preload=False,
            category="test",
            description="Test description",
        )

        assert doc.title == "Test Doc"
        assert doc.url == "https://example.com/test.html"
        assert doc.source_url == "https://example.com/test.md"
        assert doc.preload is False
        assert doc.category == "test"
        assert doc.description == "Test description"

    def test_docpage_to_dict(self):
        """Test converting DocPage to dictionary."""
        doc = DocPage(
            title="Test",
            url="https://example.com/test.html",
            source_url="https://example.com/test.md",
            preload=True,
            category="test",
            description="A test document",
        )

        doc_dict = doc.to_dict()

        assert doc_dict["title"] == "Test"
        assert doc_dict["url"] == "https://example.com/test.html"
        assert doc_dict["source_url"] == "https://example.com/test.md"
        assert doc_dict["preload"] is True
        assert doc_dict["category"] == "test"
        assert doc_dict["description"] == "A test document"


class TestFormattedDocList:
    """Tests for formatted document list for agent consumption."""

    def test_format_doc_list_includes_preloaded(self):
        """Test that formatted list includes preloaded section."""
        formatted = HED_DOCS.format_doc_list(include_preloaded=True, include_descriptions=True)

        assert "### Preloaded Documents" in formatted
        assert "HED annotation semantics" in formatted
        assert "Basic annotation" in formatted

    def test_format_doc_list_includes_descriptions(self):
        """Test that formatted list includes descriptions."""
        formatted = HED_DOCS.format_doc_list(include_preloaded=True, include_descriptions=True)

        # Check that descriptions appear
        assert "Outlines the fundamental principles" in formatted
        assert "Covers the essential guidelines" in formatted

    def test_format_doc_list_without_descriptions(self):
        """Test formatted list without descriptions."""
        formatted = HED_DOCS.format_doc_list(include_preloaded=True, include_descriptions=False)

        # Titles should be present
        assert "HED annotation semantics" in formatted

        # Descriptions should NOT be present
        assert "Outlines the fundamental principles" not in formatted

    def test_format_doc_list_categories(self):
        """Test that formatted list includes all categories."""
        formatted = HED_DOCS.format_doc_list(include_preloaded=False, include_descriptions=True)

        # Check for category headers
        assert "### Quickstart:" in formatted or "### Quick Start" in formatted
        assert "### Tools:" in formatted
        assert "### Advanced:" in formatted

    def test_format_doc_list_deterministic(self):
        """Test that formatting is deterministic."""
        formatted1 = HED_DOCS.format_doc_list()
        formatted2 = HED_DOCS.format_doc_list()

        assert formatted1 == formatted2


class TestDocumentDescriptions:
    """Tests for document descriptions quality and consistency."""

    def test_description_length_reasonable(self):
        """Test that descriptions are reasonable length (1-3 sentences)."""
        for doc in HED_DOCS.docs:
            # Descriptions should be at least 20 chars
            assert len(doc.description) >= 20, f"{doc.title}: description too short"

            # Descriptions should be at most ~300 chars (roughly 3 sentences)
            assert len(doc.description) <= 400, f"{doc.title}: description too long"

    def test_descriptions_end_with_period(self):
        """Test that descriptions end with proper punctuation."""
        for doc in HED_DOCS.docs:
            # Should end with . or similar punctuation
            assert doc.description.rstrip().endswith((".", "!", "?")), (
                f"{doc.title}: description doesn't end with punctuation: '{doc.description}'"
            )

    def test_descriptions_are_informative(self):
        """Test that descriptions contain key information."""
        # Spot check a few descriptions
        intro_doc = HED_DOCS.find_by_url(
            "https://www.hedtags.org/hed-resources/IntroductionToHed.html"
        )
        assert intro_doc is not None
        assert (
            "overview" in intro_doc.description.lower()
            or "introduction" in intro_doc.description.lower()
        )

        quickstart_doc = HED_DOCS.find_by_url(
            "https://www.hedtags.org/hed-resources/HedAnnotationQuickstart.html"
        )
        assert quickstart_doc is not None
        assert (
            "quick" in quickstart_doc.description.lower()
            or "step" in quickstart_doc.description.lower()
        )

    def test_similar_docs_have_distinct_descriptions(self):
        """Test that similar documents have distinct descriptions."""
        # Get all validation/search/summary guides
        validation_doc = next(
            (d for d in HED_DOCS.docs if "validation guide" in d.title.lower()), None
        )
        search_doc = next((d for d in HED_DOCS.docs if "search guide" in d.title.lower()), None)
        summary_doc = next((d for d in HED_DOCS.docs if "summary guide" in d.title.lower()), None)

        assert validation_doc and search_doc and summary_doc

        # Descriptions should be different
        assert validation_doc.description != search_doc.description
        assert search_doc.description != summary_doc.description
        assert validation_doc.description != summary_doc.description

        # Each should mention its specific purpose
        assert "validat" in validation_doc.description.lower()
        assert (
            "search" in search_doc.description.lower() or "query" in search_doc.description.lower()
        )
        assert "summar" in summary_doc.description.lower()


class TestDocumentURLStructure:
    """Tests for URL structure and consistency."""

    def test_markdown_urls_are_raw_github(self):
        """Test that markdown URLs point to raw GitHub content."""
        for doc in HED_DOCS.docs:
            if doc.source_url.endswith(".md") or doc.source_url.endswith(".rst"):
                assert "raw.githubusercontent.com" in doc.source_url, (
                    f"{doc.title}: markdown URL should use raw.githubusercontent.com"
                )

    def test_html_urls_use_hedtags_or_raw(self):
        """Test that HTML URLs use hedtags.org or raw for JSON."""
        for doc in HED_DOCS.docs:
            if doc.url.endswith(".json"):
                # JSON files can be raw
                assert "raw.githubusercontent.com" in doc.url or "hedtags.org" in doc.url
            else:
                # HTML files should use hedtags.org
                assert "hedtags.org" in doc.url or "hed-" in doc.url, (
                    f"{doc.title}: unexpected HTML URL format: {doc.url}"
                )

    def test_url_correspondence(self):
        """Test that HTML and source URLs correspond logically."""
        for doc in HED_DOCS.docs:
            # If it's a markdown doc, source should be .md
            if ".md" in doc.source_url:
                # HTML version should be .html or similar (allow ndx- for NWB extensions)
                assert (".html" in doc.url) or ("hed-" in doc.url) or ("ndx-" in doc.url), (
                    f"{doc.title}: HTML URL doesn't match markdown source"
                )


class TestPreloadedVsOnDemand:
    """Tests for preloaded vs on-demand document distinction."""

    def test_preloaded_are_core_introductory_spec(self):
        """Test that preloaded docs are from core/introductory/spec categories."""
        preloaded = HED_DOCS.get_preloaded()
        preloaded_categories = {doc.category for doc in preloaded}

        # Preloaded should be core, introductory, specification, or schemas
        expected_categories = {"core", "introductory", "specification", "schemas"}
        assert preloaded_categories.issubset(expected_categories)

    def test_quickstarts_are_on_demand(self):
        """Test that quickstart documents are on-demand, not preloaded."""
        quickstarts = HED_DOCS.get_by_category("quickstart")

        for doc in quickstarts:
            assert doc.preload is False, f"Quickstart '{doc.title}' should not be preloaded"

    def test_tools_docs_are_on_demand(self):
        """Test that tools documentation is on-demand."""
        tools_docs = HED_DOCS.get_by_category("tools")

        for doc in tools_docs:
            assert doc.preload is False, f"Tools doc '{doc.title}' should not be preloaded"

    def test_advanced_docs_are_on_demand(self):
        """Test that advanced documentation is on-demand."""
        advanced_docs = HED_DOCS.get_by_category("advanced")

        for doc in advanced_docs:
            assert doc.preload is False, f"Advanced doc '{doc.title}' should not be preloaded"
