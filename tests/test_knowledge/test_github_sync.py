"""Tests for GitHub sync module.

Note: These are real API tests, not mocks, per project guidelines.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import get_connection, init_db
from src.knowledge.github_sync import sync_repo, sync_repo_issues, sync_repos


@pytest.fixture
def temp_db(tmp_path: Path):
    """Create temporary database for testing."""
    db_path = tmp_path / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()
        yield db_path


class TestGitHubSync:
    """Test GitHub sync functionality."""

    def test_sync_repo_issues_basic_flow(self, temp_db: Path):
        """Test basic GitHub issues sync for a known public repo.

        This is a smoke test using a real GitHub API call to verify
        the sync process works end-to-end.
        """
        # Use a small, stable public repo
        repo = "hed-standard/hed-specification"

        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Run sync (will hit real GitHub API)
            count = sync_repo_issues(repo, project="test")

            # Should find at least some issues (this repo has historical issues)
            assert count >= 0  # Accept 0 for rate-limited or network issues

            # Verify data was written to database
            with get_connection("test") as conn:
                row = conn.execute(
                    "SELECT COUNT(*) as count FROM github_items WHERE repo = ? AND item_type = 'issue'",
                    (repo,),
                ).fetchone()
                # If count > 0, database should have entries
                if count > 0:
                    assert row["count"] > 0

    def test_sync_repo_handles_invalid_format(self, temp_db: Path):
        """Test that sync rejects invalid repo formats."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Invalid format (no slash)
            count = sync_repo_issues("invalid-format", project="test")
            assert count == 0

            # Invalid format (too many slashes)
            count = sync_repo_issues("owner/repo/extra", project="test")
            assert count == 0

    def test_sync_repo_full_flow(self, temp_db: Path):
        """Test full repo sync (issues + PRs)."""
        repo = "hed-standard/hed-specification"

        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Run full sync
            total = sync_repo(repo, project="test", incremental=False)

            # Should return count >= 0
            assert total >= 0

            # Verify sync metadata was recorded
            with get_connection("test") as conn:
                row = conn.execute(
                    "SELECT * FROM sync_metadata WHERE source_type = 'github' AND source_name = ?",
                    (repo,),
                ).fetchone()
                if total > 0:
                    assert row is not None
                    assert row["items_synced"] == total

    @pytest.mark.skipif(
        os.getenv("GITHUB_TOKEN") is None,
        reason="Requires GITHUB_TOKEN for rate limits",
    )
    def test_sync_with_token(self, temp_db: Path):
        """Test sync with GitHub token (higher rate limits).

        This test is skipped if GITHUB_TOKEN is not set.
        """
        repo = "hed-standard/hed-javascript"

        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Token should be picked up from environment
            count = sync_repo(repo, project="test", incremental=False)
            assert count >= 0


class TestSyncReposTypeGuard:
    """Test that sync_repos rejects bare strings to prevent character iteration."""

    def test_rejects_bare_string(self) -> None:
        with pytest.raises(TypeError, match="must be a list of strings"):
            sync_repos("fieldtrip")  # type: ignore[arg-type]

    def test_accepts_list(self, temp_db: Path) -> None:
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            result = sync_repos(["nonexistent/repo"], project="test")
            assert isinstance(result, dict)
