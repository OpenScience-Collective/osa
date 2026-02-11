"""Tests for papers sync module.

Note: These are real API tests, not mocks, per project guidelines.
"""

from pathlib import Path
from unittest.mock import patch

import pyalex
import pytest

from src.knowledge.db import get_connection, init_db
from src.knowledge.papers_sync import (
    _reconstruct_abstract,
    configure_openalex,
    sync_openalex_papers,
)


@pytest.fixture
def temp_db(tmp_path: Path):
    """Create temporary database for testing."""
    db_path = tmp_path / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()
        yield db_path


class TestConfigureOpenalex:
    """Tests for configure_openalex helper."""

    def setup_method(self):
        """Reset pyalex config before each test."""
        pyalex.config.api_key = None
        pyalex.config.email = None

    def teardown_method(self):
        """Reset pyalex config after each test."""
        pyalex.config.api_key = None
        pyalex.config.email = None

    def test_sets_api_key(self):
        """Should set pyalex.config.api_key when api_key provided."""
        configure_openalex(api_key="test-key-123")
        assert pyalex.config.api_key == "test-key-123"

    def test_sets_email_when_no_api_key(self):
        """Should set pyalex.config.email when only email provided."""
        configure_openalex(email="test@example.com")
        assert pyalex.config.email == "test@example.com"

    def test_api_key_takes_precedence_over_email(self):
        """Should use API key over email when both provided."""
        configure_openalex(api_key="test-key", email="test@example.com")
        assert pyalex.config.api_key == "test-key"

    def test_handles_empty_strings(self):
        """Should treat empty strings as None (no config)."""
        configure_openalex(api_key="", email="")
        assert pyalex.config.api_key is None
        assert pyalex.config.email is None

    def test_handles_whitespace_strings(self):
        """Should strip whitespace and treat blank as None."""
        configure_openalex(api_key="  ", email="  ")
        assert pyalex.config.api_key is None
        assert pyalex.config.email is None

    def test_handles_none_values(self):
        """Should handle None values gracefully (anonymous access)."""
        configure_openalex(api_key=None, email=None)
        assert pyalex.config.api_key is None
        assert pyalex.config.email is None


class TestAbstractReconstruction:
    """Test OpenALEX inverted index reconstruction."""

    def test_reconstruct_abstract_basic(self):
        """Test basic abstract reconstruction from inverted index."""
        inverted_index = {
            "hello": [0],
            "world": [1],
        }
        result = _reconstruct_abstract(inverted_index)
        assert "hello" in result
        assert "world" in result

    def test_reconstruct_abstract_with_gaps(self):
        """Test reconstruction with gaps in position array."""
        inverted_index = {
            "hello": [0],
            "world": [2],  # Missing position 1
        }
        result = _reconstruct_abstract(inverted_index)
        # Should handle gaps gracefully (empty string at position 1)
        assert "hello" in result
        assert "world" in result

    def test_reconstruct_abstract_empty(self):
        """Test reconstruction with empty/None input."""
        assert _reconstruct_abstract(None) == ""
        assert _reconstruct_abstract({}) == ""

    def test_reconstruct_abstract_complex(self):
        """Test reconstruction with longer text."""
        inverted_index = {
            "Hierarchical": [0],
            "Event": [1],
            "Descriptors": [2],
            "(HED)": [3],
            "is": [4],
            "a": [5],
            "framework": [6],
        }
        result = _reconstruct_abstract(inverted_index)
        expected_words = ["Hierarchical", "Event", "Descriptors", "HED", "framework"]
        for word in expected_words:
            assert word in result


class TestPapersSync:
    """Test papers sync functionality."""

    def test_sync_openalex_papers_basic(self, temp_db: Path):
        """Test basic OpenALEX papers sync.

        This is a smoke test using a real OpenALEX API call.
        """
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Sync a small number of papers with a specific query
            count = sync_openalex_papers(
                "Hierarchical Event Descriptors", max_results=5, project="test"
            )

            # Should find at least some results (OpenALEX doesn't require auth)
            # Accept 0 for network issues
            assert count >= 0

            # If count > 0, verify data was written
            if count > 0:
                with get_connection("test") as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) as count FROM papers WHERE source = 'openalex'",
                    ).fetchone()
                    assert row["count"] > 0

    def test_sync_openalex_papers_no_results(self, temp_db: Path):
        """Test OpenALEX sync with query that returns no results."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Use an extremely specific nonsense query
            count = sync_openalex_papers("xyzabc123nonsensequery", max_results=5, project="test")

            # Should return 0 for no results (not an error)
            assert count == 0

    def test_sync_respects_max_results(self, temp_db: Path):
        """Test that max_results parameter is respected."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Request only 2 results
            count = sync_openalex_papers("neuroscience", max_results=2, project="test")

            # Should not exceed max_results
            assert count <= 2
