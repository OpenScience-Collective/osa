"""Tests for document fetching utility.

These tests use real HTTP requests to verify the fetching and caching
functionality. They require network access but test actual behavior.
"""

import time

import pytest

from src.tools.base import DocPage
from src.tools.fetcher import CacheEntry, DocumentFetcher, get_fetcher


class TestCacheEntry:
    """Tests for CacheEntry dataclass."""

    def test_create_cache_entry(self) -> None:
        """Test creating a cache entry."""
        entry = CacheEntry(
            content="# Test content",
            fetched_at=time.time(),
            source_url="https://example.com/test.md",
        )
        assert entry.content == "# Test content"
        assert entry.source_url == "https://example.com/test.md"


class TestDocumentFetcher:
    """Tests for DocumentFetcher class."""

    @pytest.fixture
    def fetcher(self, tmp_path) -> DocumentFetcher:
        """Create a fetcher with temporary cache directory."""
        return DocumentFetcher(
            cache_dir=tmp_path / "cache",
            cache_ttl_seconds=60,
        )

    @pytest.fixture
    def memory_fetcher(self) -> DocumentFetcher:
        """Create a fetcher with memory-only cache."""
        return DocumentFetcher(cache_ttl_seconds=60)

    def test_create_fetcher_with_cache_dir(self, tmp_path) -> None:
        """Test creating a fetcher with cache directory."""
        cache_dir = tmp_path / "doc_cache"
        fetcher = DocumentFetcher(cache_dir=cache_dir)
        assert cache_dir.exists()
        assert fetcher.cache_dir == cache_dir

    def test_create_memory_only_fetcher(self) -> None:
        """Test creating a fetcher without file cache."""
        fetcher = DocumentFetcher()
        assert fetcher.cache_dir is None

    def test_cache_key_generation(self, fetcher: DocumentFetcher) -> None:
        """Test that cache keys are consistent and short."""
        url = "https://example.com/some/long/path/document.md"
        key = fetcher._cache_key(url)

        # Key should be 16 characters (SHA256 truncated)
        assert len(key) == 16

        # Same URL should produce same key
        assert fetcher._cache_key(url) == key

        # Different URLs should produce different keys
        other_key = fetcher._cache_key("https://example.com/other.md")
        assert other_key != key

    def test_cache_validity_check(self, fetcher: DocumentFetcher) -> None:
        """Test cache entry validity checking."""
        # Recent entry should be valid
        recent = CacheEntry(
            content="recent",
            fetched_at=time.time(),
            source_url="https://example.com/recent.md",
        )
        assert fetcher._is_cache_valid(recent) is True

        # Old entry should be invalid
        old = CacheEntry(
            content="old",
            fetched_at=time.time() - 120,  # 2 minutes old, TTL is 60s
            source_url="https://example.com/old.md",
        )
        assert fetcher._is_cache_valid(old) is False

    def test_memory_cache_set_and_get(self, memory_fetcher: DocumentFetcher) -> None:
        """Test memory cache operations."""
        url = "https://example.com/test.md"
        content = "# Test Content"

        # Initially empty
        assert memory_fetcher.get_cached(url) is None

        # Save to cache
        memory_fetcher._save_to_cache(url, content)

        # Should be retrievable
        cached = memory_fetcher.get_cached(url)
        assert cached == content

    def test_file_cache_set_and_get(self, fetcher: DocumentFetcher) -> None:
        """Test file cache operations."""
        url = "https://example.com/test.md"
        content = "# File cached content"

        # Save to cache
        fetcher._save_to_cache(url, content)

        # Clear memory cache to force file read
        fetcher._memory_cache.clear()

        # Should still be retrievable from file
        cached = fetcher.get_cached(url)
        assert cached == content

    def test_fetch_real_document(self, fetcher: DocumentFetcher) -> None:
        """Test fetching a real document from GitHub.

        Uses the HED specification README which is a stable document.
        """
        doc = DocPage(
            title="HED Specification README",
            url="https://github.com/hed-standard/hed-specification",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/README.md",
        )

        result = fetcher.fetch(doc)

        assert result.success is True
        assert result.title == "HED Specification README"
        assert result.url == "https://github.com/hed-standard/hed-specification"
        assert len(result.content) > 0
        # The README should mention HED
        assert "HED" in result.content

    def test_fetch_caches_result(self, fetcher: DocumentFetcher) -> None:
        """Test that fetched documents are cached."""
        doc = DocPage(
            title="Test Caching",
            url="https://github.com/hed-standard/hed-specification",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/README.md",
        )

        # First fetch
        result1 = fetcher.fetch(doc)
        assert result1.success is True
        assert len(result1.content) > 100  # Should have substantial content

        # Should now be in cache
        cached = fetcher.get_cached(doc.source_url)
        assert cached is not None
        # Verify caching works - content should contain key terms from the README
        assert "HED" in cached
        assert "specification" in cached.lower()

    def test_fetch_uses_cache(self, fetcher: DocumentFetcher) -> None:
        """Test that subsequent fetches use cache."""
        url = "https://example.com/cached.md"
        cached_content = "# Pre-cached content"

        # Pre-populate cache
        fetcher._save_to_cache(url, cached_content)

        doc = DocPage(
            title="Cached Doc",
            url="https://example.com/cached.html",
            source_url=url,
        )

        # Fetch should return cached content (no network request)
        result = fetcher.fetch(doc)
        assert result.success is True
        assert result.content == cached_content

    def test_fetch_invalid_url(self, fetcher: DocumentFetcher) -> None:
        """Test fetching from an invalid URL."""
        doc = DocPage(
            title="Invalid",
            url="https://example.com/invalid.html",
            source_url="https://raw.githubusercontent.com/nonexistent-org/nonexistent-repo/main/NONEXISTENT.md",
        )

        result = fetcher.fetch(doc)

        assert result.success is False
        assert result.error is not None
        assert "404" in result.error or "Not Found" in result.error

    def test_fetch_many(self, fetcher: DocumentFetcher) -> None:
        """Test fetching multiple documents."""
        # Pre-cache one doc to test mixed cache/fetch
        fetcher._save_to_cache("https://example.com/cached.md", "# Cached")

        docs = [
            DocPage(
                title="Cached",
                url="https://example.com/cached.html",
                source_url="https://example.com/cached.md",
            ),
            DocPage(
                title="HED README",
                url="https://github.com/hed-standard/hed-specification",
                source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/README.md",
            ),
        ]

        results = fetcher.fetch_many(docs)

        assert len(results) == 2
        assert results[0].title == "Cached"
        assert results[0].content == "# Cached"
        assert results[1].title == "HED README"
        assert results[1].success is True

    def test_preload(self, fetcher: DocumentFetcher) -> None:
        """Test preloading documents."""
        docs = [
            DocPage(
                title="Preload Me",
                url="https://github.com/hed-standard/hed-specification",
                source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/README.md",
                preload=True,
            ),
            DocPage(
                title="Don't Preload",
                url="https://example.com/skip.html",
                source_url="https://example.com/skip.md",
                preload=False,
            ),
        ]

        preloaded = fetcher.preload(docs)

        # Only preload=True docs should be fetched
        assert len(preloaded) == 1
        assert "https://github.com/hed-standard/hed-specification" in preloaded
        assert "https://example.com/skip.html" not in preloaded

    def test_clear_cache(self, fetcher: DocumentFetcher) -> None:
        """Test clearing all caches."""
        url = "https://example.com/to-clear.md"
        fetcher._save_to_cache(url, "# Will be cleared")

        # Verify it's cached
        assert fetcher.get_cached(url) is not None

        # Clear cache
        fetcher.clear_cache()

        # Should be gone from memory
        assert len(fetcher._memory_cache) == 0

        # Should be gone from files too
        if fetcher.cache_dir:
            assert len(list(fetcher.cache_dir.glob("*.md"))) == 0

    def test_cache_stats(self, fetcher: DocumentFetcher) -> None:
        """Test cache statistics."""
        # Start empty
        stats = fetcher.cache_stats()
        assert stats["memory_entries"] == 0

        # Add some entries
        fetcher._save_to_cache("https://example.com/1.md", "content 1")
        fetcher._save_to_cache("https://example.com/2.md", "content 2")

        stats = fetcher.cache_stats()
        assert stats["memory_entries"] == 2
        assert stats["file_entries"] == 2
        assert stats["ttl_seconds"] == 60


class TestGetFetcher:
    """Tests for the get_fetcher factory function."""

    def test_get_default_fetcher(self) -> None:
        """Test getting the default fetcher instance."""
        # Reset the module-level default
        import src.tools.fetcher as fetcher_module

        fetcher_module._default_fetcher = None

        fetcher = get_fetcher()
        assert fetcher is not None
        assert fetcher.cache_dir is None  # Memory-only by default

    def test_get_fetcher_returns_same_instance(self) -> None:
        """Test that get_fetcher returns the same instance."""
        import src.tools.fetcher as fetcher_module

        fetcher_module._default_fetcher = None

        fetcher1 = get_fetcher()
        fetcher2 = get_fetcher()
        assert fetcher1 is fetcher2
