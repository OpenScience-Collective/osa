"""End-to-end integration tests for mailing list FAQ system.

Tests the full pipeline: scrape → store → search → summarize.
Uses real database operations with test data.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.db import get_connection, init_db
from src.knowledge.faq_summarizer import summarize_threads
from src.knowledge.mailman_sync import sync_mailing_list_year
from src.knowledge.search import search_faq_entries

# Mock HTML for full pipeline testing
MOCK_THREAD_HTML = """
<html><body><ul>
<LI><A HREF="000001.html">Question about setup</A>
<LI><A HREF="000002.html">Re: Question about setup</A>
<LI><A HREF="000003.html">Re: Question about setup</A>
</ul></body></html>
"""

MOCK_MSG_1_HTML = """
<html>
<head><title>[Test-List] Question about setup</title></head>
<body>
<B>Alice Johnson</B> <a href="mailto:alice@example.com">alice at example.com</a><br>
<I>Mon Jan 27 10:00:00 PST 2026</I>
<PRE>
Hi everyone,

How do I set up this software? I'm having trouble with the installation.

Thanks!
Alice
</PRE>
</body>
</html>
"""

MOCK_MSG_2_HTML = """
<html>
<head><title>[Test-List] Re: Question about setup</title></head>
<body>
<B>Bob Smith</B> <a href="mailto:bob@example.com">bob at example.com</a><br>
<I>Mon Jan 27 11:00:00 PST 2026</I>
<PRE>
Hi Alice,

You need to run the install script first:
  ./install.sh

Then configure your settings.

Hope this helps!
Bob
</PRE>
</body>
</html>
"""

MOCK_MSG_3_HTML = """
<html>
<head><title>[Test-List] Re: Question about setup</title></head>
<body>
<B>Alice Johnson</B> <a href="mailto:alice@example.com">alice at example.com</a><br>
<I>Mon Jan 27 12:00:00 PST 2026</I>
<PRE>
Thanks Bob! That worked perfectly.

