"""Tests for the search_result block builder (src/tools/citations.py)."""

import pytest

from src.tools.citations import build_search_result, truncate


class TestTruncate:
    """Tests for the shared truncation helper."""

    def test_returns_text_unchanged_when_within_limit(self) -> None:
        assert truncate("short", 100) == "short"

    def test_returns_text_unchanged_when_exactly_at_limit(self) -> None:
        text = "x" * 50
        assert truncate(text, 50) == text

    def test_truncates_and_appends_default_suffix(self) -> None:
        text = "x" * 300
        result = truncate(text, 200)
        assert result == "x" * 200 + "..."

    def test_truncates_with_custom_suffix(self) -> None:
        text = "x" * 300
        result = truncate(text, 200, suffix="\n\n... [truncated for length]")
        assert result == "x" * 200 + "\n\n... [truncated for length]"

    def test_empty_text_is_never_truncated(self) -> None:
        assert truncate("", 10) == ""


class TestBuildSearchResult:
    """Tests for the search_result block shape."""

    def test_shape_matches_verified_schema(self) -> None:
        block = build_search_result(
            source="https://example.com/doc", title="Example Doc", text="Some content."
        )
        assert block == {
            "type": "search_result",
            "source": "https://example.com/doc",
            "title": "Example Doc",
            "content": [{"type": "text", "text": "Some content."}],
            "citations": {"enabled": True},
        }

    def test_citations_are_enabled(self) -> None:
        block = build_search_result("src", "title", "text")
        assert block["citations"] == {"enabled": True}

    def test_content_is_a_single_non_empty_text_block(self) -> None:
        block = build_search_result("src", "title", "the retrieved text")
        assert len(block["content"]) == 1
        assert block["content"][0]["type"] == "text"
        assert block["content"][0]["text"] == "the retrieved text"

    def test_source_and_title_are_passed_through_unchanged(self) -> None:
        block = build_search_result("some-stable-id", "A Title", "text")
        assert block["source"] == "some-stable-id"
        assert block["title"] == "A Title"

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            build_search_result("src", "title", "")
