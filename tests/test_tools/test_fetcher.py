"""Tests for document fetching utility.

Most tests here exercise caching and configuration logic offline. A few
(marked ``network``) make real HTTP requests to verify actual fetch
behavior against a live document; see the ``@pytest.mark.network`` tests
below.
"""

import http.server
import threading
import time
from collections.abc import Iterator

import pytest

from src.tools.base import DocPage
from src.tools.fetcher import (
    CacheEntry,
    DocumentFetcher,
    _is_html,
    _served_as_html,
    get_fetcher,
)


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

    @pytest.mark.network
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

    @pytest.mark.network
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

    def _fetch_cached(self, fetcher: DocumentFetcher, source_url: str, text: str) -> str:
        """Put text in the cache as a document's source, and return what fetch() gives for it."""
        fetcher._save_to_cache(source_url, text)
        doc = DocPage(title="Doc", url="https://example.com/doc.html", source_url=source_url)
        result = fetcher.fetch(doc)
        assert result.success is True
        return result.content

    def test_rst_source_keeps_link_targets(self, fetcher: DocumentFetcher) -> None:
        """An RST link's target sits in angle brackets and is content, not an HTML tag."""
        text = "See the `NWB Inspector <https://nwbinspector.readthedocs.io/>`_ docs."
        content = self._fetch_cached(fetcher, "https://example.com/raw/docs/index.rst", text)
        assert content == text

    def test_python_source_keeps_comparisons_and_generics(self, fetcher: DocumentFetcher) -> None:
        """Code between a `<` and a later `>` is code: a comparison, a generic, a repr."""
        text = (
            "if a<b and c>d:\n"
            "    x: Mapping<str, int> = load()\n"
            "    print(raw)  # <Raw | sample_audvis_raw.fif, 376 x 166800>"
        )
        content = self._fetch_cached(
            fetcher, "https://example.com/raw/tutorials/plot_file.py", text
        )
        assert content == text

    def test_converted_html_page_keeps_angle_brackets(self, fetcher: DocumentFetcher) -> None:
        """An HTML page is cached as the markdown it was converted to, which has no tags
        left to strip; what is in angle brackets there is the page's own text."""
        text = (
            "The reader returns `<Info | 10 non-empty values>`.\n\nUse sub-<label> in file names."
        )
        content = self._fetch_cached(fetcher, "https://example.com/stable/overview.html", text)
        assert content == text

    def test_markdown_source_still_loses_inline_html(self, fetcher: DocumentFetcher) -> None:
        """A markdown source keeps the cleaning it had: inline HTML tags are dropped."""
        text = "# Title\n\n<details><summary>More</summary>Hidden text</details>\n\nLine<br/>break"
        for suffix in (".md", ".MD", ".markdown", ".mdx"):
            content = self._fetch_cached(fetcher, f"https://example.com/raw/README{suffix}", text)
            assert "<" not in content and ">" not in content
            assert "MoreHidden text" in content
            assert "Linebreak" in content

    def test_markdown_suffix_is_read_from_the_path_not_the_query(
        self, fetcher: DocumentFetcher
    ) -> None:
        """A query string or fragment does not change what kind of source a URL names."""
        markdown = self._fetch_cached(
            fetcher, "https://example.com/doc.md?ref=main#top", "Line<br/>break"
        )
        assert markdown == "Linebreak"
        text = "Line<br/>break and `link <https://example.org>`_"
        rst = self._fetch_cached(fetcher, "https://example.com/doc.rst?format=.md", text)
        assert rst == text

    @pytest.mark.network
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

    @pytest.mark.network
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

    @pytest.mark.network
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


class _LocalSite:
    """A real HTTP server on 127.0.0.1 that serves the pages a test registers."""

    def __init__(self) -> None:
        pages: dict[str, tuple[bytes, str]] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                page = pages.get(self.path.split("?")[0])
                if page is None:
                    self.send_error(404)
                    return
                body, content_type = page
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                """Keep the test output quiet."""

        self._pages = pages
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()

    def serve(self, path: str, body: str, content_type: str, encoding: str = "utf-8") -> str:
        """Serve body at path with the given Content-Type, and return its URL."""
        self._pages[path] = (body.encode(encoding), content_type)
        return f"http://127.0.0.1:{self._server.server_port}{path}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


