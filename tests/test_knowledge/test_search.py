"""Tests for the knowledge search module.

These tests use a temporary database populated with test data.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import get_connection, init_db, upsert_github_item, upsert_paper
from src.knowledge.search import (
    SearchResult,
    _sanitize_fts5_query,
    search_all,
    search_github_items,
    search_papers,
)


@pytest.fixture
def populated_db(tmp_path: Path):
    """Create a populated test database."""
    db_path = tmp_path / "knowledge" / "test.db"

    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()

        with get_connection() as conn:
            # Add GitHub issues
            upsert_github_item(
                conn,
                repo="hed-standard/hed-specification",
                item_type="issue",
                number=1,
                title="Validation error with nested groups",
                first_message="I'm getting a validation error when using nested parentheses.",
                status="open",
                url="https://github.com/hed-standard/hed-specification/issues/1",
                created_at="2024-01-01T00:00:00Z",
            )
            upsert_github_item(
                conn,
                repo="hed-standard/hed-specification",
                item_type="issue",
                number=2,
                title="Library schema question",
                first_message="How do I use library schemas with my annotations?",
                status="closed",
                url="https://github.com/hed-standard/hed-specification/issues/2",
                created_at="2024-01-02T00:00:00Z",
            )
            upsert_github_item(
                conn,
                repo="hed-standard/hed-schemas",
                item_type="pr",
                number=10,
                title="Add new sensory tags",
                first_message="This PR adds new tags for sensory events.",
                status="open",
                url="https://github.com/hed-standard/hed-schemas/pull/10",
                created_at="2024-01-03T00:00:00Z",
            )

            # Add papers
            upsert_paper(
                conn,
                source="openalex",
                external_id="W1",
                title="HED Annotation Best Practices",
                first_message="This paper describes best practices for HED annotation in neuroimaging.",
                url="https://doi.org/10.1234/hed1",
                created_at="2023",
            )
            upsert_paper(
                conn,
                source="semanticscholar",
                external_id="S1",
                title="BIDS and HED Integration",
                first_message="We present a framework for integrating BIDS with HED annotations.",
                url="https://doi.org/10.1234/bids-hed",
                created_at="2024",
            )

            conn.commit()

        yield db_path


class TestSearchGithubItems:
    """Tests for GitHub item search."""

    def test_search_finds_matching_issues(self, populated_db: Path):
        """Test that search finds issues matching the query."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            results = search_github_items("validation error")

            assert len(results) >= 1
            assert any("validation" in r.title.lower() for r in results)

    def test_search_returns_search_result_objects(self, populated_db: Path):
        """Test that search returns SearchResult objects."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            results = search_github_items("validation")

            assert len(results) > 0
            assert all(isinstance(r, SearchResult) for r in results)
            assert all(r.source == "github" for r in results)

    def test_filter_by_item_type(self, populated_db: Path):
        """Test filtering by issue or PR."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            issues = search_github_items("schema", item_type="issue")
            prs = search_github_items("tags", item_type="pr")

            assert all(r.item_type == "issue" for r in issues)
            assert all(r.item_type == "pr" for r in prs)

    def test_filter_by_status(self, populated_db: Path):
        """Test filtering by open/closed status."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            # All our test issues - find open ones
            open_results = search_github_items("validation OR schema OR tags", status="open")

            assert all(r.status == "open" for r in open_results)

    def test_limit_results(self, populated_db: Path):
        """Test that limit parameter works."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            # Search with a broad query that matches multiple items
            results = search_github_items("HED OR validation OR schema OR tags", limit=1)

            assert len(results) <= 1

    def test_empty_results_for_no_match(self, populated_db: Path):
        """Test that search returns empty list for non-matching query."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            results = search_github_items("xyznonexistent123")

            assert len(results) == 0


class TestSearchPapers:
    """Tests for paper search."""

    def test_search_finds_matching_papers(self, populated_db: Path):
        """Test that search finds papers matching the query."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            results = search_papers("HED annotation")

            assert len(results) >= 1
            assert any("hed" in r.title.lower() for r in results)

    def test_search_returns_search_result_objects(self, populated_db: Path):
        """Test that search returns SearchResult objects."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            results = search_papers("annotation")

            assert len(results) > 0
            assert all(isinstance(r, SearchResult) for r in results)
            assert all(r.status == "published" for r in results)

    def test_filter_by_source(self, populated_db: Path):
        """Test filtering by paper source."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            openalex_results = search_papers("annotation OR BIDS", source="openalex")
            s2_results = search_papers("annotation OR BIDS", source="semanticscholar")

            assert all(r.source == "openalex" for r in openalex_results)
            assert all(r.source == "semanticscholar" for r in s2_results)


class TestSearchAll:
    """Tests for combined search."""

    def test_search_all_returns_both_categories(self, populated_db: Path):
        """Test that search_all returns both github and paper results."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            results = search_all("HED")

            assert "github" in results
            assert "papers" in results
            assert isinstance(results["github"], list)
            assert isinstance(results["papers"], list)


class TestFTS5Sanitization:
    """Tests for FTS5 query sanitization to prevent injection."""

    def test_sanitize_basic_query(self):
        """Test that basic queries are wrapped in quotes."""
        result = _sanitize_fts5_query("validation error")
        assert result == '"validation error"'

    def test_sanitize_escapes_quotes(self):
        """Test that double quotes in user input are escaped."""
        result = _sanitize_fts5_query('say "hello" world')
        assert result == '"say ""hello"" world"'

    def test_sanitize_fts5_operators(self):
        """Test that FTS5 operators are treated as literal text."""
        # These would be dangerous without sanitization
        dangerous_queries = [
            "test AND DROP TABLE",
            "test OR 1=1",
            "test NOT secure",
            "test NEAR malicious",
            "test*",  # Wildcard
        ]
        for query in dangerous_queries:
            result = _sanitize_fts5_query(query)
            # Should be wrapped in quotes, treating operators as literals
            assert result.startswith('"')
            assert result.endswith('"')

    def test_search_handles_special_characters(self, populated_db: Path):
        """Test that search doesn't crash with special FTS5 characters."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_db):
            # These should not crash, even if they return no results
            dangerous_inputs = [
                "validation AND DROP",
                "test OR 1=1",
                'test" OR "1"="1',
                "validation*",
                "(test)",
                "test:value",
            ]
            for query in dangerous_inputs:
                # Should not raise an exception
                results = search_github_items(query)
                assert isinstance(results, list)
                results = search_papers(query)
                assert isinstance(results, list)
