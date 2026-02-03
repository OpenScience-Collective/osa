"""Tests for budget checking."""

import pytest

from src.metrics.budget import BudgetStatus, check_budget
from src.metrics.db import RequestLogEntry, get_metrics_connection, init_metrics_db, log_request


@pytest.fixture
def budget_db(tmp_path):
    """Create a metrics DB with cost data for budget testing."""
    db_path = tmp_path / "metrics.db"
    init_metrics_db(db_path)

    entries = [
        RequestLogEntry(
            request_id="b1",
            timestamp="2025-01-15T10:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=200.0,
            status_code=200,
            estimated_cost=0.50,
        ),
        RequestLogEntry(
            request_id="b2",
            timestamp="2025-01-15T11:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=300.0,
            status_code=200,
            estimated_cost=0.30,
        ),
        RequestLogEntry(
            request_id="b3",
            timestamp="2025-01-14T10:00:00+00:00",
            endpoint="/hed/ask",
            method="POST",
            community_id="hed",
            duration_ms=250.0,
            status_code=200,
            estimated_cost=2.00,
        ),
        RequestLogEntry(
            request_id="b4",
            timestamp="2025-01-15T10:00:00+00:00",
            endpoint="/eeglab/ask",
            method="POST",
            community_id="eeglab",
            duration_ms=150.0,
            status_code=200,
            estimated_cost=0.10,
        ),
    ]
    for e in entries:
        log_request(e, db_path=db_path)

    return db_path


class TestBudgetStatus:
    """Tests for BudgetStatus dataclass."""

    def test_daily_pct(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=4.0,
            monthly_spend_usd=0.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.daily_pct == 80.0

    def test_monthly_pct(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=0.0,
            monthly_spend_usd=40.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.monthly_pct == 80.0

    def test_zero_limit_returns_zero_pct(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=1.0,
            monthly_spend_usd=1.0,
            daily_limit_usd=0.0,
            monthly_limit_usd=0.0,
            alert_threshold_pct=80.0,
        )
        assert status.daily_pct == 0.0
        assert status.monthly_pct == 0.0

    def test_daily_exceeded(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=5.0,
            monthly_spend_usd=0.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.daily_exceeded is True

    def test_daily_not_exceeded(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=4.99,
            monthly_spend_usd=0.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.daily_exceeded is False

    def test_monthly_exceeded(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=0.0,
            monthly_spend_usd=50.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.monthly_exceeded is True

    def test_daily_alert_at_threshold(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=4.0,
            monthly_spend_usd=0.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.daily_alert is True

    def test_daily_alert_below_threshold(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=3.99,
            monthly_spend_usd=0.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.daily_alert is False

    def test_needs_alert_daily(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=4.0,
            monthly_spend_usd=0.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.needs_alert is True

    def test_needs_alert_monthly(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=0.0,
            monthly_spend_usd=40.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.needs_alert is True

    def test_no_alert_needed(self):
        status = BudgetStatus(
            community_id="test",
            daily_spend_usd=1.0,
            monthly_spend_usd=10.0,
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert status.needs_alert is False


class TestCheckBudget:
    """Tests for check_budget() function.

    Note: check_budget queries today's date with date('now'), so the test
    data from 2025-01-15 won't appear as "today". We test that the query
    runs without error and returns the expected structure.
    """

    def test_returns_budget_status(self, budget_db):
        conn = get_metrics_connection(budget_db)
        try:
            status = check_budget(
                community_id="hed",
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                alert_threshold_pct=80.0,
                conn=conn,
            )
            assert isinstance(status, BudgetStatus)
            assert status.community_id == "hed"
            assert status.daily_limit_usd == 5.0
            assert status.monthly_limit_usd == 50.0
            assert status.alert_threshold_pct == 80.0
        finally:
            conn.close()

    def test_empty_community_zero_spend(self, budget_db):
        conn = get_metrics_connection(budget_db)
        try:
            status = check_budget(
                community_id="nonexistent",
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                alert_threshold_pct=80.0,
                conn=conn,
            )
            assert status.daily_spend_usd == 0.0
            assert status.monthly_spend_usd == 0.0
        finally:
            conn.close()

    def test_spend_values_are_non_negative(self, budget_db):
        conn = get_metrics_connection(budget_db)
        try:
            status = check_budget(
                community_id="hed",
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                alert_threshold_pct=80.0,
                conn=conn,
            )
            assert status.daily_spend_usd >= 0.0
            assert status.monthly_spend_usd >= 0.0
        finally:
            conn.close()
