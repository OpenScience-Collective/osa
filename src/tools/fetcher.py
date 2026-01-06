"""Document fetching utility with caching for OSA tools."""

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from src.tools.base import DocPage, RetrievedDoc
from src.tools.markdown_cleaner import clean_markdown


@dataclass
class CacheEntry:
    """A cached document entry."""

    content: str
    """The cached content."""

    fetched_at: float
    """Unix timestamp when fetched."""

    source_url: str
    """The URL this was fetched from."""


@dataclass
class DocumentFetcher:
    """Fetches and caches documentation content.

    Provides simple file-based caching to avoid repeated fetches
    of the same documents within a session or across sessions.
    """

    cache_dir: Path | None = None
    """Directory for file-based cache. None for memory-only."""

    cache_ttl_seconds: int = 3600
    """Time-to-live for cached entries (default: 1 hour)."""

    timeout_seconds: float = 30.0
    """HTTP request timeout."""

    user_agent: str = "OSA-DocumentFetcher/1.0"
    """User agent for HTTP requests."""

    clean_markdown_content: bool = True
    """Whether to clean and normalize markdown content."""

    _memory_cache: dict[str, CacheEntry] = field(default_factory=dict)
    """In-memory cache for fast access."""

    def __post_init__(self) -> None:
        """Initialize cache directory if specified."""
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, url: str) -> str:
        """Generate a cache key from URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def _is_cache_valid(self, entry: CacheEntry) -> bool:
        """Check if a cache entry is still valid."""
        age = time.time() - entry.fetched_at
        return age < self.cache_ttl_seconds

    def _get_from_memory(self, url: str) -> str | None:
        """Get content from memory cache if valid."""
        key = self._cache_key(url)
        if key in self._memory_cache:
            entry = self._memory_cache[key]
            if self._is_cache_valid(entry):
                return entry.content
            # Expired, remove from cache
            del self._memory_cache[key]
        return None

    def _get_from_file(self, url: str) -> str | None:
        """Get content from file cache if valid."""
        if not self.cache_dir:
            return None

        key = self._cache_key(url)
        cache_file = self.cache_dir / f"{key}.md"
        meta_file = self.cache_dir / f"{key}.meta"

        if not cache_file.exists() or not meta_file.exists():
            return None

        # Check metadata for expiration
        try:
            meta = meta_file.read_text().strip().split("\n")
            fetched_at = float(meta[0])
            if time.time() - fetched_at >= self.cache_ttl_seconds:
                # Expired
                cache_file.unlink(missing_ok=True)
                meta_file.unlink(missing_ok=True)
                return None

            content = cache_file.read_text()
            # Also populate memory cache
            self._memory_cache[key] = CacheEntry(
                content=content,
                fetched_at=fetched_at,
                source_url=url,
            )
            return content
        except (ValueError, IndexError, OSError):
            # Corrupted cache, clean up
            cache_file.unlink(missing_ok=True)
            meta_file.unlink(missing_ok=True)
            return None

    def _save_to_cache(self, url: str, content: str) -> None:
        """Save content to both memory and file cache."""
        key = self._cache_key(url)
        now = time.time()

        # Memory cache
        self._memory_cache[key] = CacheEntry(
            content=content,
            fetched_at=now,
            source_url=url,
        )

        # File cache
        if self.cache_dir:
            cache_file = self.cache_dir / f"{key}.md"
            meta_file = self.cache_dir / f"{key}.meta"
            try:
                cache_file.write_text(content)
                meta_file.write_text(f"{now}\n{url}")
            except OSError:
                # File write failed, memory cache still works
                pass

    def get_cached(self, url: str) -> str | None:
        """Get content from cache if available and valid.

        Checks memory cache first, then file cache.
        """
        # Try memory first (fastest)
        content = self._get_from_memory(url)
        if content is not None:
            return content

        # Try file cache
        return self._get_from_file(url)

    def fetch(self, doc: DocPage) -> RetrievedDoc:
        """Fetch a document, using cache if available.

        Args:
            doc: The document page to fetch.

        Returns:
            RetrievedDoc with content or error.
        """
        # Check cache first
        cached = self.get_cached(doc.source_url)
        if cached is not None:
            content = clean_markdown(cached) if self.clean_markdown_content else cached
            return RetrievedDoc(
                title=doc.title,
                url=doc.url,
                content=content,
            )

        # Fetch from network
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(
                    doc.source_url,
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True,
                )
                response.raise_for_status()
                content = response.text

                # Cache the raw content (before cleaning)
                self._save_to_cache(doc.source_url, content)

                # Clean markdown if enabled
                if self.clean_markdown_content:
                    content = clean_markdown(content)

                return RetrievedDoc(
                    title=doc.title,
                    url=doc.url,
                    content=content,
                )

        except httpx.HTTPStatusError as e:
            return RetrievedDoc(
                title=doc.title,
                url=doc.url,
                content="",
                error=f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
            )
        except httpx.RequestError as e:
            return RetrievedDoc(
                title=doc.title,
                url=doc.url,
                content="",
                error=f"Request failed: {e!s}",
            )

    def fetch_many(self, docs: list[DocPage]) -> list[RetrievedDoc]:
        """Fetch multiple documents.

        Args:
            docs: List of document pages to fetch.

        Returns:
            List of RetrievedDoc results in same order as input.
        """
        return [self.fetch(doc) for doc in docs]

    def preload(self, docs: list[DocPage]) -> dict[str, str]:
        """Preload documents and return content by URL.

        This is used to embed preloaded documents in system prompts.

        Args:
            docs: Documents to preload.

        Returns:
            Dictionary mapping URL to content for successful fetches.
        """
        results: dict[str, str] = {}
        for doc in docs:
            if doc.preload:
                retrieved = self.fetch(doc)
                if retrieved.success:
                    results[doc.url] = retrieved.content
        return results

    def clear_cache(self) -> None:
        """Clear all cached content."""
        self._memory_cache.clear()

        if self.cache_dir and self.cache_dir.exists():
            for f in self.cache_dir.glob("*.md"):
                f.unlink(missing_ok=True)
            for f in self.cache_dir.glob("*.meta"):
                f.unlink(missing_ok=True)

    def cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        memory_count = len(self._memory_cache)
        file_count = 0
        if self.cache_dir and self.cache_dir.exists():
            file_count = len(list(self.cache_dir.glob("*.md")))

        return {
            "memory_entries": memory_count,
            "file_entries": file_count,
            "cache_dir": str(self.cache_dir) if self.cache_dir else None,
            "ttl_seconds": self.cache_ttl_seconds,
        }


# Default fetcher instance for simple usage
_default_fetcher: DocumentFetcher | None = None


def get_fetcher(cache_dir: Path | None = None) -> DocumentFetcher:
    """Get or create the default document fetcher.

    Args:
        cache_dir: Optional cache directory. If not provided and no
            default fetcher exists, uses memory-only caching.

    Returns:
        The document fetcher instance.
    """
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = DocumentFetcher(cache_dir=cache_dir)
    return _default_fetcher
