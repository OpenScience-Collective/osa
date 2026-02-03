"""Tests for budget alert issue creation."""

from unittest.mock import patch

from src.metrics.alerts import create_budget_alert_issue
from src.metrics.budget import BudgetStatus


def _make_budget_status(
    community_id: str = "hed",
    daily_spend: float = 4.5,
    monthly_spend: float = 45.0,
    daily_limit: float = 5.0,
    monthly_limit: float = 50.0,
    alert_pct: float = 80.0,
) -> BudgetStatus:
    return BudgetStatus(
        community_id=community_id,
        daily_spend_usd=daily_spend,
        monthly_spend_usd=monthly_spend,
        daily_limit_usd=daily_limit,
        monthly_limit_usd=monthly_limit,
        alert_threshold_pct=alert_pct,
    )


class TestCreateBudgetAlertIssue:
    """Tests for create_budget_alert_issue()."""

    def test_no_alert_when_no_threshold_crossed(self):
        """Returns None when no alert condition is met."""
        status = _make_budget_status(daily_spend=1.0, monthly_spend=10.0)
        result = create_budget_alert_issue(status, maintainers=["user1"])
        assert result is None

    @patch("src.metrics.alerts._issue_exists", return_value=True)
    @patch("src.metrics.alerts.subprocess")
    def test_skips_duplicate_issue(self, mock_subprocess, _mock_exists):
        """Returns None when issue already exists."""
        status = _make_budget_status()
        result = create_budget_alert_issue(status, maintainers=["user1"])
        assert result is None
        mock_subprocess.run.assert_not_called()

    @patch("src.metrics.alerts._issue_exists", return_value=False)
    @patch("src.metrics.alerts.subprocess")
    def test_creates_issue_for_daily_alert(self, mock_subprocess, _mock_exists):
        """Creates issue when daily alert threshold crossed."""
        mock_subprocess.run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "https://github.com/test/issues/1\n", "stderr": ""},
        )()

        status = _make_budget_status(daily_spend=4.5, monthly_spend=10.0)
        result = create_budget_alert_issue(status, maintainers=["user1"])
        assert result == "https://github.com/test/issues/1"

        # Verify subprocess.run was called with gh issue create
        call_args = mock_subprocess.run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "gh"
        assert cmd[1] == "issue"
        assert cmd[2] == "create"

    @patch("src.metrics.alerts._issue_exists", return_value=False)
    @patch("src.metrics.alerts.subprocess")
    def test_creates_issue_for_monthly_exceeded(self, mock_subprocess, _mock_exists):
        """Creates issue when monthly limit exceeded."""
        mock_subprocess.run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "https://github.com/test/issues/2\n", "stderr": ""},
        )()

        status = _make_budget_status(daily_spend=1.0, monthly_spend=50.0)
        result = create_budget_alert_issue(status, maintainers=["user1"])
        assert result == "https://github.com/test/issues/2"

    @patch("src.metrics.alerts._issue_exists", return_value=False)
    @patch("src.metrics.alerts.subprocess")
    def test_issue_body_contains_maintainer_mentions(self, mock_subprocess, _mock_exists):
        """Issue body includes @mentions for maintainers."""
        mock_subprocess.run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "https://github.com/test/issues/3\n", "stderr": ""},
        )()

        status = _make_budget_status()
        create_budget_alert_issue(status, maintainers=["VisLab", "yarikoptic"])

        call_args = mock_subprocess.run.call_args
        cmd = call_args[0][0]
        # Find the --body argument
        body_idx = cmd.index("--body") + 1
        body = cmd[body_idx]
        assert "@VisLab" in body
        assert "@yarikoptic" in body

    @patch("src.metrics.alerts._issue_exists", return_value=False)
    @patch("src.metrics.alerts.subprocess")
    def test_issue_title_format(self, mock_subprocess, _mock_exists):
        """Issue title follows expected format."""
        mock_subprocess.run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "https://github.com/test/issues/4\n", "stderr": ""},
        )()

        status = _make_budget_status(daily_spend=5.0)
        create_budget_alert_issue(status, maintainers=[])

        call_args = mock_subprocess.run.call_args
        cmd = call_args[0][0]
        title_idx = cmd.index("--title") + 1
        title = cmd[title_idx]
        assert title.startswith("[Budget Alert] hed:")

    @patch("src.metrics.alerts._issue_exists", return_value=False)
    @patch("src.metrics.alerts.subprocess")
    def test_issue_has_labels(self, mock_subprocess, _mock_exists):
        """Issue is created with cost-management and operations labels."""
        mock_subprocess.run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "https://github.com/test/issues/5\n", "stderr": ""},
        )()

        status = _make_budget_status()
        create_budget_alert_issue(status, maintainers=[])

        call_args = mock_subprocess.run.call_args
        cmd = call_args[0][0]
        label_idx = cmd.index("--label") + 1
        labels = cmd[label_idx]
        assert "cost-management" in labels
        assert "operations" in labels

    @patch("src.metrics.alerts._issue_exists", return_value=False)
    @patch("src.metrics.alerts.subprocess")
    def test_returns_none_on_gh_failure(self, mock_subprocess, _mock_exists):
        """Returns None when gh CLI fails."""
        mock_subprocess.run.return_value = type(
            "Result", (), {"returncode": 1, "stdout": "", "stderr": "auth required"}
        )()

        status = _make_budget_status()
        result = create_budget_alert_issue(status, maintainers=[])
        assert result is None

    @patch("src.metrics.alerts._issue_exists", return_value=False)
    @patch("src.metrics.alerts.subprocess")
    def test_no_maintainers_message(self, mock_subprocess, _mock_exists):
        """Body shows 'No maintainers configured' when list is empty."""
        mock_subprocess.run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "https://github.com/test/issues/6\n", "stderr": ""},
        )()

        status = _make_budget_status()
        create_budget_alert_issue(status, maintainers=[])

        call_args = mock_subprocess.run.call_args
        cmd = call_args[0][0]
        body_idx = cmd.index("--body") + 1
        body = cmd[body_idx]
        assert "No maintainers configured" in body
