"""Tests for metrics middleware."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.metrics.middleware import MetricsMiddleware, _extract_community_id


class TestExtractCommunityId:
    """Tests for _extract_community_id helper."""

    def test_extracts_from_ask(self):
        assert _extract_community_id("/hed/ask") == "hed"

    def test_extracts_from_chat(self):
        assert _extract_community_id("/bids/chat") == "bids"

    def test_returns_none_for_non_community(self):
        assert _extract_community_id("/health") is None
        assert _extract_community_id("/sync/status") is None
        assert _extract_community_id("/metrics/overview") is None

    def test_returns_none_for_root(self):
        assert _extract_community_id("/") is None


class TestMetricsMiddleware:
    """Tests for MetricsMiddleware integration."""

    @pytest.fixture
    def test_app(self):
        """Create a minimal FastAPI app with MetricsMiddleware."""
        app = FastAPI()
        app.add_middleware(MetricsMiddleware)

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.post("/hed/ask")
        async def ask(request: Request):
            # Simulate agent metrics
            request.state.metrics_agent_data = {
                "model": "test-model",
                "key_source": "platform",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "tools_called": ["search"],
                "stream": False,
            }
            return {"answer": "test"}

        @app.post("/hed/streaming")
        async def streaming(request: Request):
            # Simulate handler that logs its own metrics
            request.state.metrics_logged = True
            return {"answer": "streamed"}

        with patch("src.metrics.middleware.log_request") as mock_log:
            yield app, mock_log

    def test_logs_basic_request(self, test_app):
        """Middleware logs basic request data for non-agent endpoints."""
        app, mock_log = test_app
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

        assert mock_log.called
        entry = mock_log.call_args[0][0]
        assert entry.endpoint == "/health"
        assert entry.method == "GET"
        assert entry.status_code == 200
        assert entry.duration_ms > 0
        assert entry.model is None  # non-agent

    def test_picks_up_agent_metrics(self, test_app):
        """Middleware reads agent metrics from request.state."""
        app, mock_log = test_app
        client = TestClient(app)
        response = client.post("/hed/ask", json={})
        assert response.status_code == 200

        assert mock_log.called
        entry = mock_log.call_args[0][0]
        assert entry.model == "test-model"
        assert entry.key_source == "platform"
        assert entry.input_tokens == 10
        assert entry.tools_called == ["search"]
        assert entry.community_id == "hed"

    def test_skips_when_handler_logged(self, test_app):
        """Middleware skips logging when handler set metrics_logged=True."""
        app, mock_log = test_app
        client = TestClient(app)
        response = client.post("/hed/streaming", json={})
        assert response.status_code == 200
        assert not mock_log.called
