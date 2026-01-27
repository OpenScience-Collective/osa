"""Tests for FAQ summarization from mailing list threads.

Tests are tool-centered, not community-specific. They validate the
faq_summarizer module works correctly for any mailing list data.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.db import get_connection, init_db, upsert_mailing_list_message
from src.knowledge.faq_summarizer import (
    _build_thread_context,
    _score_thread_quality,
    _summarize_thread,
    estimate_summarization_cost,
)


@pytest.fixture
def populated_mailman_db(tmp_path: Path):
    """Create a test database with mailing list messages."""
    db_path = tmp_path / "knowledge" / "test-faq.db"

    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db("test-faq")

        with get_connection("test-faq") as conn:
            # Add messages forming a thread
            for i in range(3):
                upsert_mailing_list_message(
                    conn,
                    list_name="test-list",
                    message_id=f"msg{i:03d}",
                    thread_id="thread001",  # Same thread
                    subject=f"Test subject - message {i}",
                    author=f"Author {i}",
                    author_email=f"author{i}@example.com",
                    date=f"2026-01-{i + 1:02d}T10:00:00Z",
                    body=f"This is the body of message {i}.\nIt has multiple lines.",
                    in_reply_to=f"msg{i - 1:03d}" if i > 0 else None,
                    url=f"https://example.com/list/2026/msg{i:03d}.html",
                    year=2026,
                )

            # Add a single-message thread (should be filtered out)
            upsert_mailing_list_message(
                conn,
                list_name="test-list",
                message_id="single001",
                thread_id="thread002",
                subject="Single message thread",
                author="Solo Author",
                author_email="solo@example.com",
                date="2026-01-10T10:00:00Z",
                body="This thread has only one message.",
                in_reply_to=None,
                url="https://example.com/list/2026/single001.html",
                year=2026,
            )

            conn.commit()

        yield db_path


class TestBuildThreadContext:
    """Tests for thread context building."""

    def test_build_context_formats_correctly(self):
        """Test that thread context is formatted correctly."""
        messages = [
            {
                "author": "Alice",
                "date": "2026-01-01",
                "subject": "Question about feature",
                "body": "How do I use this feature?",
            },
            {
                "author": "Bob",
                "date": "2026-01-02",
                "subject": "Re: Question about feature",
                "body": "You can use it by following these steps...",
            },
        ]

        context = _build_thread_context(messages)

        assert "--- Message 1 ---" in context
        assert "--- Message 2 ---" in context
        assert "From: Alice" in context
        assert "From: Bob" in context
        assert "How do I use this feature?" in context
        assert "You can use it by following these steps" in context

    def test_build_context_truncates_long_messages(self):
        """Test that very long messages are truncated."""
        messages = [
            {
                "author": "Alice",
                "date": "2026-01-01",
                "subject": "Long message",
                "body": "A" * 3000,  # 3000 character message
            }
        ]

        context = _build_thread_context(messages)

        # Should be truncated to 2000 chars + truncation message
        assert len(messages[0]["body"]) == 3000
        assert "... truncated ...]" in context

    def test_build_context_handles_none_values(self):
        """Test that None values are handled gracefully."""
        messages = [
            {
                "author": None,
                "date": "2026-01-01",
                "subject": "Test",
                "body": None,
            }
        ]

        context = _build_thread_context(messages)

        assert "From: Unknown" in context
        assert "--- Message 1 ---" in context


class TestScoreThreadQuality:
    """Tests for thread quality scoring."""

    def test_score_returns_valid_range(self):
        """Test that scoring returns a value between 0.0 and 1.0."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "0.75"
        mock_model.invoke.return_value = mock_response

        score = _score_thread_quality("Test thread context", mock_model)

        assert 0.0 <= score <= 1.0
        assert score == 0.75

    def test_score_extracts_float_from_text(self):
        """Test that scoring extracts float even from verbose responses."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "The quality score for this thread is 0.82 out of 1.0"
        mock_model.invoke.return_value = mock_response

        score = _score_thread_quality("Test thread context", mock_model)

        assert score == 0.82

    def test_score_clamps_to_valid_range(self):
        """Test that scores outside 0-1 are clamped."""
        mock_model = MagicMock()
        mock_response = MagicMock()

        # Test upper bound
        mock_response.content = "1.5"
        mock_model.invoke.return_value = mock_response
        score = _score_thread_quality("Test thread context", mock_model)
        assert score == 1.0

        # Test lower bound (regex extracts "5" from "1.5" on second call)
        # Note: The regex doesn't capture negative signs, so "-0.5" would extract as "0.5"
        # We test that very high values are clamped
        mock_response.content = "2.5"
        score = _score_thread_quality("Test thread context", mock_model)
        assert score == 1.0

    def test_score_handles_non_numeric_response(self):
        """Test that non-numeric responses return None."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "I cannot determine a score"
        mock_model.invoke.return_value = mock_response

        score = _score_thread_quality("Test thread context", mock_model)

        assert score is None

    def test_score_handles_llm_errors(self):
        """Test that unexpected LLM errors are raised."""
        mock_model = MagicMock()
        mock_model.invoke.side_effect = Exception("API timeout")

        # Unexpected errors should be raised
        with pytest.raises(Exception, match="API timeout"):
            _score_thread_quality("Test thread context", mock_model)


