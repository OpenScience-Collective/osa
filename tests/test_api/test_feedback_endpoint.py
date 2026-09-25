"""Tests for the feedback API: POST /feedback and GET /metrics/feedback."""

import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.metrics.db import get_metrics_connection, init_metrics_db
from src.metrics.queries import get_feedback_summary

ADMIN_KEY = "test-feedback-admin-key"
HED_KEY = "hed-community-key"


@pytest.fixture
def feedback_db(tmp_path):
    """Isolated, empty metrics DB via DATA_DIR (real path resolution, no mocks).

    get_metrics_db_path() reads DATA_DIR, so pointing it at tmp_path redirects
    every metrics code path (writes, reads, middleware) to a throwaway DB.
    """
    os.environ["DATA_DIR"] = str(tmp_path)
    init_metrics_db()
    yield tmp_path / "metrics.db"
    del os.environ["DATA_DIR"]


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

    def test_general_whitespace_comment_rejected(self, client):
        # _normalize collapses a whitespace-only comment to None, and _check_shape
        # then rejects general feedback that carries no real comment.
        resp = client.post(
            "/feedback",
            json={"community_id": "hed", "feedback_type": "general", "comment": "   "},
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

    @pytest.mark.parametrize(
        "bad_url",
        [
            "javascript:alert(1)",
            "data:text/html,<img onerror=alert(1)>",
            "ftp://evil.example/x",
        ],
    )
    def test_non_http_page_url_rejected(self, client, bad_url):
        # page_url is rendered as a link in the admin dashboard; only http(s)
        # schemes are allowed to prevent stored XSS against admins.
        resp = client.post(
            "/feedback",
            json={
                "community_id": "hed",
                "feedback_type": "response",
                "sentiment": "up",
                "page_url": bad_url,
            },
        )
        assert resp.status_code == 422

    def test_general_sentiment_is_stripped(self, feedback_db, client):
        # A general payload that smuggles a sentiment must be stored with NULL
        # sentiment so it never counts as a thumbs-down.
        resp = client.post(
            "/feedback",
            json={
                "community_id": "hed",
                "feedback_type": "general",
                "sentiment": "down",
                "comment": "smuggled sentiment",
            },
        )
        assert resp.status_code == 200
        conn = get_metrics_connection(feedback_db)
        try:
            row = conn.execute(
                "SELECT sentiment FROM feedback_log WHERE feedback_type='general'"
            ).fetchone()
            summary = get_feedback_summary(conn, community_id="hed")
        finally:
            conn.close()
        assert row["sentiment"] is None
        assert summary["thumbs_down"] == 0

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

    def test_request_id_round_trips(self, feedback_db, client):
        # The widget keys a vote on the request_id from the chat `done` event;
        # the stored row must carry it so feedback joins back to request_log.
        client.post(
            "/feedback",
            json={
                "community_id": "hed",
                "feedback_type": "response",
                "sentiment": "down",
                "request_id": "req-xyz-123",
                "message_index": 4,
            },
        )
        conn = get_metrics_connection(feedback_db)
        try:
            row = conn.execute("SELECT request_id, message_index FROM feedback_log").fetchone()
        finally:
            conn.close()
        assert row["request_id"] == "req-xyz-123"
        assert row["message_index"] == 4


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

    def test_admin_aggregates_across_communities(self, client):
        # Two up votes in different communities must sum, proving the admin view
        # is not silently scoped to one community.
        client.post(
            "/feedback",
            json={"community_id": "hed", "feedback_type": "response", "sentiment": "up"},
        )
        client.post(
            "/feedback",
            json={"community_id": "eeglab", "feedback_type": "response", "sentiment": "up"},
        )
        resp = client.get("/metrics/feedback", headers={"X-API-Key": ADMIN_KEY})
        assert resp.json()["summary"]["thumbs_up"] == 2

    def test_comments_only_filter(self, client):
        client.post(
            "/feedback",
            json={"community_id": "hed", "feedback_type": "response", "sentiment": "up"},
        )
        client.post(
            "/feedback",
            json={"community_id": "hed", "feedback_type": "general", "comment": "great tool"},
        )
        resp = client.get(
            "/metrics/feedback",
            params={"comments_only": "true"},
            headers={"X-API-Key": HED_KEY},
        )
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["comment"] == "great tool"

    def test_limit_above_max_rejected(self, client):
        resp = client.get(
            "/metrics/feedback",
            params={"limit": 9999},
            headers={"X-API-Key": HED_KEY},
        )
        assert resp.status_code == 422

    def test_offset_pagination(self, client):
        for i in range(3):
            client.post(
                "/feedback",
                json={
                    "community_id": "hed",
                    "feedback_type": "general",
                    "comment": f"note {i}",
                },
            )
        page1 = client.get(
            "/metrics/feedback",
            params={"limit": 2, "offset": 0},
            headers={"X-API-Key": HED_KEY},
        ).json()["entries"]
        page2 = client.get(
            "/metrics/feedback",
            params={"limit": 2, "offset": 2},
            headers={"X-API-Key": HED_KEY},
        ).json()["entries"]
        assert len(page1) == 2
        assert len(page2) == 1
        # No overlap between pages
        seen = {e["comment"] for e in page1} | {e["comment"] for e in page2}
        assert seen == {"note 0", "note 1", "note 2"}

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


@pytest.fixture
def langfuse_env():
    """LangFuse keys configured in settings (values are never sent anywhere)."""
    from src.api.config import get_settings

    os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-test"
    os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-test"
    get_settings.cache_clear()
    yield
    del os.environ["LANGFUSE_PUBLIC_KEY"]
    del os.environ["LANGFUSE_SECRET_KEY"]
    get_settings.cache_clear()


@pytest.fixture
def fake_langfuse(monkeypatch):
    """Stand-in for the optional langfuse package that records create_score calls."""
    scores: list[dict] = []

    class FakeLangfuse:
        fail = False

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def create_score(self, **kwargs):
            if FakeLangfuse.fail:
                raise RuntimeError("LangFuse is down")
            scores.append(kwargs)

    module = types.ModuleType("langfuse")
    module.Langfuse = FakeLangfuse
    monkeypatch.setitem(sys.modules, "langfuse", module)
    return types.SimpleNamespace(scores=scores, cls=FakeLangfuse)


def _log_traced_request(db_path, request_id: str, trace_id: str | None) -> None:
    conn = get_metrics_connection(db_path)
    conn.execute(
        "INSERT INTO request_log (request_id, timestamp, community_id, endpoint, method, "
        "langfuse_trace_id) VALUES (?, '2026-09-25T00:00:00Z', 'hed', '/hed/ask', 'POST', ?)",
        (request_id, trace_id),
    )
    conn.commit()
    conn.close()


def _rate(client, request_id: str | None, sentiment: str = "down", comment: str | None = None):
    return client.post(
        "/feedback",
        json={
            "community_id": "hed",
            "feedback_type": "response",
            "sentiment": sentiment,
            "request_id": request_id,
            "comment": comment,
        },
    )


@pytest.mark.usefixtures("langfuse_env")
class TestFeedbackToLangfuse:
    """Feedback is attached to the rated answer's LangFuse trace (issue #515)."""

    TRACE_ID = "0123456789abcdef0123456789abcdef"

    def test_score_sent_to_the_answers_trace(self, feedback_db, client, fake_langfuse):
        _log_traced_request(feedback_db, "req-traced", self.TRACE_ID)

        resp = _rate(client, "req-traced", "down", "wrong class name")

        assert resp.status_code == 200
        assert fake_langfuse.scores == [
            {
                "trace_id": self.TRACE_ID,
                "name": "user_feedback",
                "value": "down",
                "data_type": "CATEGORICAL",
                "comment": "wrong class name",
            }
        ]

    def test_no_score_when_request_was_not_traced(self, feedback_db, client, fake_langfuse):
        _log_traced_request(feedback_db, "req-untraced", None)

        assert _rate(client, "req-untraced").status_code == 200
        assert _rate(client, "req-unknown").status_code == 200
        assert fake_langfuse.scores == []

    def test_langfuse_error_does_not_fail_the_request(self, feedback_db, client, fake_langfuse):
        _log_traced_request(feedback_db, "req-traced", self.TRACE_ID)
        fake_langfuse.cls.fail = True

        resp = _rate(client, "req-traced")

        assert resp.status_code == 200
        assert fake_langfuse.scores == []


@pytest.mark.usefixtures("feedback_db")
def test_no_score_when_langfuse_not_configured(feedback_db, client, fake_langfuse):
    """Without LangFuse keys, feedback is stored locally only."""
    _log_traced_request(feedback_db, "req-traced", TestFeedbackToLangfuse.TRACE_ID)

    assert _rate(client, "req-traced").status_code == 200
    assert fake_langfuse.scores == []
