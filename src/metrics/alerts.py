"""GitHub issue alerting for budget and operational alerts.

Creates GitHub issues when budget thresholds are exceeded,
with deduplication to avoid spamming.
"""

import json
import logging
import subprocess

from src.metrics.budget import BudgetStatus

logger = logging.getLogger(__name__)

# GitHub repo for alert issues (org/repo format)
ALERT_REPO = "OpenScience-Collective/osa"


def _issue_exists(title: str, repo: str = ALERT_REPO) -> bool:
    """Check if an open issue with this title already exists.

    Uses gh CLI to search for existing issues to prevent duplicates.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--search",
                title,
                "--json",
                "title",
                "--limit",
                "5",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error("gh issue list failed: %s", result.stderr)
            return True  # Conservative: assume duplicate exists to prevent spam

        issues = json.loads(result.stdout)
        return any(issue.get("title") == title for issue in issues)
    except Exception:
        logger.exception("Failed to check existing issues")
        return True  # Conservative: assume duplicate exists to prevent spam


def create_budget_alert_issue(
    budget_status: BudgetStatus,
    maintainers: list[str],
    repo: str = ALERT_REPO,
) -> str | None:
    """Create a GitHub issue for a budget alert.

    Includes deduplication: checks for existing open issues with the same
    title before creating a new one.

    Args:
        budget_status: The budget check result with spend/limit data.
        maintainers: GitHub usernames to @mention in the issue body.
        repo: GitHub repository in org/repo format.

    Returns:
        The issue URL if created, None if skipped (duplicate or error).
    """
    # Determine alert type
    alert_parts = []
    if budget_status.daily_exceeded:
        alert_parts.append("daily limit exceeded")
    elif budget_status.daily_alert:
        alert_parts.append(f"daily spend at {budget_status.daily_pct:.0f}%")
    if budget_status.monthly_exceeded:
        alert_parts.append("monthly limit exceeded")
    elif budget_status.monthly_alert:
        alert_parts.append(f"monthly spend at {budget_status.monthly_pct:.0f}%")

    if not alert_parts:
        return None

    alert_type = ", ".join(alert_parts)
    title = f"[Budget Alert] {budget_status.community_id}: {alert_type}"

    # Check for existing open issue
    if _issue_exists(title, repo):
        logger.info(
            "Budget alert issue already exists for %s, skipping", budget_status.community_id
        )
        return None

    # Build issue body
    mentions = (
        " ".join(f"@{m}" for m in maintainers) if maintainers else "No maintainers configured"
    )

    body = f"""## Budget Alert for `{budget_status.community_id}`

**Alert:** {alert_type}

### Current Spend

| Metric | Spend | Limit | Usage |
|--------|-------|-------|-------|
| Daily | ${budget_status.daily_spend_usd:.4f} | ${budget_status.daily_limit_usd:.2f} | {budget_status.daily_pct:.1f}% |
| Monthly | ${budget_status.monthly_spend_usd:.4f} | ${budget_status.monthly_limit_usd:.2f} | {budget_status.monthly_pct:.1f}% |

### Alert Threshold
Configured at {budget_status.alert_threshold_pct:.0f}% of limits.

### Maintainers
{mentions}

---
*This issue was created automatically by the OSA budget monitoring system.*
"""

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                title,
                "--body",
                body,
                "--label",
                "cost-management,operations",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error("Failed to create budget alert issue: %s", result.stderr)
            return None

        issue_url = result.stdout.strip()
        logger.info("Created budget alert issue: %s", issue_url)
        return issue_url
    except Exception:
        logger.exception("Failed to create budget alert issue for %s", budget_status.community_id)
        return None
