"""Tests for metrics database storage layer."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.metrics.db import (
    RequestLogEntry,
    extract_token_usage,
    extract_tool_names,
    get_metrics_connection,
    init_metrics_db,
    log_request,
    now_iso,
)


@pytest.fixture
def metrics_db(tmp_path):
    """Create a temporary metrics database."""
    db_path = tmp_path / "metrics.db"
    init_metrics_db(db_path)
    return db_path


class TestInitMetricsDb:
    """Tests for init_metrics_db()."""

    def test_creates_table(self, tmp_path):
        """init_metrics_db creates the request_log table."""
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)

        conn = get_metrics_connection(db_path)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='request_log'"
            ).fetchall()
            assert len(tables) == 1
        finally:
            conn.close()

    def test_idempotent(self, tmp_path):
        """Calling init_metrics_db twice does not error."""
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)
        init_metrics_db(db_path)

        conn = get_metrics_connection(db_path)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='request_log'"
            ).fetchall()
            assert len(tables) == 1
        finally:
            conn.close()

    def test_wal_mode(self, tmp_path):
        """init_metrics_db sets WAL journal mode."""
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)

        conn = get_metrics_connection(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
        finally:
            conn.close()

    def test_creates_indexes(self, tmp_path):
        """init_metrics_db creates the expected indexes."""
        db_path = tmp_path / "metrics.db"
        init_metrics_db(db_path)

        conn = get_metrics_connection(db_path)
        try:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='request_log'"
            ).fetchall()
            index_names = {r[0] for r in indexes}
            assert "idx_request_log_community" in index_names
            assert "idx_request_log_timestamp" in index_names
            assert "idx_request_log_community_timestamp" in index_names
        finally:
            conn.close()


class TestLogRequest:
    """Tests for log_request()."""

    def test_inserts_entry(self, metrics_db):
        """log_request inserts a row that can be read back."""
        entry = RequestLogEntry(
            request_id="test-123",
            timestamp=now_iso(),
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=150.5,
            status_code=200,
            model="qwen/qwen3-235b",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            tools_called=["search_docs", "validate_hed"],
            key_source="platform",
            stream=False,
        )
        log_request(entry, db_path=metrics_db)

        conn = get_metrics_connection(metrics_db)
        try:
            row = conn.execute(
                "SELECT * FROM request_log WHERE request_id = ?", ("test-123",)
            ).fetchone()
            assert row is not None
            assert row["community_id"] == "hed"
            assert row["endpoint"] == "/hed/ask"
            assert row["method"] == "POST"
            assert row["duration_ms"] == 150.5
            assert row["status_code"] == 200
            assert row["model"] == "qwen/qwen3-235b"
            assert row["input_tokens"] == 100
            assert row["output_tokens"] == 50
            assert row["total_tokens"] == 150
            assert row["key_source"] == "platform"
            assert row["stream"] == 0
            tools = json.loads(row["tools_called"])
            assert tools == ["search_docs", "validate_hed"]
        finally:
            conn.close()

    def test_null_agent_fields(self, metrics_db):
        """Non-agent requests have NULL agent fields."""
        entry = RequestLogEntry(
            request_id="basic-req",
            timestamp=now_iso(),
            endpoint="/health",
            method="GET",
            status_code=200,
            duration_ms=5.0,
        )
        log_request(entry, db_path=metrics_db)

        conn = get_metrics_connection(metrics_db)
        try:
            row = conn.execute(
                "SELECT * FROM request_log WHERE request_id = ?", ("basic-req",)
            ).fetchone()
            assert row is not None
            assert row["model"] is None
            assert row["input_tokens"] is None
            assert row["output_tokens"] is None
            assert row["total_tokens"] is None
            assert row["tools_called"] is None
            assert row["key_source"] is None
            assert row["community_id"] is None
        finally:
            conn.close()

    def test_stream_flag(self, metrics_db):
        """stream=True is stored as 1."""
        entry = RequestLogEntry(
            request_id="stream-req",
            timestamp=now_iso(),
            endpoint="/hed/ask",
            method="POST",
            stream=True,
        )
        log_request(entry, db_path=metrics_db)

        conn = get_metrics_connection(metrics_db)
        try:
            row = conn.execute(
                "SELECT stream FROM request_log WHERE request_id = ?", ("stream-req",)
            ).fetchone()
            assert row["stream"] == 1
        finally:
            conn.close()


class TestExtractTokenUsage:
    """Tests for extract_token_usage()."""

    def test_extracts_from_ai_messages(self):
        """Extracts token usage from AIMessages with usage_metadata."""
        msg = AIMessage(content="hello")
        msg.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        result = {"messages": [HumanMessage(content="hi"), msg]}

        inp, out, total = extract_token_usage(result)
        assert inp == 10
        assert out == 5
        assert total == 15

    def test_sums_multiple_messages(self):
        """Sums usage across multiple AIMessages."""
        msg1 = AIMessage(content="first")
        msg1.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        msg2 = AIMessage(content="second")
        msg2.usage_metadata = {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
        result = {"messages": [msg1, msg2]}

        inp, out, total = extract_token_usage(result)
        assert inp == 30
        assert out == 15
        assert total == 45

    def test_returns_zeros_when_no_usage(self):
        """Returns (0, 0, 0) when no usage_metadata."""
        result = {"messages": [AIMessage(content="hello")]}
        inp, out, total = extract_token_usage(result)
        assert (inp, out, total) == (0, 0, 0)

    def test_returns_zeros_for_empty_result(self):
        """Returns (0, 0, 0) for empty result."""
        assert extract_token_usage({}) == (0, 0, 0)
        assert extract_token_usage({"messages": []}) == (0, 0, 0)


class TestExtractToolNames:
    """Tests for extract_tool_names()."""

    def test_extracts_names(self):
        result = {"tool_calls": [{"name": "search"}, {"name": "validate"}]}
        assert extract_tool_names(result) == ["search", "validate"]

    def test_empty_tool_calls(self):
        assert extract_tool_names({"tool_calls": []}) == []
        assert extract_tool_names({}) == []

    def test_skips_empty_names(self):
        result = {"tool_calls": [{"name": ""}, {"name": "search"}]}
        assert extract_tool_names(result) == ["search"]