Alice
</PRE>
</body>
</html>
"""


@pytest.fixture
def e2e_test_db(tmp_path: Path):
    """Create a fresh database for end-to-end testing."""
    db_path = tmp_path / "knowledge" / "test-e2e.db"

    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db("test-e2e")
        yield db_path


class TestMailmanEndToEnd:
    """End-to-end tests for the complete mailing list FAQ pipeline."""

    def test_full_pipeline_scrape_to_search(self, e2e_test_db: Path):
        """Test the complete pipeline from scraping to searching."""

        def mock_fetch(url: str, **kwargs):  # noqa: ARG001
            """Mock fetch that returns appropriate HTML."""
            if "thread.html" in url:
                return MOCK_THREAD_HTML
            elif "000001.html" in url:
                return MOCK_MSG_1_HTML
            elif "000002.html" in url:
                return MOCK_MSG_2_HTML
            elif "000003.html" in url:
                return MOCK_MSG_3_HTML
            return None

        with (
            patch("src.knowledge.db.get_db_path", return_value=e2e_test_db),
            patch("src.knowledge.mailman_sync._fetch_page", side_effect=mock_fetch),
        ):
            # Step 1: Scrape mailing list
            sync_count = sync_mailing_list_year(
                list_name="test-list",
                base_url="https://example.com/pipermail/test-list/",
                year=2026,
                project="test-e2e",
            )

            assert sync_count == 3

            # Step 2: Verify messages in database
            with get_connection("test-e2e") as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM mailing_list_messages")
                msg_count = cursor.fetchone()[0]
                assert msg_count == 3

                # Check message content
                cursor = conn.execute(
                    """
                    SELECT subject, author, body
                    FROM mailing_list_messages
                    WHERE message_id = '000001'
                    """
                )
                row = cursor.fetchone()
                assert "Question about setup" in row["subject"]
                assert row["author"] == "Alice Johnson"
                assert "trouble with the installation" in row["body"]

    def test_full_pipeline_with_summarization(self, e2e_test_db: Path):
        """Test the complete pipeline including FAQ summarization."""

        def mock_fetch(url: str, **kwargs):  # noqa: ARG001
            if "thread.html" in url:
                return MOCK_THREAD_HTML
            elif "000001.html" in url:
                return MOCK_MSG_1_HTML
            elif "000002.html" in url:
                return MOCK_MSG_2_HTML
            elif "000003.html" in url:
                return MOCK_MSG_3_HTML
            return None

        # Mock LLM responses
        mock_scoring_model = MagicMock()
        mock_scoring_response = MagicMock()
        mock_scoring_response.content = "0.85"
        mock_scoring_model.invoke.return_value = mock_scoring_response

        mock_summary_model = MagicMock()
        mock_summary_response = MagicMock()
        mock_summary_response.content = """
        {
          "question": "How do I set up the software?",
          "answer": "Run the install script (./install.sh) and then configure your settings.",
          "tags": ["installation", "setup", "beginner"],
          "category": "how-to"
        }
        """
        mock_summary_model.invoke.return_value = mock_summary_response

        with (
            patch("src.knowledge.db.get_db_path", return_value=e2e_test_db),
            patch("src.knowledge.mailman_sync._fetch_page", side_effect=mock_fetch),
            patch(
                "src.knowledge.faq_summarizer.create_openrouter_llm",
                side_effect=[mock_scoring_model, mock_summary_model],
            ),
        ):
            # Step 1: Scrape messages
            sync_mailing_list_year(
                list_name="test-list",
                base_url="https://example.com/pipermail/test-list/",
                year=2026,
                project="test-e2e",
            )

            # Step 2: Verify thread_id was automatically assigned
            with get_connection("test-e2e") as conn:
                cursor = conn.execute(
                    """
                    SELECT message_id, thread_id, subject
                    FROM mailing_list_messages
                    WHERE list_name = 'test-list'
                    ORDER BY date
                    """
                )
                messages_check = cursor.fetchall()
                # All messages should have same normalized subject "Question about setup"
                # So they should all get the same thread_id (the first message's ID)
                thread_ids = {msg["thread_id"] for msg in messages_check}
                assert len(thread_ids) == 1, f"Expected 1 thread, got {len(thread_ids)}"
                assert all(msg["thread_id"] is not None for msg in messages_check), (
                    "All messages should have thread_id set"
                )

            # Step 3: Summarize threads
            result = summarize_threads(
                list_name="test-list",
                project="test-e2e",
                quality_threshold=0.5,
                max_threads=10,
            )

            assert result["summarized"] == 1

            # Step 4: Verify FAQ entry
            with get_connection("test-e2e") as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM faq_entries")
                faq_count = cursor.fetchone()[0]
                assert faq_count == 1

                cursor = conn.execute(
                    """
                    SELECT question, answer, tags, category, quality_score
                    FROM faq_entries
                    """
                )
                row = cursor.fetchone()
                assert "How do I set up the software?" in row["question"]
                assert "install script" in row["answer"]
                assert row["quality_score"] == 0.85
                assert row["category"] == "how-to"

            # Step 5: Search FAQ entries
            faq_results = search_faq_entries(
                query="installation setup",
                project="test-e2e",
                limit=5,
            )

            assert len(faq_results) >= 1
            assert any("set up" in r.question.lower() for r in faq_results)

    def test_pipeline_handles_no_threads(self, e2e_test_db: Path):
        """Test pipeline gracefully handles database with no threads."""

        def mock_fetch(url: str, **kwargs):  # noqa: ARG001
            # Return empty thread index
            if "thread.html" in url:
                return "<html><body><ul></ul></body></html>"
            return None

        with (
            patch("src.knowledge.db.get_db_path", return_value=e2e_test_db),
            patch("src.knowledge.mailman_sync._fetch_page", side_effect=mock_fetch),
        ):
            # Scrape empty list
            sync_count = sync_mailing_list_year(
                list_name="test-list",
                base_url="https://example.com/pipermail/test-list/",
                year=2026,
                project="test-e2e",
            )

            assert sync_count == 0

            # Try to search (should return empty)
            results = search_faq_entries(
                query="anything",
                project="test-e2e",
                limit=5,
            )

            assert results == []

    def test_pipeline_fts5_search_works(self, e2e_test_db: Path):
        """Test that FTS5 full-text search works correctly."""

        def mock_fetch(url: str, **kwargs):  # noqa: ARG001
            if "thread.html" in url:
                return MOCK_THREAD_HTML
            elif "000001.html" in url:
                return MOCK_MSG_1_HTML
            elif "000002.html" in url:
                return MOCK_MSG_2_HTML
            elif "000003.html" in url:
                return MOCK_MSG_3_HTML
            return None

        mock_scoring_model = MagicMock()
        mock_scoring_response = MagicMock()
        mock_scoring_response.content = "0.9"
        mock_scoring_model.invoke.return_value = mock_scoring_response

        mock_summary_model = MagicMock()
        mock_summary_response = MagicMock()
        mock_summary_response.content = """
        {
          "question": "How to install?",
          "answer": "Use the installation script.",
          "tags": ["installation"],
          "category": "how-to"
        }
        """
        mock_summary_model.invoke.return_value = mock_summary_response

        with (
            patch("src.knowledge.db.get_db_path", return_value=e2e_test_db),
            patch("src.knowledge.mailman_sync._fetch_page", side_effect=mock_fetch),
            patch(
                "src.knowledge.faq_summarizer.create_openrouter_llm",
                side_effect=[mock_scoring_model, mock_summary_model],
            ),
        ):
            # Scrape (thread_id will be automatically assigned)
            sync_mailing_list_year(
                list_name="test-list",
                base_url="https://example.com/pipermail/test-list/",
                year=2026,
                project="test-e2e",
            )

            # Summarize
            summarize_threads(
                list_name="test-list",
                project="test-e2e",
                quality_threshold=0.5,
                max_threads=10,
            )

            # Test different search queries
            results_install = search_faq_entries("install", project="test-e2e")
            assert len(results_install) >= 1

            results_setup = search_faq_entries("installation", project="test-e2e")
            assert len(results_setup) >= 1

            # Verify same FAQ returned for related terms
            if results_install and results_setup:
                assert results_install[0].question == results_setup[0].question
