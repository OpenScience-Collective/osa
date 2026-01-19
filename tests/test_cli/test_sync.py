"""Tests for sync CLI commands.

Tests cover:
- Admin access requirement (API_KEYS check)
- Community option validation
- Dynamic repository lookup from registry
- Paper query lookup from registry
"""

import os
from unittest.mock import patch

from typer.testing import CliRunner

from src.assistants import discover_assistants, registry
from src.cli.main import cli

# Discover assistants to populate registry
discover_assistants()

runner = CliRunner()


class TestAdminAccessCheck:
    """Tests for admin access requirement on sync commands."""

    def test_sync_init_requires_api_keys(self) -> None:
        """sync init should fail without API_KEYS."""
        with patch.dict(os.environ, {"API_KEYS": ""}, clear=False):
            # Make sure API_KEYS is not set
            if "API_KEYS" in os.environ:
                del os.environ["API_KEYS"]

            result = runner.invoke(cli, ["sync", "init"])
            assert result.exit_code == 1
            assert "API_KEYS required" in result.output

    def test_sync_github_requires_api_keys(self) -> None:
        """sync github should fail without API_KEYS."""
        with patch.dict(os.environ, {}, clear=False):
            if "API_KEYS" in os.environ:
                del os.environ["API_KEYS"]

            result = runner.invoke(cli, ["sync", "github"])
            assert result.exit_code == 1
            assert "API_KEYS required" in result.output

    def test_sync_papers_requires_api_keys(self) -> None:
        """sync papers should fail without API_KEYS."""
        with patch.dict(os.environ, {}, clear=False):
            if "API_KEYS" in os.environ:
                del os.environ["API_KEYS"]

            result = runner.invoke(cli, ["sync", "papers"])
            assert result.exit_code == 1
            assert "API_KEYS required" in result.output

    def test_sync_all_requires_api_keys(self) -> None:
        """sync all should fail without API_KEYS."""
        with patch.dict(os.environ, {}, clear=False):
            if "API_KEYS" in os.environ:
                del os.environ["API_KEYS"]

            result = runner.invoke(cli, ["sync", "all"])
            assert result.exit_code == 1
            assert "API_KEYS required" in result.output

    def test_sync_status_does_not_require_api_keys(self) -> None:
        """sync status should work without API_KEYS (read-only)."""
        with patch.dict(os.environ, {}, clear=False):
            if "API_KEYS" in os.environ:
                del os.environ["API_KEYS"]

            result = runner.invoke(cli, ["sync", "status"])
            # Should not fail due to missing API_KEYS
            # (may fail due to missing DB, which is fine)
            assert "API_KEYS required" not in result.output

    def test_sync_search_does_not_require_api_keys(self) -> None:
        """sync search should work without API_KEYS (read-only)."""
        with patch.dict(os.environ, {}, clear=False):
            if "API_KEYS" in os.environ:
                del os.environ["API_KEYS"]

            result = runner.invoke(cli, ["sync", "search", "test"])
            # Should not fail due to missing API_KEYS
            assert "API_KEYS required" not in result.output


class TestCommunityValidation:
    """Tests for community option validation."""

    def test_sync_github_rejects_unknown_community(self) -> None:
        """sync github should reject unknown community ID."""
        with patch.dict(os.environ, {"API_KEYS": "test-key"}, clear=False):
            result = runner.invoke(cli, ["sync", "github", "--community", "nonexistent"])
            assert result.exit_code == 1
            assert "Unknown community" in result.output
            assert "nonexistent" in result.output

    def test_sync_papers_rejects_unknown_community(self) -> None:
        """sync papers should reject unknown community ID."""
        with patch.dict(os.environ, {"API_KEYS": "test-key"}, clear=False):
            result = runner.invoke(cli, ["sync", "papers", "--community", "nonexistent"])
            assert result.exit_code == 1
            assert "Unknown community" in result.output

    def test_sync_all_rejects_unknown_community(self) -> None:
        """sync all should reject unknown community ID."""
        with patch.dict(os.environ, {"API_KEYS": "test-key"}, clear=False):
            result = runner.invoke(cli, ["sync", "all", "--community", "nonexistent"])
            assert result.exit_code == 1
            assert "Unknown community" in result.output

    def test_sync_search_rejects_unknown_community(self) -> None:
        """sync search should reject unknown community ID."""
        result = runner.invoke(cli, ["sync", "search", "test", "--community", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown community" in result.output

    def test_sync_shows_available_communities(self) -> None:
        """Error message should show available community IDs."""
        with patch.dict(os.environ, {"API_KEYS": "test-key"}, clear=False):
            result = runner.invoke(cli, ["sync", "github", "--community", "nonexistent"])
            assert "Available communities:" in result.output
            # HED should be in the list since it's registered
            assert "hed" in result.output


class TestRegistryIntegration:
    """Tests for registry integration in sync commands."""

    def test_hed_community_is_registered(self) -> None:
        """HED community should be registered and available."""
        info = registry.get("hed")
        assert info is not None
        assert info.id == "hed"

    def test_hed_has_github_repos(self) -> None:
        """HED community should have GitHub repos configured."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert info.community_config.github is not None
        assert len(info.community_config.github.repos) > 0

    def test_hed_has_paper_queries(self) -> None:
        """HED community should have paper queries configured."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert info.community_config.citations is not None
        assert len(info.community_config.citations.queries) > 0

    def test_hed_has_paper_dois(self) -> None:
        """HED community should have DOIs for citation tracking."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert info.community_config.citations is not None
        assert len(info.community_config.citations.dois) > 0


class TestSyncHelp:
    """Tests for sync command help text."""

    def test_sync_help_shows_commands(self) -> None:
        """sync --help should show all subcommands."""
        result = runner.invoke(cli, ["sync", "--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "github" in result.output
        assert "papers" in result.output
        assert "all" in result.output
        assert "status" in result.output
        assert "search" in result.output

    def test_sync_github_help_shows_community_option(self) -> None:
        """sync github --help should show --community option."""
        result = runner.invoke(cli, ["sync", "github", "--help"])
        assert result.exit_code == 0
        assert "--community" in result.output

    def test_sync_papers_help_shows_community_option(self) -> None:
        """sync papers --help should show --community option."""
        result = runner.invoke(cli, ["sync", "papers", "--help"])
        assert result.exit_code == 0
        assert "--community" in result.output

    def test_sync_all_help_shows_community_option(self) -> None:
        """sync all --help should show --community option."""
        result = runner.invoke(cli, ["sync", "all", "--help"])
        assert result.exit_code == 0
        assert "--community" in result.output
