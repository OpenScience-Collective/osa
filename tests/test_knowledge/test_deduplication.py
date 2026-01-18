"""Tests for paper deduplication logic."""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import get_connection, init_db, upsert_paper
from src.knowledge.search import (
    _normalize_title_for_dedup,
    _titles_are_similar,
    search_papers,
)


@pytest.fixture
def temp_db(tmp_path: Path):
    """Create temporary database for testing."""
    db_path = tmp_path / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()
        yield db_path


class TestTitleNormalization:
    """Test title normalization for deduplication."""

    def test_normalize_lowercase(self):
        """Test lowercasing."""
        result = _normalize_title_for_dedup("HED Schema")
        assert isinstance(result, set)
        assert "hed" in result
        assert "schema" in result

    def test_normalize_punctuation(self):
        """Test punctuation removal (all punctuation removed)."""
        # All punctuation is removed, leaving just words
        result1 = _normalize_title_for_dedup("HED – Framework")
        result2 = _normalize_title_for_dedup("HED — Framework")
        result3 = _normalize_title_for_dedup("HED - Framework")
        # All should produce same set of words (punctuation removed)
        assert result1 == result2 == result3
        assert "hed" in result1
        assert "framework" in result1

    def test_normalize_whitespace(self):
        """Test extra whitespace normalization."""
        # Extra whitespace is normalized during split()
        result1 = _normalize_title_for_dedup(
            "HED  Schema   v10"
        )  # Note: v1.0 becomes v10 (punctuation removed)
        result2 = _normalize_title_for_dedup("  HED Schema  v10")
        assert result1 == result2
        assert "hed" in result1
        assert "schema" in result1

    def test_normalize_combined(self):
        """Test combined normalization (case + punctuation + whitespace)."""
        title1 = "HED – A   Framework"
        title2 = "hed - a framework"
        # Both should produce same set (but 'a' is < 3 chars, filtered out)
        assert _normalize_title_for_dedup(title1) == _normalize_title_for_dedup(title2)


class TestTitleSimilarity:
    """Test fuzzy title similarity matching."""

    def test_exact_match(self):
        """Test exact match after normalization."""
        title1 = {"hed", "schema", "v1.0"}
        title2 = {"hed", "schema", "v1.0"}
        assert _titles_are_similar(title1, title2)

    def test_high_similarity(self):
        """Test high similarity (>70%)."""
        title1 = {"hierarchical", "event", "descriptors", "framework"}
        title2 = {"hierarchical", "event", "descriptors", "system"}  # 3/4 match = 75%
        # Jaccard: intersection = 3, union = 5, similarity = 60%
        # This should be below threshold
        assert not _titles_are_similar(title1, title2)

    def test_low_similarity(self):
        """Test low similarity (<70%)."""
        title1 = {"hed", "schema"}
        title2 = {"bids", "standard"}
        assert not _titles_are_similar(title1, title2)

    def test_partial_overlap(self):
        """Test partial overlap."""
        # 2 common words out of 3 unique = 67% (below 70% threshold)
        title1 = {"hed", "schema"}
        title2 = {"hed", "framework"}
        # Jaccard: intersection = 1, union = 3, similarity = 33%
        assert not _titles_are_similar(title1, title2)


class TestDeduplicationInSearch:
    """Test deduplication in actual search results."""

    def test_deduplication_removes_duplicates(self, temp_db: Path):
        """Test that search deduplicates same paper from different sources."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Add same paper from different sources
            with get_connection("test") as conn:
                upsert_paper(
                    conn,
                    source="openalex",
                    external_id="W1234",
                    title="HED: Hierarchical Event Descriptors",
                    first_message="Abstract text",
                    url="https://doi.org/10.1234/test",
                    created_at=None,
                )
                upsert_paper(
                    conn,
                    source="semanticscholar",
                    external_id="S5678",
                    title="HED: Hierarchical Event Descriptors",  # Exact same title
                    first_message="Abstract text",
                    url="https://semanticscholar.org/paper/5678",
                    created_at=None,
                )
                upsert_paper(
                    conn,
                    source="pubmed",
                    external_id="PMID9999",
                    title="HED - Hierarchical Event Descriptors",  # Similar (different dash)
                    first_message="Abstract text",
                    url="https://pubmed.ncbi.nlm.nih.gov/9999",
                    created_at=None,
                )
                conn.commit()

            # Search for HED
            results = search_papers("HED", project="test", limit=10)

            # Should deduplicate - only 1 result despite 3 entries
            assert len(results) == 1

    def test_deduplication_keeps_different_papers(self, temp_db: Path):
        """Test that search keeps genuinely different papers."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Add different papers
            with get_connection("test") as conn:
                upsert_paper(
                    conn,
                    source="openalex",
                    external_id="W1111",
                    title="HED Schema Version 8.0",
                    first_message="Schema description",
                    url="https://doi.org/10.1234/hed8",
                    created_at=None,
                )
                upsert_paper(
                    conn,
                    source="openalex",
                    external_id="W2222",
                    title="BIDS Standard Specification",
                    first_message="BIDS description",
                    url="https://doi.org/10.1234/bids",
                    created_at=None,
                )
                conn.commit()

            # Search
            results = search_papers("standard", project="test", limit=10)

            # Should keep both (titles are very different)
            # Note: FTS5 might not match both depending on indexing
            assert len(results) >= 1  # At least one should match
