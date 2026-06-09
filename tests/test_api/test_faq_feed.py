"""Tests for the public FAQ feed endpoint: GET /{community_id}/faq.

Uses a real registered community, a temporary SQLite knowledge database
populated with FAQ rows, and the config gate toggled per test. No business
logic is mocked; only the database path and the opt-in flag are controlled.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.community import create_community_router
from src.assistants import discover_assistants, registry
from src.core.config.community import PublicFeedsConfig
from src.knowledge.db import get_connection, init_db, upsert_faq_entry

COMMUNITY_ID = "eeglab"

discover_assistants()


@pytest.fixture
def faq_db(tmp_path: Path) -> Iterator[Path]:
    """Temp knowledge DB populated with FAQ entries, including one with an email."""
    db_path = tmp_path / "knowledge" / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()
        with get_connection() as conn:
            upsert_faq_entry(
                conn,
                list_name="eeglablist",
                thread_id="t1",
                thread_url="https://example.org/t1",
                question="How do I run ICA in EEGLAB?",
                answer="Use runica from the Tools menu.",
                tags=["ica"],
                category="how-to",
                message_count=3,
                participant_count=2,
                first_message_date="2020-01-01",
                quality_score=0.95,
                summary_model="test-model",
            )
            upsert_faq_entry(
                conn,
                list_name="eeglablist",
                thread_id="t2",
                thread_url="https://example.org/t2",
                question="Who do I contact for hardware support?",
                answer="Email support@brainproducts.com for hardware questions.",
                tags=["hardware"],
                category="reference",
                message_count=2,
                participant_count=2,
                first_message_date="2021-01-01",
                quality_score=0.70,
                summary_model="test-model",
            )
            conn.commit()
        yield db_path


@pytest.fixture
def feeds_enabled() -> Iterator[None]:
    """Enable public_feeds.faq on the community config, restoring it afterward."""
    info = registry.get(COMMUNITY_ID)
    assert info is not None and info.community_config is not None
    original = info.community_config.public_feeds
    info.community_config.public_feeds = PublicFeedsConfig(faq=True)
    try:
        yield
    finally:
        info.community_config.public_feeds = original


@pytest.fixture
def feeds_disabled() -> Iterator[None]:
    """Force public_feeds off, restoring the original afterward."""
    info = registry.get(COMMUNITY_ID)
    assert info is not None and info.community_config is not None
    original = info.community_config.public_feeds
    info.community_config.public_feeds = None
    try:
        yield
    finally:
        info.community_config.public_feeds = original


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_community_router(COMMUNITY_ID))
    return TestClient(app)


class TestFAQFeedGate:
    """The endpoint is opt-in via public_feeds.faq."""

    @pytest.mark.usefixtures("feeds_disabled")
    def test_disabled_returns_404(self, client, faq_db):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            resp = client.get(f"/{COMMUNITY_ID}/faq")
        assert resp.status_code == 404

    @pytest.mark.usefixtures("feeds_enabled")
    def test_enabled_returns_200(self, client, faq_db):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            resp = client.get(f"/{COMMUNITY_ID}/faq")
        assert resp.status_code == 200


@pytest.mark.usefixtures("feeds_enabled")
class TestFAQFeedContent:
    """Response shape and filtering when enabled."""

    def test_returns_all_entries(self, client, faq_db):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            resp = client.get(f"/{COMMUNITY_ID}/faq")
        body = resp.json()
        assert body["community_id"] == COMMUNITY_ID
        assert body["total"] == 2
        assert len(body["entries"]) == 2
        # Ordered by quality descending
        assert body["entries"][0]["quality_score"] == 0.95

    def test_exposed_fields_only(self, client, faq_db):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            resp = client.get(f"/{COMMUNITY_ID}/faq")
        entry = resp.json()["entries"][0]
        assert set(entry.keys()) == {
            "question",
            "answer",
            "tags",
            "category",
            "quality_score",
            "message_count",
            "first_message_date",
            "thread_url",
        }

    def test_emails_are_redacted(self, client, faq_db):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            resp = client.get(f"/{COMMUNITY_ID}/faq")
        answers = " ".join(e["answer"] for e in resp.json()["entries"])
        assert "support@brainproducts.com" not in answers
        assert "[email redacted]" in answers

    def test_category_filter(self, client, faq_db):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            resp = client.get(f"/{COMMUNITY_ID}/faq", params={"category": "how-to"})
        body = resp.json()
        assert body["total"] == 1
        assert body["entries"][0]["category"] == "how-to"

    def test_min_quality_filter(self, client, faq_db):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            resp = client.get(f"/{COMMUNITY_ID}/faq", params={"min_quality": 0.9})
        body = resp.json()
        assert body["total"] == 1
        assert body["entries"][0]["quality_score"] >= 0.9

    def test_search_query(self, client, faq_db):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            resp = client.get(f"/{COMMUNITY_ID}/faq", params={"q": "ICA"})
        body = resp.json()
        assert body["total"] >= 1
        assert any("ICA" in e["question"] for e in body["entries"])

    def test_pagination(self, client, faq_db):
        with patch("src.knowledge.db.get_db_path", return_value=faq_db):
            resp = client.get(f"/{COMMUNITY_ID}/faq", params={"limit": 1, "offset": 0})
        body = resp.json()
        assert body["total"] == 2
        assert len(body["entries"]) == 1
        assert body["limit"] == 1
        assert body["offset"] == 0


@pytest.mark.usefixtures("feeds_enabled", "faq_db")
class TestFAQFeedValidation:
    """Query parameter bounds are enforced (rejected before DB access)."""

    def test_invalid_min_quality_rejected(self, client):
        resp = client.get(f"/{COMMUNITY_ID}/faq", params={"min_quality": 5})
        assert resp.status_code == 422

    def test_limit_upper_bound_enforced(self, client):
        resp = client.get(f"/{COMMUNITY_ID}/faq", params={"limit": 9999})
        assert resp.status_code == 422
