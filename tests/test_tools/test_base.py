"""Tests for base tool infrastructure."""

import pytest

from src.tools.base import DocPage, DocRegistry, RetrievedDoc


class TestDocPage:
    """Tests for DocPage dataclass."""

    def test_create_doc_page(self) -> None:
        """Test creating a basic DocPage."""
        doc = DocPage(
            title="Test Doc",
            url="https://example.com/doc.html",
            source_url="https://raw.example.com/doc.md",
        )
        assert doc.title == "Test Doc"
        assert doc.url == "https://example.com/doc.html"
        assert doc.source_url == "https://raw.example.com/doc.md"
        assert doc.preload is False
        assert doc.category == "general"

    def test_create_preloaded_doc_page(self) -> None:
        """Test creating a preloaded DocPage."""
        doc = DocPage(
            title="Core Doc",
            url="https://example.com/core.html",
            source_url="https://raw.example.com/core.md",
            preload=True,
            category="getting-started",
        )
        assert doc.preload is True
        assert doc.category == "getting-started"

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        doc = DocPage(
            title="Test",
            url="https://example.com/test.html",
            source_url="https://raw.example.com/test.md",
            preload=True,
            category="reference",
        )
        data = doc.to_dict()
        assert data == {
            "title": "Test",
            "url": "https://example.com/test.html",
            "source_url": "https://raw.example.com/test.md",
            "preload": True,
            "category": "reference",
            "description": "",  # Default empty description
        }


class TestDocRegistry:
    """Tests for DocRegistry class."""

    @pytest.fixture
    def sample_registry(self) -> DocRegistry:
        """Create a sample registry for testing."""
        registry = DocRegistry(name="test")
        registry.add(
            DocPage(
                title="Intro",
                url="https://example.com/intro.html",
                source_url="https://raw.example.com/intro.md",
                preload=True,
                category="getting-started",
            )
        )
        registry.add(
            DocPage(
                title="Guide",
                url="https://example.com/guide.html",
                source_url="https://raw.example.com/guide.md",
                preload=True,
                category="getting-started",
            )
        )
        registry.add(
            DocPage(
                title="Reference",
                url="https://example.com/ref.html",
                source_url="https://raw.example.com/ref.md",
                category="reference",
            )
        )
        registry.add(
            DocPage(
                title="API",
                url="https://example.com/api.html",
                source_url="https://raw.example.com/api.md",
                category="reference",
            )
        )
        return registry

    def test_create_empty_registry(self) -> None:
        """Test creating an empty registry."""
        registry = DocRegistry(name="empty")
        assert registry.name == "empty"
        assert len(registry.docs) == 0

    def test_add_document(self) -> None:
        """Test adding a document to registry."""
        registry = DocRegistry(name="test")
        doc = DocPage(
            title="New Doc",
            url="https://example.com/new.html",
            source_url="https://raw.example.com/new.md",
        )
        registry.add(doc)
        assert len(registry.docs) == 1
        assert registry.docs[0].title == "New Doc"

    def test_get_preloaded(self, sample_registry: DocRegistry) -> None:
        """Test getting preloaded documents."""
        preloaded = sample_registry.get_preloaded()
        assert len(preloaded) == 2
        assert all(d.preload for d in preloaded)
        titles = [d.title for d in preloaded]
        assert "Intro" in titles
        assert "Guide" in titles

    def test_get_on_demand(self, sample_registry: DocRegistry) -> None:
        """Test getting on-demand documents."""
        on_demand = sample_registry.get_on_demand()
        assert len(on_demand) == 2
        assert all(not d.preload for d in on_demand)
        titles = [d.title for d in on_demand]
        assert "Reference" in titles
        assert "API" in titles

    def test_get_by_category(self, sample_registry: DocRegistry) -> None:
        """Test getting documents by category."""
        getting_started = sample_registry.get_by_category("getting-started")
        assert len(getting_started) == 2

        reference = sample_registry.get_by_category("reference")
        assert len(reference) == 2

        unknown = sample_registry.get_by_category("unknown")
        assert len(unknown) == 0

    def test_get_categories(self, sample_registry: DocRegistry) -> None:
        """Test getting unique categories in order."""
        categories = sample_registry.get_categories()
        assert categories == ["getting-started", "reference"]

    def test_find_by_url_found(self, sample_registry: DocRegistry) -> None:
        """Test finding a document by URL."""
        doc = sample_registry.find_by_url("https://example.com/intro.html")
        assert doc is not None
        assert doc.title == "Intro"

    def test_find_by_url_not_found(self, sample_registry: DocRegistry) -> None:
        """Test finding a document that doesn't exist."""
        doc = sample_registry.find_by_url("https://example.com/nonexistent.html")
        assert doc is None

    def test_format_doc_list(self, sample_registry: DocRegistry) -> None:
        """Test formatting document list for display."""
        formatted = sample_registry.format_doc_list()

        # Should include preloaded section
        assert "Preloaded Documents" in formatted
        assert "Intro" in formatted
        assert "Guide" in formatted

        # Should include category sections
        assert "Reference:" in formatted
        assert "API" in formatted

    def test_format_doc_list_no_preloaded(self, sample_registry: DocRegistry) -> None:
        """Test formatting without preloaded section."""
        formatted = sample_registry.format_doc_list(include_preloaded=False)
        assert "Preloaded Documents" not in formatted
        # On-demand docs should still be there
        assert "Reference:" in formatted


class TestRetrievedDoc:
    """Tests for RetrievedDoc dataclass."""

    def test_create_successful_retrieval(self) -> None:
        """Test creating a successfully retrieved document."""
        doc = RetrievedDoc(
            title="Test Doc",
            url="https://example.com/test.html",
            content="# Test\n\nThis is test content.",
        )
        assert doc.title == "Test Doc"
        assert doc.url == "https://example.com/test.html"
        assert doc.content == "# Test\n\nThis is test content."
        assert doc.error is None
        assert doc.success is True

    def test_create_failed_retrieval(self) -> None:
        """Test creating a failed retrieval."""
        doc = RetrievedDoc(
            title="Failed Doc",
            url="https://example.com/fail.html",
            content="",
            error="HTTP 404: Not Found",
        )
        assert doc.content == ""
        assert doc.error == "HTTP 404: Not Found"
        assert doc.success is False

    def test_to_dict_success(self) -> None:
        """Test serialization of successful retrieval."""
        doc = RetrievedDoc(
            title="Test",
            url="https://example.com/test.html",
            content="Content here",
        )
        data = doc.to_dict()
        assert data == {
            "title": "Test",
            "url": "https://example.com/test.html",
            "content": "Content here",
        }
        assert "error" not in data

    def test_to_dict_with_error(self) -> None:
        """Test serialization includes error when present."""
        doc = RetrievedDoc(
            title="Failed",
            url="https://example.com/fail.html",
            content="",
            error="Connection timeout",
        )
        data = doc.to_dict()
        assert data["error"] == "Connection timeout"
