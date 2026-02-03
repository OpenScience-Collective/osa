"""Budget checking for community cost management.

Queries the metrics database for current spend and compares against
configured budget limits.
"""

import logging
import sqlite3
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BudgetStatus:
    """Result of a budget check for a community."""

    community_id: str
    daily_spend_usd: float
    monthly_spend_usd: float
    daily_limit_usd: float
    monthly_limit_usd: float
    alert_threshold_pct: float

    @property
    def daily_pct(self) -> float:
        """Daily spend as percentage of limit."""
        if self.daily_limit_usd <= 0:
            return 0.0
        return (self.daily_spend_usd / self.daily_limit_usd) * 100

    @property
    def monthly_pct(self) -> float:
        """Monthly spend as percentage of limit."""
        if self.monthly_limit_usd <= 0:
            return 0.0
        return (self.monthly_spend_usd / self.monthly_limit_usd) * 100

    @property
    def daily_exceeded(self) -> bool:
        """Whether daily spend has reached or exceeded the daily limit."""
        return self.daily_spend_usd >= self.daily_limit_usd

    @property
    def monthly_exceeded(self) -> bool:
        """Whether monthly spend has reached or exceeded the monthly limit."""
        return self.monthly_spend_usd >= self.monthly_limit_usd

    @property
    def daily_alert(self) -> bool:
        """Whether daily spend crossed the alert threshold."""
        return self.daily_pct >= self.alert_threshold_pct

    @property
    def monthly_alert(self) -> bool:
        """Whether monthly spend crossed the alert threshold."""
        return self.monthly_pct >= self.alert_threshold_pct

    @property
    def needs_alert(self) -> bool:
        """Whether any alert threshold has been crossed."""
        return self.daily_alert or self.monthly_alert


def check_budget(
    community_id: str,
    daily_limit_usd: float,
    monthly_limit_usd: float,
    alert_threshold_pct: float,
    conn: sqlite3.Connection,
) -> BudgetStatus:
    """Check current spend against budget limits.

    Queries estimated_cost from request_log for today and current month.

    Args:
        community_id: The community identifier.
        daily_limit_usd: Maximum daily spend.
        monthly_limit_usd: Maximum monthly spend.
        alert_threshold_pct: Alert threshold percentage.
        conn: SQLite connection.

    Returns:
        BudgetStatus with current spend and limit info.
    """
    # Daily spend (today UTC)
    daily_row = conn.execute(
        """
        SELECT COALESCE(SUM(estimated_cost), 0) as spend
        FROM request_log
        WHERE community_id = ?
          AND date(timestamp) = date('now')
        """,
        (community_id,),
    ).fetchone()

    # Monthly spend (current month UTC)
    monthly_row = conn.execute(
        """
        SELECT COALESCE(SUM(estimated_cost), 0) as spend
        FROM request_log
        WHERE community_id = ?
          AND strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
        """,
        (community_id,),
    ).fetchone()

    return BudgetStatus(
        community_id=community_id,
        daily_spend_usd=round(daily_row["spend"], 6),
        monthly_spend_usd=round(monthly_row["spend"], 6),
        daily_limit_usd=daily_limit_usd,
        monthly_limit_usd=monthly_limit_usd,
        alert_threshold_pct=alert_threshold_pct,
    )
