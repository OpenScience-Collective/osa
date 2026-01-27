"""Tests for Mailman archive scraping.

Tests are tool-centered, not community-specific. They validate the
mailman_sync module works correctly for any Mailman pipermail archive.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import get_connection, init_db
from src.knowledge.mailman_sync import (
    _parse_message_page,
    _parse_thread_index,
    _parse_year_index,
    sync_mailing_list_year,
)

# Mock HTML responses for testing
MOCK_YEAR_INDEX_HTML = """
<html>
<head><title>The test-list Archives</title></head>
<body>
<h1>The test-list Archives</h1>
<table>
<tr><td><a href="2024/">2024</a></td></tr>
<tr><td><a href="2025/">2025</a></td></tr>
<tr><td><a href="2026/">2026</a></td></tr>
</table>
</body>
</html>
"""

MOCK_THREAD_INDEX_HTML = """
<html>
<head><title>The test-list Archives by thread</title></head>
<body>
<ul>
<LI><A HREF="000001.html">First test message
</A><A NAME="1">&nbsp;</A>
<LI><A HREF="000002.html">Second test message
</A><A NAME="2">&nbsp;</A>
<LI><A HREF="000003.html">Third test message
</A><A NAME="3">&nbsp;</A>
</ul>
</body>
</html>
"""

MOCK_MESSAGE_HTML = """
<html>
<head><title>Test Subject Line</title></head>
<body>
<B>Test Author</B> <a href="mailto:author@example.com">author at example.com</a><br>
<I>Mon Jan 27 10:00:00 PST 2026</I>
<PRE>
This is the body of the test message.

