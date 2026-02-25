"""Tests for the Discourse forum sync and search.

Tests cover:
- DB schema creation and topic upsert
- FTS5 search on discourse topics
- Config validation (MNE community)
- Live Discourse API fetch (against mne.discourse.group)
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import (
    get_connection,
    init_db,
    upsert_discourse_topic,
)
from src.knowledge.search import DiscourseTopicResult, search_discourse_topics


@pytest.fixture
def temp_db(tmp_path: Path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "knowledge" / "test_discourse.db"

    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()
        yield db_path


class TestDiscourseDbSchema:
    """Tests for Discourse database schema and upsert."""

    def test_discourse_table_exists(self, temp_db: Path):
        """Test that discourse_topics table is created."""
        conn = sqlite3.connect(temp_db)
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        conn.close()
        assert "discourse_topics" in tables
        assert "discourse_topics_fts" in tables

    def test_upsert_discourse_topic(self, temp_db: Path):
        """Test inserting and updating a discourse topic."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db), get_connection() as conn:
            upsert_discourse_topic(
                conn,
                forum_url="https://mne.discourse.group",
                topic_id=123,
                title="How to read EDF files",
                first_post="I want to read EDF files using MNE-Python.",
                accepted_answer="Use mne.io.read_raw_edf().",
                category_name="Support",
                tags=["edf", "io"],
                reply_count=5,
                like_count=3,
                views=100,
                url="https://mne.discourse.group/t/how-to-read-edf-files/123",
                created_at="2024-01-15T10:00:00Z",
                last_posted_at="2024-01-16T14:00:00Z",
            )
            conn.commit()

            # Verify the topic was inserted
            row = conn.execute(
                "SELECT title, first_post, accepted_answer, category_name "
                "FROM discourse_topics WHERE topic_id = 123"
            ).fetchone()
            assert row is not None
            assert row[0] == "How to read EDF files"
            assert row[1] == "I want to read EDF files using MNE-Python."
            assert row[2] == "Use mne.io.read_raw_edf()."
            assert row[3] == "Support"

    def test_upsert_updates_existing(self, temp_db: Path):
        """Test that upsert updates an existing topic."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db), get_connection() as conn:
            # Insert first
            upsert_discourse_topic(
                conn,
                forum_url="https://mne.discourse.group",
                topic_id=456,
                title="Original title",
                first_post="Original post",
                accepted_answer=None,
                category_name=None,
                tags=None,
                reply_count=0,
                like_count=0,
                views=10,
                url="https://mne.discourse.group/t/original/456",
                created_at="2024-01-01T00:00:00Z",
                last_posted_at=None,
            )
            conn.commit()

            # Update
            upsert_discourse_topic(
                conn,
                forum_url="https://mne.discourse.group",
                topic_id=456,
                title="Updated title",
                first_post="Updated post",
                accepted_answer="New answer",
                category_name="General",
                tags=["test"],
                reply_count=10,
                like_count=5,
                views=200,
                url="https://mne.discourse.group/t/updated/456",
                created_at="2024-01-01T00:00:00Z",
                last_posted_at="2024-02-01T00:00:00Z",
            )
            conn.commit()

            row = conn.execute(
                "SELECT title, reply_count, accepted_answer "
                "FROM discourse_topics WHERE topic_id = 456"
            ).fetchone()
            assert row[0] == "Updated title"
            assert row[1] == 10
            assert row[2] == "New answer"

            # Verify only one row exists
            count = conn.execute(
                "SELECT COUNT(*) FROM discourse_topics WHERE topic_id = 456"
            ).fetchone()[0]
            assert count == 1


class TestDiscourseSearch:
    """Tests for FTS5 search on discourse topics."""

    def test_search_finds_topic(self, temp_db: Path):
        """Test that search finds indexed topics."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            with get_connection() as conn:
                upsert_discourse_topic(
                    conn,
                    forum_url="https://mne.discourse.group",
                    topic_id=789,
                    title="Epoch rejection threshold",
                    first_post="What is the best threshold for epoch rejection in MNE?",
                    accepted_answer="Use autoreject or set reject dict manually.",
                    category_name="Support",
                    tags=["epochs", "rejection"],
                    reply_count=8,
                    like_count=4,
                    views=250,
                    url="https://mne.discourse.group/t/epoch-rejection/789",
                    created_at="2024-03-01T00:00:00Z",
                    last_posted_at="2024-03-02T00:00:00Z",
                )
                conn.commit()

            results = search_discourse_topics("epoch rejection", project="test_discourse", limit=5)
            assert len(results) >= 1
            assert isinstance(results[0], DiscourseTopicResult)
            assert "Epoch rejection" in results[0].title
            assert results[0].reply_count == 8

    def test_search_empty_query_returns_empty(self, temp_db: Path):
        """Test that an empty or nonsensical query returns no results."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            results = search_discourse_topics(
                "xyznonexistent12345", project="test_discourse", limit=5
            )
            assert results == []


class TestMNEConfig:
    """Tests for MNE community configuration."""

    def test_mne_config_loads(self):
        """Test that MNE config.yaml loads and validates correctly."""
        from src.core.config.community import CommunityConfig

        config = CommunityConfig.from_yaml("src/assistants/mne/config.yaml")
        assert config.id == "mne"
        assert config.name == "MNE-Python"
        assert len(config.documentation) > 0
        assert len(config.discourse) == 1
        assert "mne.discourse.group" in str(config.discourse[0].url)

    def test_mne_has_github_repos(self):
        """Test that MNE config has GitHub repos configured."""
        from src.core.config.community import CommunityConfig

        config = CommunityConfig.from_yaml("src/assistants/mne/config.yaml")
        assert config.github is not None
        assert len(config.github.repos) >= 5
        assert "mne-tools/mne-python" in config.github.repos

    def test_mne_has_docstrings(self):
        """Test that MNE config has docstring repos configured."""
        from src.core.config.community import CommunityConfig

        config = CommunityConfig.from_yaml("src/assistants/mne/config.yaml")
        assert config.docstrings is not None
        assert len(config.docstrings.repos) >= 5
        repo_names = [r.repo for r in config.docstrings.repos]
        assert "mne-tools/mne-python" in repo_names

    def test_mne_has_citations(self):
        """Test that MNE config has citation DOIs."""
        from src.core.config.community import CommunityConfig

        config = CommunityConfig.from_yaml("src/assistants/mne/config.yaml")
        assert config.citations is not None
        assert len(config.citations.dois) >= 5

    def test_mne_has_sync_schedule(self):
        """Test that MNE config has sync schedules configured."""
        from src.core.config.community import CommunityConfig

        config = CommunityConfig.from_yaml("src/assistants/mne/config.yaml")
        assert config.sync is not None
        assert config.sync.discourse is not None
        assert config.sync.github is not None


class TestDiscourseApiLive:
    """Live tests against mne.discourse.group public API.

    These tests make real HTTP requests. They verify the Discourse
    API integration works end-to-end.
    """

    @pytest.mark.network
    def test_fetch_latest_topics(self):
        """Test fetching latest topics from MNE Discourse."""
        from src.knowledge.discourse_sync import _fetch_json

        data = _fetch_json("https://mne.discourse.group/latest.json", delay=0.5)
        assert data is not None
        topics = data.get("topic_list", {}).get("topics", [])
        assert len(topics) > 0
        # Each topic should have an id and title
        first = topics[0]
        assert "id" in first
        assert "title" in first

    @pytest.mark.network
    def test_fetch_single_topic(self):
        """Test fetching a single topic with posts."""
        from src.knowledge.discourse_sync import _fetch_json

        # First get a valid topic ID from latest
        latest = _fetch_json("https://mne.discourse.group/latest.json", delay=0.5)
        assert latest is not None
        topics = latest["topic_list"]["topics"]
        assert len(topics) > 0
        topic_id = topics[0]["id"]

        # Now fetch that specific topic
        data = _fetch_json(f"https://mne.discourse.group/t/{topic_id}.json", delay=0.5)
        assert data is not None
        assert "title" in data
        posts = data.get("post_stream", {}).get("posts", [])
        assert len(posts) >= 1

    @pytest.mark.network
    def test_sync_small_batch(self, temp_db: Path):
        """Test syncing a small batch of topics end-to-end."""
        from src.knowledge.discourse_sync import sync_discourse_topics

        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            count = sync_discourse_topics(
                base_url="https://mne.discourse.group",
                project="test_discourse",
                incremental=False,
                max_topics=3,
                request_delay=0.5,
            )
            assert count >= 1
            assert count <= 3

            # Verify topics are searchable
            with get_connection("test_discourse") as conn:
                rows = conn.execute("SELECT COUNT(*) FROM discourse_topics").fetchone()
                assert rows[0] >= 1