class TestFetchOverHttp:
    """fetch() against a real local server: the network branch, with no mocks.

    These cover what the cached-source tests above cannot: the Content-Type header, the
    HTML sniffing and conversion, and what is written to the cache.
    """

    @pytest.fixture
    def site(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[_LocalSite]:
        """A local server; the environment's proxy settings must not intercept it."""
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        monkeypatch.setenv("no_proxy", "127.0.0.1")
        server = _LocalSite()
        yield server
        server.close()

    @pytest.fixture
    def fetcher(self, tmp_path) -> DocumentFetcher:
        """A fetcher whose file cache is in the test's temporary directory."""
        return DocumentFetcher(cache_dir=tmp_path / "cache", cache_ttl_seconds=60)

    def _fetch(self, fetcher: DocumentFetcher, source_url: str) -> str:
        doc = DocPage(title="Doc", url="https://example.com/doc.html", source_url=source_url)
        result = fetcher.fetch(doc)
        assert result.success is True, result.error
        return result.content

    @pytest.mark.parametrize(
        ("name", "prefix"),
        [
            ("doctype", ""),
            ("byte order mark", "\ufeff"),
            ("xml prolog", '<?xml version="1.0" encoding="utf-8"?>\n'),
            ("comment", "<!-- generated by a docs tool -->\n"),
            ("xml prolog and comment", '<?xml version="1.0"?>\n<!-- built -->\n'),
        ],
    )
    def test_html_page_is_converted_whatever_precedes_the_doctype(
        self, site: _LocalSite, fetcher: DocumentFetcher, name: str, prefix: str
    ) -> None:
        """A page is HTML whether or not a BOM, XML prolog or comment comes first: its
        markup is converted, and what its text shows as `List<String>` stays."""
        page = (
            f"{prefix}<!DOCTYPE html><html><head><title>T</title></head><body>"
            "<main><h1>Title</h1><p>Use <code>List&lt;String&gt;</code> and <b>bold</b>.</p>"
            "</main></body></html>"
        )
        url = site.serve("/guide.html", page, "text/html; charset=utf-8")

        content = self._fetch(fetcher, url)

        assert "# Title" in content, name
        assert "**bold**" in content, name
        assert "List<String>" in content, name
        assert "<main>" not in content and "<b>" not in content and "&lt;" not in content, name

    def test_script_and_style_text_is_dropped(
        self, site: _LocalSite, fetcher: DocumentFetcher
    ) -> None:
        """The text of a <script> or <style> is not page content, with or without a <main>."""
        script = "<script>\n<!--//\nvar tracker = 1;\n//-->\n</script>"
        style = "<style>p { margin: 0 }</style>"
        with_main = (
            f"<!DOCTYPE html><html><head>{style}</head><body><main><h1>T</h1>{script}"
            f"<p>Body text</p>{style}</main></body></html>"
        )
        without_main = f"<!DOCTYPE html><html><head>{style}</head><body>{script}<p>Body text</p>"
        for name, page in (("main.html", with_main), ("plain.html", without_main)):
            content = self._fetch(fetcher, site.serve(f"/{name}", page, "text/html"))
            assert "Body text" in content, name
            assert "tracker" not in content and "margin" not in content, name

    def test_html_page_is_cached_as_markdown(
        self, site: _LocalSite, fetcher: DocumentFetcher
    ) -> None:
        """The cache holds the converted page, so a second fetch cleans it the same way."""
        url = site.serve(
            "/page.html",
            "<!DOCTYPE html><html><body><main><h1>T</h1><p>See <code>List&lt;int&gt;</code>"
            "</p></main></body></html>",
            "text/html",
        )
        first = self._fetch(fetcher, url)
        cached = fetcher.get_cached(url)
        assert cached is not None and not cached.lstrip().startswith("<")
        assert self._fetch(fetcher, url) == first

    def test_html_fragment_served_as_html_is_converted(
        self, site: _LocalSite, fetcher: DocumentFetcher
    ) -> None:
        """A fragment has no doctype to sniff; the server's Content-Type says it is HTML."""
        body = "<div><h1>Title</h1><p>Use <b>bold</b> and <code>List&lt;String&gt;</code></p></div>"
        url = site.serve("/fragment", body, "text/html; charset=utf-8")

        content = self._fetch(fetcher, url)

        assert "# Title" in content
        assert "**bold**" in content
        assert "List<String>" in content
        assert "<div>" not in content and "<b>" not in content

    def test_same_fragment_served_as_text_is_left_alone(
        self, site: _LocalSite, fetcher: DocumentFetcher
    ) -> None:
        """The Content-Type decides: the same bytes labeled text/plain are not HTML."""
        body = "<div><h1>Title</h1><p>Use <b>bold</b></p></div>"
        url = site.serve("/fragment.txt", body, "text/plain; charset=utf-8")

        assert self._fetch(fetcher, url) == body

    def test_markdown_source_served_as_html_stays_markdown(
        self, site: _LocalSite, fetcher: DocumentFetcher
    ) -> None:
        """A .md URL is markdown even when its server labels it text/html."""
        body = '<p align="center">Logo</p>\n\n# Title\n\nSome *emphasis*<br/>text'
        url = site.serve("/README.md", body, "text/html")

        content = self._fetch(fetcher, url)

        assert "*emphasis*" in content and "\\*" not in content
        assert "<" not in content and ">" not in content
        assert "Logo" in content

    @pytest.mark.parametrize(
        "start",
        [
            '<p align="center">Logo</p>\n\n',
            "<!-- markdownlint-disable -->\n",
            "<header>Top</header>\n\n",
        ],
    )
    def test_markdown_that_starts_with_a_tag_or_comment_is_not_html(
        self, site: _LocalSite, fetcher: DocumentFetcher, start: str
    ) -> None:
        """READMEs start with a <p>, a lint comment or a <header>; none of these is a page."""
        url = site.serve("/README.md", f"{start}# Title\n\nSome *emphasis* here", "text/plain")

        content = self._fetch(fetcher, url)

        assert "# Title" in content
        assert "*emphasis*" in content and "\\*" not in content

    def test_rst_source_keeps_link_targets(
        self, site: _LocalSite, fetcher: DocumentFetcher
    ) -> None:
        text = "See the `NWB Inspector <https://nwbinspector.readthedocs.io/>`_ docs."
        url = site.serve("/docs/index.rst", text, "text/plain; charset=utf-8")

        assert self._fetch(fetcher, url) == text

    def test_python_source_keeps_comparisons_and_generics(
        self, site: _LocalSite, fetcher: DocumentFetcher
    ) -> None:
        text = "if a<b and c>d:\n    x: Mapping<str, int> = load()\n    print(raw)  # <Raw | f.fif>"
        url = site.serve("/tutorials/plot_file.py", text, "text/plain; charset=utf-8")

        assert self._fetch(fetcher, url) == text

    def test_markdown_source_loses_inline_html(
        self, site: _LocalSite, fetcher: DocumentFetcher
    ) -> None:
        body = "# Title\n\n<details><summary>More</summary>Hidden text</details>\n\nLine<br/>break"
        url = site.serve("/docs/guide.md", body, "text/plain; charset=utf-8")

        content = self._fetch(fetcher, url)

        assert "<" not in content and ">" not in content
        assert "MoreHidden text" in content and "Linebreak" in content

    def test_a_missing_page_is_reported_not_cleaned(
        self, site: _LocalSite, fetcher: DocumentFetcher
    ) -> None:
        url = site.serve("/exists.md", "# Here", "text/plain")
        missing = url.replace("exists.md", "missing.md")

        result = fetcher.fetch(
            DocPage(title="Doc", url="https://example.com/x", source_url=missing)
        )

        assert result.success is False
        assert result.error is not None and "404" in result.error


class TestHtmlDetection:
    """_is_html and _served_as_html decide whether a response is converted."""

    @pytest.mark.parametrize(
        "text",
        [
            "<!DOCTYPE html><html>",
            "<!doctype html>",
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">',
            "<HTML>",
            "<Html lang=en>",
            "  \n<html>",
            "\ufeff<!DOCTYPE html>",
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>',
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">',
            "<!-- a -->\n<!DOCTYPE html>",
            "<!-- a -->\n<!-- b -->\n<head><title>T</title>",
            "<body>text",
        ],
    )
    def test_html(self, text: str) -> None:
        assert _is_html(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "# Title",
            "See `x <https://example.org>`_",
            '<p align="center">Logo</p>\n# Title',
            "<div><h1>T</h1></div>",
            "<header>Top</header>",
            "<htmlfoo>",
            "<!-- lint -->\n# Title",
            "<!-- unclosed comment",
        ],
    )
    def test_not_html(self, text: str) -> None:
        assert _is_html(text) is False

    def test_a_run_of_comments_before_markdown_is_read_once(self) -> None:
        """Failing after many comments must not retry every way of splitting them."""
        assert _is_html("<!-- x -->" * 200 + "\n# Title\n" + "-->" * 2000) is False

    @pytest.mark.parametrize(
        ("content_type", "source_url", "expected"),
        [
            ("text/html", "https://example.com/page", True),
            ("text/html; charset=utf-8", "https://example.com/page", True),
            ("TEXT/HTML", "https://example.com/page.rst", True),
            ("application/xhtml+xml", "https://example.com/page", True),
            ("text/plain", "https://example.com/page", False),
            ("application/octet-stream", "https://example.com/page", False),
            ("", "https://example.com/page", False),
            ("text/html", "https://example.com/README.md", False),
            ("text/html", "https://example.com/README.md?ref=main", False),
        ],
    )
    def test_served_as_html(self, content_type: str, source_url: str, expected: bool) -> None:
        assert _served_as_html(content_type, source_url) is expected


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
