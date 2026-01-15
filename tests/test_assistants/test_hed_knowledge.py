"""Tests for HED knowledge discovery tools.

Tests cover:
- Database initialization checks
- Result formatting
- No results handling
"""

from pathlib import Path
from unittest.mock import patch

from src.knowledge.db import get_connection, init_db, upsert_github_item, upsert_paper


class TestSearchHedDiscussions:
    """Tests for search_hed_discussions tool."""

    def test_returns_error_when_db_not_exists(self, tmp_path: Path) -> None:
        """Should return initialization message when DB doesn't exist."""
        # Import inside test to avoid module-level issues
        from src.assistants.hed.knowledge import search_hed_discussions

        nonexistent_path = tmp_path / "nonexistent.db"
        with patch("src.assistants.hed.knowledge.get_db_path", return_value=nonexistent_path):
            result = search_hed_discussions.invoke({"query": "validation"})
            assert "not initialized" in result
            assert "osa sync init" in result

    def test_returns_no_results_message(self, tmp_path: Path) -> None:
        """Should return 'no results' message for non-matching query."""
        from src.assistants.hed.knowledge import search_hed_discussions

        db_path = tmp_path / "knowledge" / "hed.db"
        with (
            patch("src.knowledge.db.get_db_path", return_value=db_path),
            patch("src.assistants.hed.knowledge.get_db_path", return_value=db_path),
        ):
            init_db("hed")  # Create empty DB
            result = search_hed_discussions.invoke({"query": "xyznonexistent123"})
            assert "No related discussions found" in result

    def test_formats_results_correctly(self, tmp_path: Path) -> None:
        """Should format GitHub items with type, status, and URL."""
        from src.assistants.hed.knowledge import search_hed_discussions

        db_path = tmp_path / "knowledge" / "hed.db"
        with (
            patch("src.knowledge.db.get_db_path", return_value=db_path),
            patch("src.assistants.hed.knowledge.get_db_path", return_value=db_path),
        ):
            init_db("hed")

            with get_connection("hed") as conn:
                upsert_github_item(
                    conn,
                    repo="hed-standard/hed-specification",
                    item_type="issue",
                    number=1,
                    title="Validation error with nested groups",
                    first_message="I'm getting a validation error.",
                    status="open",
                    url="https://github.com/hed-standard/hed-specification/issues/1",
                    created_at="2024-01-01T00:00:00Z",
                )
                conn.commit()

            result = search_hed_discussions.invoke({"query": "validation"})
            assert "[Issue]" in result
            assert "(open)" in result
            assert "https://github.com" in result
            assert "Validation error" in result


class TestSearchHedPapers:
    """Tests for search_hed_papers tool."""

    def test_returns_error_when_db_not_exists(self, tmp_path: Path) -> None:
        """Should return initialization message when DB doesn't exist."""
        from src.assistants.hed.knowledge import search_hed_papers

        nonexistent_path = tmp_path / "nonexistent.db"
        with patch("src.assistants.hed.knowledge.get_db_path", return_value=nonexistent_path):
            result = search_hed_papers.invoke({"query": "HED annotation"})
            assert "not initialized" in result
            assert "osa sync papers" in result

    def test_returns_no_results_message(self, tmp_path: Path) -> None:
        """Should return 'no results' message for non-matching query."""
        from src.assistants.hed.knowledge import search_hed_papers

        db_path = tmp_path / "knowledge" / "hed.db"
        with (
            patch("src.knowledge.db.get_db_path", return_value=db_path),
            patch("src.assistants.hed.knowledge.get_db_path", return_value=db_path),
        ):
            init_db("hed")  # Create empty DB
            result = search_hed_papers.invoke({"query": "xyznonexistent123"})
            assert "No related papers found" in result

    def test_formats_results_correctly(self, tmp_path: Path) -> None:
        """Should format papers with title, source, and URL."""
        from src.assistants.hed.knowledge import search_hed_papers

        db_path = tmp_path / "knowledge" / "hed.db"
        with (
            patch("src.knowledge.db.get_db_path", return_value=db_path),
            patch("src.assistants.hed.knowledge.get_db_path", return_value=db_path),
        ):
            init_db("hed")

            with get_connection("hed") as conn:
                upsert_paper(
                    conn,
                    source="openalex",
                    external_id="W12345",
                    title="HED Annotation Framework",
                    first_message="This paper describes the HED framework.",
                    url="https://doi.org/10.1234/hed",
                    created_at="2024-01-01",
                )
                conn.commit()

            result = search_hed_papers.invoke({"query": "HED"})
            assert "HED Annotation Framework" in result
            assert "[openalex]" in result
            assert "https://doi.org" in result
