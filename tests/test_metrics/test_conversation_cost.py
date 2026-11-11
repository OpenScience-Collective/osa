"""Cost per conversation, quick against analysis (`get_conversation_costs`).

The database is built with real `log_request` calls and costs from the real `estimate_cost`,
the way the chat path writes them; nothing here stands in for the code under test.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.core.services.anthropic_models import HAIKU, SONNET
from src.metrics.cost import estimate_cost
from src.metrics.db import (
    RequestLogEntry,
    get_metrics_connection,
    init_metrics_db,
    log_request,
)
from src.metrics.queries import get_conversation_costs

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)


def _row(
    db: Path,
    request_id: str,
    *,
    model: str | None,
    tier: str | None,
    conversation_id: str | None,
    input_tokens: int = 1000,
    output_tokens: int = 100,
    age_days: float = 1.0,
    community: str = "nemar",
) -> float | None:
    """Log one row as the chat path does; returns the cost it priced the row at."""
    has_tokens = model is not None
    cost = estimate_cost(model, input_tokens, output_tokens) if has_tokens else None
    log_request(
        RequestLogEntry(
            request_id=request_id,
            timestamp=(NOW - timedelta(days=age_days)).isoformat(),
            endpoint=f"/{community}/chat",
            method="POST",
            community_id=community,
            model=model,
            input_tokens=input_tokens if has_tokens else None,
            output_tokens=output_tokens if has_tokens else None,
            total_tokens=input_tokens + output_tokens if has_tokens else None,
            estimated_cost=cost,
            tier=tier,
            conversation_id=conversation_id,
        ),
        db,
    )
    return cost


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "metrics.db"
    init_metrics_db(path)
    return path


def _report(db: Path, community: str = "nemar") -> dict:
    conn = get_metrics_connection(db)
    try:
        return get_conversation_costs(conn, community, SINCE)
    finally:
        conn.close()


class TestConversationCosts:
    def test_a_handover_conversation_is_priced_quick_against_analysis(self, db) -> None:
        quick_1 = _row(db, "r1", model=HAIKU, tier="quick", conversation_id="conv-a", age_days=2)
        quick_2 = _row(
            db, "r2:quick", model=HAIKU, tier="quick", conversation_id="conv-a", age_days=1.5
        )
        analysis = _row(
            db,
            "r2",
            model=SONNET,
            tier="analysis",
            conversation_id="conv-a",
            input_tokens=20_000,
            output_tokens=2_000,
            age_days=1.5,
        )

        (conversation,) = _report(db)["conversations"]

        assert conversation["conversation_id"] == "conv-a"
        assert conversation["quick"]["turns"] == 2
        assert conversation["analysis"]["turns"] == 1
        assert conversation["quick"]["cost"] == pytest.approx(quick_1 + quick_2)
        assert conversation["analysis"]["cost"] == pytest.approx(analysis)
        assert conversation["total_cost"] == pytest.approx(quick_1 + quick_2 + analysis)
        assert conversation["analysis"]["cost"] > conversation["quick"]["cost"]
        assert conversation["analysis"]["input_tokens"] == 20_000
        assert conversation["started"].startswith("2026-11-08")

    def test_conversations_are_separate_and_totals_add_up(self, db) -> None:
        a = _row(db, "r1", model=HAIKU, tier="quick", conversation_id="conv-a", age_days=3)
        b = _row(db, "r2", model=HAIKU, tier="quick", conversation_id="conv-b", age_days=1)
        c = _row(db, "r3", model=SONNET, tier="analysis", conversation_id="conv-b", age_days=1)

        report = _report(db)

        assert [c["conversation_id"] for c in report["conversations"]] == ["conv-a", "conv-b"]
        assert report["totals"]["quick"]["turns"] == 2
        assert report["totals"]["analysis"]["turns"] == 1
        assert report["totals"]["quick"]["cost"] == pytest.approx(a + b)
        assert report["totals"]["analysis"]["cost"] == pytest.approx(c)
        assert report["totals"]["total_cost"] == pytest.approx(a + b + c)
        assert sum(c["total_cost"] for c in report["conversations"]) == pytest.approx(
            report["totals"]["total_cost"]
        )

    def test_middleware_stub_rows_are_ignored(self, db) -> None:
        _row(db, "stub", model=None, tier=None, conversation_id=None)
        _row(db, "real", model=HAIKU, tier="quick", conversation_id="conv-a")

        report = _report(db)

        assert report["totals"]["quick"]["turns"] == 1
        assert report["unassigned"] == []

    def test_rows_from_before_conversation_ids_are_listed_by_model(self, db) -> None:
        haiku = _row(db, "old1", model=HAIKU, tier=None, conversation_id=None)
        haiku += _row(db, "old2", model=HAIKU, tier=None, conversation_id=None)
        sonnet = _row(
            db, "old3", model=SONNET, tier=None, conversation_id=None, input_tokens=50_000
        )

        report = _report(db)

        assert report["conversations"] == []
        by_model = {b["model"]: b for b in report["unassigned"]}
        assert by_model[HAIKU]["turns"] == 2
        assert by_model[HAIKU]["cost"] == pytest.approx(haiku)
        assert by_model[SONNET]["cost"] == pytest.approx(sonnet)
        assert [b["model"] for b in report["unassigned"]] == [SONNET, HAIKU]

    def test_a_conversation_row_with_no_tier_is_counted_apart(self, db) -> None:
        cost = _row(db, "r1", model=SONNET, tier=None, conversation_id="conv-a")

        (conversation,) = _report(db)["conversations"]

        assert conversation["other"]["cost"] == pytest.approx(cost)
        assert conversation["quick"]["turns"] == conversation["analysis"]["turns"] == 0
        assert conversation["total_cost"] == pytest.approx(cost)

    def test_other_communities_and_older_rows_are_left_out(self, db) -> None:
        _row(db, "other", model=HAIKU, tier="quick", conversation_id="x", community="hed")
        _row(db, "old", model=HAIKU, tier="quick", conversation_id="y", age_days=30)
        kept = _row(db, "kept", model=HAIKU, tier="quick", conversation_id="z", age_days=6.9)

        report = _report(db)

        assert [c["conversation_id"] for c in report["conversations"]] == ["z"]
        assert report["totals"]["total_cost"] == pytest.approx(kept)

    def test_no_rows_is_an_empty_report(self, db) -> None:
        report = _report(db)

        assert report["conversations"] == []
        assert report["unassigned"] == []
        assert report["totals"]["total_cost"] == 0

    def test_a_database_from_before_the_columns_existed_still_reports(self, tmp_path) -> None:
        """The report opens the database read-only, so it cannot migrate an old copy."""
        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE request_log (id INTEGER PRIMARY KEY, request_id TEXT, timestamp TEXT,"
            " community_id TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER,"
            " estimated_cost REAL)"
        )
        conn.execute(
            "INSERT INTO request_log (request_id, timestamp, community_id, model, input_tokens,"
            " output_tokens, estimated_cost) VALUES ('o1', ?, 'nemar', ?, 100, 10, 0.5)",
            ((NOW - timedelta(days=1)).isoformat(), HAIKU),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        try:
            report = get_conversation_costs(conn, "nemar", SINCE)
        finally:
            conn.close()

        assert report["conversations"] == []
        assert report["unassigned"][0]["model"] == HAIKU
        assert report["unassigned"][0]["cost"] == pytest.approx(0.5)

    def test_since_accepts_a_datetime_and_a_naive_one_is_utc(self, db) -> None:
        _row(db, "r1", model=HAIKU, tier="quick", conversation_id="conv-a", age_days=1)
        conn = get_metrics_connection(db)
        try:
            naive = get_conversation_costs(conn, "nemar", SINCE.replace(tzinfo=None))
        finally:
            conn.close()

        assert len(naive["conversations"]) == 1
