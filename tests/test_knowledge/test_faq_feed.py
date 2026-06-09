"""Tests for the public FAQ feed listing helper.

Uses a temporary SQLite database populated with real FAQ rows (no mocks of
business logic; only the database path is redirected to a temp file).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import get_connection, init_db, upsert_faq_entry
from src.knowledge.search import FAQResult, list_faq_entries


@pytest.fixture
def faq_db(tmp_path: Path):
    """Create a test database populated with FAQ entries."""
    db_path = tmp_path / "knowledge" / "test.db"

    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()

        with get_connection() as conn:
            entries = [
                {
                    "thread_id": "t1",
                    "question": "How do I run ICA in EEGLAB?",
                    "answer": "Use runica via the Tools menu.",
                    "tags": ["ica", "eeglab"],
                    "category": "how-to",
                    "quality_score": 0.95,
                    "first_message_date": "2020-01-01",
                },
                {
                    "thread_id": "t2",
                    "question": "Why does my dataset fail to load?",
                    "answer": "Check the file path and channel locations.",
                    "tags": ["loading"],
                    "category": "troubleshooting",
                    "quality_score": 0.80,
                    "first_message_date": "2021-06-15",
                },
                {
                    "thread_id": "t3",
                    "question": "What is a reference electrode?",
                    "answer": "Contact support@brainproducts.com for hardware details.",
                    "tags": ["reference"],
                    "category": "reference",
                    "quality_score": 0.60,
                    "first_message_date": "2019-03-20",
                },
            ]
            for e in entries:
                upsert_faq_entry(
                    conn,
                    list_name="eeglablist",
                    thread_id=e["thread_id"],
                    thread_url=f"https://example.org/{e['thread_id']}",
                    question=e["question"],
                    answer=e["answer"],
                    tags=e["tags"],
                    category=e["category"],
                    message_count=3,
                    participant_count=2,
                    first_message_date=e["first_message_date"],
                    quality_score=e["quality_score"],
                    summary_model="test-model",
                )
            conn.commit()

        yield db_path


class TestListFAQEntries:
    """Tests for list_faq_entries (browse mode, no FTS query)."""

    def test_returns_all_entries_and_total(self, faq_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            entries, total = list_faq_entries(project="eeglab")

        assert total == 3
        assert len(entries) == 3
        assert all(isinstance(e, FAQResult) for e in entries)

    def test_ordered_by_quality_descending(self, faq_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            entries, _ = list_faq_entries(project="eeglab")

        scores = [e.quality_score for e in entries]
        assert scores == sorted(scores, reverse=True)
        assert entries[0].quality_score == 0.95

    def test_min_quality_filter(self, faq_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            entries, total = list_faq_entries(project="eeglab", min_quality=0.85)

        assert total == 1
        assert len(entries) == 1
        assert entries[0].quality_score >= 0.85

    def test_category_filter(self, faq_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            entries, total = list_faq_entries(project="eeglab", category="troubleshooting")

        assert total == 1
        assert entries[0].category == "troubleshooting"

    def test_pagination_limit_and_offset(self, faq_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            page1, total1 = list_faq_entries(project="eeglab", limit=2, offset=0)
            page2, total2 = list_faq_entries(project="eeglab", limit=2, offset=2)

        # total is the full count regardless of pagination window
        assert total1 == 3
        assert total2 == 3
        assert len(page1) == 2
        assert len(page2) == 1
        # No overlap between pages
        page1_questions = {e.question for e in page1}
        page2_questions = {e.question for e in page2}
        assert page1_questions.isdisjoint(page2_questions)

    def test_empty_database_returns_zero(self, tmp_path: Path):
        db_path = tmp_path / "knowledge" / "empty.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db()
            entries, total = list_faq_entries(project="eeglab")

        assert total == 0
        assert entries == []
