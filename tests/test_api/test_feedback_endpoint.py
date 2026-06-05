"""Tests for the feedback API: POST /feedback and GET /metrics/feedback."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.metrics.db import get_metrics_connection, init_metrics_db
from src.metrics.queries import get_feedback_summary

ADMIN_KEY = "test-feedback-admin-key"
HED_KEY = "hed-community-key"


@pytest.fixture
def feedback_db(tmp_path):
    """Isolated, empty metrics DB redirected for all metrics code paths."""
    db_path = tmp_path / "metrics.db"
    init_metrics_db(db_path)
    with patch("src.metrics.db.get_metrics_db_path", return_value=db_path):
        yield db_path


@pytest.fixture
def scoped_auth_env():
    """Admin key + per-community key for hed."""
    from src.api.config import get_settings

    os.environ["API_KEYS"] = ADMIN_KEY
    os.environ["REQUIRE_API_AUTH"] = "true"
    os.environ["COMMUNITY_ADMIN_KEYS"] = f"hed:{HED_KEY}"
    get_settings.cache_clear()
    yield
    del os.environ["API_KEYS"]
    del os.environ["REQUIRE_API_AUTH"]
    del os.environ["COMMUNITY_ADMIN_KEYS"]
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.usefixtures("feedback_db")
class TestSubmitFeedback:
    """POST /feedback (anonymous, no auth)."""

    def test_thumbs_up(self, client):
        resp = client.post(
            "/feedback",
            json={
                "community_id": "hed",
                "feedback_type": "response",
                "sentiment": "up",
                "request_id": "req-1",
                "session_id": "sess-1",
                "message_index": 1,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["feedback_id"]

    def test_thumbs_down_with_comment(self, client):
        resp = client.post(
            "/feedback",
            json={
                "community_id": "hed",
                "feedback_type": "response",
                "sentiment": "down",
                "comment": "missed the BIDS sidecar question",
            },
        )
        assert resp.status_code == 200

    def test_general_feedback(self, client):
        resp = client.post(
            "/feedback",
            json={
                "community_id": "hed",
                "feedback_type": "general",
                "comment": "Please add more examples.",
            },
        )
        assert resp.status_code == 200

    def test_unknown_community_rejected(self, client):
        resp = client.post(
            "/feedback",
            json={
                "community_id": "not-a-real-community",
                "feedback_type": "response",
                "sentiment": "up",
            },
        )
        assert resp.status_code == 404

    def test_response_without_sentiment_rejected(self, client):
        resp = client.post(
            "/feedback",
            json={"community_id": "hed", "feedback_type": "response"},
        )
        assert resp.status_code == 422

    def test_general_without_comment_rejected(self, client):
        resp = client.post(
            "/feedback",
            json={"community_id": "hed", "feedback_type": "general"},
        )
        assert resp.status_code == 422

    def test_oversized_comment_rejected(self, client):
        resp = client.post(
            "/feedback",
            json={
                "community_id": "hed",
                "feedback_type": "general",
                "comment": "x" * 6000,
            },
        )
        assert resp.status_code == 422

    def test_non_http_page_url_rejected(self, client):
        # page_url is rendered as a link in the admin dashboard; a javascript:
        # scheme must be rejected to prevent stored XSS against admins.
        resp = client.post(
            "/feedback",
            json={
                "community_id": "hed",
                "feedback_type": "response",
                "sentiment": "up",
                "page_url": "javascript:alert(1)",
            },
        )
        assert resp.status_code == 422

    def test_http_page_url_accepted(self, client):
        resp = client.post(
            "/feedback",
            json={
                "community_id": "hed",
                "feedback_type": "response",
                "sentiment": "up",
                "page_url": "https://hedtags.org/page",
            },
        )
        assert resp.status_code == 200

    def test_persisted_and_readable(self, feedback_db, client):
        client.post(
            "/feedback",
            json={"community_id": "hed", "feedback_type": "response", "sentiment": "up"},
        )
        conn = get_metrics_connection(feedback_db)
        try:
            summary = get_feedback_summary(conn, community_id="hed")
        finally:
            conn.close()
        assert summary["thumbs_up"] == 1


@pytest.mark.usefixtures("feedback_db", "scoped_auth_env")
class TestReadFeedback:
    """GET /metrics/feedback (scoped admin auth)."""

    def _seed(self, client):
        client.post(
            "/feedback",
            json={"community_id": "hed", "feedback_type": "response", "sentiment": "up"},
        )
        client.post(
            "/feedback",
            json={
                "community_id": "eeglab",
                "feedback_type": "response",
                "sentiment": "down",
            },
        )

    def test_admin_sees_all(self, client):
        self._seed(client)
        resp = client.get("/metrics/feedback", headers={"X-API-Key": ADMIN_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["thumbs_up"] == 1
        assert data["summary"]["thumbs_down"] == 1

    def test_community_key_scoped_to_own(self, client):
        self._seed(client)
        resp = client.get("/metrics/feedback", headers={"X-API-Key": HED_KEY})
        assert resp.status_code == 200
        data = resp.json()
        # hed key must only see hed's up vote, not eeglab's down vote
        assert data["community_id"] == "hed"
        assert data["summary"]["thumbs_up"] == 1
        assert data["summary"]["thumbs_down"] == 0
        assert all(e["community_id"] == "hed" for e in data["entries"])

    def test_community_key_cannot_override_filter(self, client):
        self._seed(client)
        # hed key asks for eeglab; must still be scoped to hed
        resp = client.get(
            "/metrics/feedback",
            params={"community_id": "eeglab"},
            headers={"X-API-Key": HED_KEY},
        )
        data = resp.json()
        assert data["community_id"] == "hed"
        assert data["summary"]["thumbs_down"] == 0

    def test_requires_auth(self, client):
        resp = client.get("/metrics/feedback")
        assert resp.status_code == 401
