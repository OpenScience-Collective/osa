"""Tests for budget checking."""

import pytest

from src.core.config.community import BudgetConfig
from src.metrics.budget import BudgetStatus, check_budget
from src.metrics.db import (
    RequestLogEntry,
    get_metrics_connection,
    init_metrics_db,
    log_request,
    now_iso,
)


def _make_config(
    daily: float = 5.0,
    monthly: float = 50.0,
    alert_pct: float = 80.0,
) -> BudgetConfig:
    return BudgetConfig(
        daily_limit_usd=daily,
        monthly_limit_usd=monthly,
        alert_threshold_pct=alert_pct,
    )


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

    def test_rejects_negative_daily_spend(self):
        with pytest.raises(ValueError, match="daily_spend_usd must be non-negative"):
            BudgetStatus(
                community_id="test",
                daily_spend_usd=-1.0,
                monthly_spend_usd=0.0,
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                alert_threshold_pct=80.0,
            )

    def test_rejects_negative_monthly_spend(self):
        with pytest.raises(ValueError, match="monthly_spend_usd must be non-negative"):
            BudgetStatus(
                community_id="test",
                daily_spend_usd=0.0,
                monthly_spend_usd=-1.0,
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                alert_threshold_pct=80.0,
            )


class TestCheckBudget:
    """Tests for check_budget() function."""

    def test_returns_budget_status(self, budget_db):
        config = _make_config()
        conn = get_metrics_connection(budget_db)
        try:
            status = check_budget(community_id="hed", config=config, conn=conn)
            assert isinstance(status, BudgetStatus)
            assert status.community_id == "hed"
            assert status.daily_limit_usd == 5.0
            assert status.monthly_limit_usd == 50.0
            assert status.alert_threshold_pct == 80.0
        finally:
            conn.close()

    def test_empty_community_zero_spend(self, budget_db):
        config = _make_config()
        conn = get_metrics_connection(budget_db)
        try:
            status = check_budget(community_id="nonexistent", config=config, conn=conn)
            assert status.daily_spend_usd == 0.0
            assert status.monthly_spend_usd == 0.0
        finally:
            conn.close()

    def test_spend_values_are_non_negative(self, budget_db):
        config = _make_config()
        conn = get_metrics_connection(budget_db)
        try:
            status = check_budget(community_id="hed", config=config, conn=conn)
            assert status.daily_spend_usd >= 0.0
            assert status.monthly_spend_usd >= 0.0
        finally:
            conn.close()

    def test_today_spend_with_current_timestamps(self, tmp_path):
        """Verify check_budget sums costs for entries with today's timestamp."""
        db_path = tmp_path / "today.db"
        init_metrics_db(db_path)

        # Insert entries with current timestamps
        for i, cost in enumerate([0.25, 0.35, 0.40]):
            log_request(
                RequestLogEntry(
                    request_id=f"today-{i}",
                    timestamp=now_iso(),
                    endpoint="/hed/ask",
                    method="POST",
                    community_id="hed",
                    status_code=200,
                    estimated_cost=cost,
                ),
                db_path=db_path,
            )

        config = _make_config()
        conn = get_metrics_connection(db_path)
        try:
            status = check_budget(community_id="hed", config=config, conn=conn)
            assert status.daily_spend_usd == pytest.approx(1.0, abs=1e-6)
            assert status.monthly_spend_usd == pytest.approx(1.0, abs=1e-6)
        finally:
            conn.close()

    def test_today_spend_triggers_alert(self, tmp_path):
        """Verify budget alert triggers when today's spend crosses threshold."""
        db_path = tmp_path / "alert.db"
        init_metrics_db(db_path)

        log_request(
            RequestLogEntry(
                request_id="expensive",
                timestamp=now_iso(),
                endpoint="/hed/ask",
                method="POST",
                community_id="hed",
                status_code=200,
                estimated_cost=4.5,
            ),
            db_path=db_path,
        )

        config = _make_config(daily=5.0, monthly=50.0, alert_pct=80.0)
        conn = get_metrics_connection(db_path)
        try:
            status = check_budget(community_id="hed", config=config, conn=conn)
            assert status.daily_pct == 90.0
            assert status.daily_alert is True
            assert status.needs_alert is True
        finally:
            conn.close()