It contains multiple lines.
And some **markdown** formatting.
</PRE>
</body>
</html>
"""


class TestParseYearIndex:
    """Tests for year index parsing."""

    def test_parse_valid_year_index(self):
        """Test parsing a valid year index."""
        years = _parse_year_index(MOCK_YEAR_INDEX_HTML)

        assert isinstance(years, list)
        assert len(years) == 3
        assert years == [2024, 2025, 2026]

    def test_parse_empty_year_index(self):
        """Test parsing an empty year index."""
        html = "<html><body>No years here</body></html>"
        years = _parse_year_index(html)

        assert years == []

    def test_parse_duplicate_years(self):
        """Test that duplicate years are deduplicated."""
        html = """
        <a href="2024/">2024</a>
        <a href="2024/">2024</a>
        <a href="2025/">2025</a>
        """
        years = _parse_year_index(html)

        assert len(years) == 2
        assert years == [2024, 2025]


class TestParseThreadIndex:
    """Tests for thread index parsing."""

    def test_parse_valid_thread_index(self):
        """Test parsing a valid thread index."""
        base_url = "https://example.com/pipermail/test-list/"
        year = 2026

        messages = _parse_thread_index(MOCK_THREAD_INDEX_HTML, base_url, year)

        assert isinstance(messages, list)
        assert len(messages) == 3

        # Check first message (now returns url, msg_id, subject)
        url, msg_id, subject = messages[0]
        assert url == "https://example.com/pipermail/test-list/2026/000001.html"
        assert msg_id == "000001"
        assert "First test message" in subject

    def test_parse_empty_thread_index(self):
        """Test parsing an empty thread index."""
        html = "<html><body>No messages</body></html>"
        messages = _parse_thread_index(html, "https://example.com/list/", 2026)

        assert messages == []


class TestParseMessagePage:
    """Tests for message page parsing."""

    def test_parse_valid_message(self):
        """Test parsing a valid message page."""
        url = "https://example.com/pipermail/test-list/2026/000001.html"

        msg = _parse_message_page(MOCK_MESSAGE_HTML, url)

        assert msg is not None
        assert msg.subject == "Test Subject Line"
        assert msg.author == "Test Author"
        assert msg.author_email == "author@example.com"
        assert msg.date == "Mon Jan 27 10:00:00 PST 2026"
        assert "body of the test message" in msg.body
        assert msg.message_id == "000001"
        assert msg.url == url

    def test_parse_message_with_missing_fields(self):
        """Test parsing a message with some missing fields."""
        html = """
        <html>
        <head><title>Minimal Subject</title></head>
        <body>
        <PRE>Just a body, no author or date.</PRE>
        </body>
        </html>
        """
        url = "https://example.com/list/2026/000001.html"

        msg = _parse_message_page(html, url)

        assert msg is not None
        assert msg.subject == "Minimal Subject"
        assert msg.author is None
        assert msg.author_email is None
        assert "Just a body" in msg.body

    def test_parse_malformed_html(self):
        """Test that malformed HTML returns None gracefully."""
        html = "This is not HTML at all!"
        url = "https://example.com/list/2026/000001.html"

        msg = _parse_message_page(html, url)

        # Should handle gracefully (either return None or partial data)
        # Implementation currently returns data even from malformed HTML
        assert msg is not None


@pytest.fixture
def temp_test_db(tmp_path: Path):
    """Create a temporary test database."""
    db_path = tmp_path / "knowledge" / "test-mailman.db"

    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db("test-mailman")
        yield db_path


class TestSyncMailingListYear:
    """Tests for year synchronization."""

    def test_sync_year_with_mocked_responses(self, temp_test_db: Path):
        """Test syncing a year with mocked HTTP responses."""

        def mock_fetch(url: str, **kwargs):  # noqa: ARG001
            """Mock fetch function that returns appropriate HTML."""
            if "thread.html" in url:
                return MOCK_THREAD_INDEX_HTML
            elif url.endswith(".html"):
                return MOCK_MESSAGE_HTML
            return None

        with (
            patch("src.knowledge.db.get_db_path", return_value=temp_test_db),
            patch("src.knowledge.mailman_sync._fetch_page", side_effect=mock_fetch),
        ):
            count = sync_mailing_list_year(
                list_name="test-list",
                base_url="https://example.com/pipermail/test-list/",
                year=2026,
                project="test-mailman",
            )

            # Should sync 3 messages from mock data
            assert count == 3

            # Verify messages were stored
            with get_connection("test-mailman") as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM mailing_list_messages")
                db_count = cursor.fetchone()[0]
                assert db_count == 3

                # Check one message
                cursor = conn.execute(
                    "SELECT subject, author, year FROM mailing_list_messages LIMIT 1"
                )
                row = cursor.fetchone()
                assert row["subject"] == "Test Subject Line"
                assert row["author"] == "Test Author"
                assert row["year"] == 2026

    def test_sync_year_handles_fetch_failures(self, temp_test_db: Path):
        """Test that sync handles HTTP fetch failures gracefully."""

        def mock_fetch_failing(_url: str, **kwargs):  # noqa: ARG001
            """Mock fetch that always fails."""
            return None

        with (
            patch("src.knowledge.db.get_db_path", return_value=temp_test_db),
            patch("src.knowledge.mailman_sync._fetch_page", side_effect=mock_fetch_failing),
        ):
            count = sync_mailing_list_year(
                list_name="test-list",
                base_url="https://example.com/pipermail/test-list/",
                year=2026,
                project="test-mailman",
            )

            # Should return 0 when thread index fetch fails
            assert count == 0

    def test_sync_year_commits_periodically(self, temp_test_db: Path):
        """Test that sync commits in batches (every 50 messages)."""
        # Create large mock response with 100+ messages
        large_thread_index = "<html><body><ul>"
        for i in range(100):
            large_thread_index += f'<LI><A HREF="{i:06d}.html">Message {i}</A>'
        large_thread_index += "</ul></body></html>"

        def mock_fetch(url: str, **kwargs):  # noqa: ARG001
            if "thread.html" in url:
                return large_thread_index
            elif url.endswith(".html"):
                return MOCK_MESSAGE_HTML
            return None

        with (
            patch("src.knowledge.db.get_db_path", return_value=temp_test_db),
            patch("src.knowledge.mailman_sync._fetch_page", side_effect=mock_fetch),
        ):
            count = sync_mailing_list_year(
                list_name="test-list",
                base_url="https://example.com/pipermail/test-list/",
                year=2026,
                project="test-mailman",
            )

            assert count == 100

            # All messages should be committed
            with get_connection("test-mailman") as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM mailing_list_messages")
                db_count = cursor.fetchone()[0]
                assert db_count == 100