class TestSummarizeThread:
    """Tests for thread summarization."""

    def test_summarize_parses_json_response(self):
        """Test that summarization parses JSON correctly."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """
        {
          "question": "How do I import data?",
          "answer": "You can import data using the File menu.",
          "tags": ["data-import", "beginner"],
          "category": "how-to"
        }
        """
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert summary.question == "How do I import data?"
        assert summary.answer == "You can import data using the File menu."
        assert summary.tags == ["data-import", "beginner"]
        assert summary.category == "how-to"

    def test_summarize_handles_markdown_code_blocks(self):
        """Test that markdown code blocks are stripped."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """```json
        {
          "question": "Test question?",
          "answer": "Test answer.",
          "tags": ["test"],
          "category": "discussion"
        }
        ```"""
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert summary.question == "Test question?"

    def test_summarize_handles_missing_optional_fields(self):
        """Test that missing tags/category use defaults."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """
        {
          "question": "Test?",
          "answer": "Answer."
        }
        """
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert summary.tags == []
        assert summary.category == "discussion"

    def test_summarize_handles_invalid_json(self):
        """Test that invalid JSON returns None."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "This is not JSON at all!"
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is None

    def test_summarize_handles_llm_errors(self):
        """Test that unexpected LLM errors are raised."""
        mock_model = MagicMock()
        mock_model.invoke.side_effect = Exception("API error")

        # Unexpected errors should be raised
        with pytest.raises(Exception, match="API error"):
            _summarize_thread("Test thread context", mock_model)


class TestEstimateSummarizationCost:
    """Tests for cost estimation."""

    def test_estimate_returns_structure(self, populated_mailman_db: Path):
        """Test that cost estimation returns expected structure."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            estimate = estimate_summarization_cost("test-list", project="test-faq")

            assert "thread_count" in estimate
            assert "estimated_input_tokens" in estimate
            assert "estimated_output_tokens" in estimate
            assert "haiku_cost" in estimate
            assert "sonnet_cost" in estimate
            assert "hybrid_cost" in estimate
            assert "recommended" in estimate

    def test_estimate_counts_threads_correctly(self, populated_mailman_db: Path):
        """Test that cost estimation counts threads correctly."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            estimate = estimate_summarization_cost("test-list", project="test-faq")

            # Should have 1 thread with >=2 messages (thread001)
            # thread002 has only 1 message and should be filtered
            assert estimate["thread_count"] == 1

    def test_estimate_handles_empty_database(self, tmp_path: Path):
        """Test cost estimation with no threads."""
        db_path = tmp_path / "knowledge" / "empty.db"

        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("empty")
            estimate = estimate_summarization_cost("test-list", project="empty")

            assert estimate["thread_count"] == 0
            assert estimate["haiku_cost"] == 0.0
            assert estimate["sonnet_cost"] == 0.0
            assert estimate["recommended"] == "none"

    def test_estimate_calculates_costs(self, populated_mailman_db: Path):
        """Test that costs are calculated as positive numbers."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            estimate = estimate_summarization_cost("test-list", project="test-faq")

            # All costs should be positive for non-zero threads
            if estimate["thread_count"] > 0:
                assert estimate["haiku_cost"] > 0
                assert estimate["sonnet_cost"] > 0
                assert estimate["hybrid_cost"] > 0

    def test_estimate_recommends_strategy(self, populated_mailman_db: Path):
        """Test that recommendation is based on thread count."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            # Small thread count
            estimate = estimate_summarization_cost("test-list", project="test-faq")
            assert estimate["recommended"] in ["haiku", "hybrid", "none"]
