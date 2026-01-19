"""Tests for sync CLI commands.

Tests cover:
- Admin access requirement (API_KEYS check)
- Community option validation
- Dynamic repository lookup from registry
- Paper query lookup from registry
- Citation sync functionality
"""

import os
import re
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from src.assistants import discover_assistants, registry
from src.cli.main import cli

# Discover assistants to populate registry
discover_assistants()

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text for reliable assertion matching."""
    ansi_pattern = re.compile(r"\x1b\[[0-9;]*m")
    return ansi_pattern.sub("", text)


@pytest.fixture
def _no_api_keys():
    """Remove API_KEYS from environment for testing admin access."""
    with patch.dict(os.environ, {}, clear=False):
        if "API_KEYS" in os.environ:
            del os.environ["API_KEYS"]
        yield


@pytest.fixture
def _with_api_keys():
    """Set API_KEYS in environment for testing admin commands."""
    with patch.dict(os.environ, {"API_KEYS": "test-key"}, clear=False):
        yield


class TestAdminAccessCheck:
    """Tests for admin access requirement on sync commands."""

    def test_sync_init_requires_api_keys(self, _no_api_keys) -> None:
        """sync init should fail without API_KEYS."""
        result = runner.invoke(cli, ["sync", "init"])
        assert result.exit_code == 1
        assert "API_KEYS required" in result.output

    def test_sync_github_requires_api_keys(self, _no_api_keys) -> None:
        """sync github should fail without API_KEYS."""
        result = runner.invoke(cli, ["sync", "github"])
        assert result.exit_code == 1
        assert "API_KEYS required" in result.output

    def test_sync_papers_requires_api_keys(self, _no_api_keys) -> None:
        """sync papers should fail without API_KEYS."""
        result = runner.invoke(cli, ["sync", "papers"])
        assert result.exit_code == 1
        assert "API_KEYS required" in result.output

    def test_sync_all_requires_api_keys(self, _no_api_keys) -> None:
        """sync all should fail without API_KEYS."""
        result = runner.invoke(cli, ["sync", "all"])
        assert result.exit_code == 1
        assert "API_KEYS required" in result.output

    def test_sync_status_does_not_require_api_keys(self, _no_api_keys) -> None:
        """sync status should work without API_KEYS (read-only)."""
        result = runner.invoke(cli, ["sync", "status"])
        # Should not fail due to missing API_KEYS
        # (may fail due to missing DB, which is fine)
        assert "API_KEYS required" not in result.output

    def test_sync_search_does_not_require_api_keys(self, _no_api_keys) -> None:
        """sync search should work without API_KEYS (read-only)."""
        result = runner.invoke(cli, ["sync", "search", "test"])
        # Should not fail due to missing API_KEYS
        assert "API_KEYS required" not in result.output


class TestCommunityValidation:
    """Tests for community option validation."""

    def test_sync_init_rejects_unknown_community(self, _with_api_keys) -> None:
        """sync init should reject unknown community ID."""
        result = runner.invoke(cli, ["sync", "init", "--community", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown community" in result.output
        assert "nonexistent" in result.output

    def test_sync_github_rejects_unknown_community(self, _with_api_keys) -> None:
        """sync github should reject unknown community ID."""
        result = runner.invoke(cli, ["sync", "github", "--community", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown community" in result.output
        assert "nonexistent" in result.output

    def test_sync_papers_rejects_unknown_community(self, _with_api_keys) -> None:
        """sync papers should reject unknown community ID."""
        result = runner.invoke(cli, ["sync", "papers", "--community", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown community" in result.output

    def test_sync_all_rejects_unknown_community(self, _with_api_keys) -> None:
        """sync all should reject unknown community ID."""
        result = runner.invoke(cli, ["sync", "all", "--community", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown community" in result.output

    def test_sync_search_rejects_unknown_community(self) -> None:
        """sync search should reject unknown community ID."""
        result = runner.invoke(cli, ["sync", "search", "test", "--community", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown community" in result.output

    def test_sync_status_rejects_unknown_community(self) -> None:
        """sync status should reject unknown community ID."""
        result = runner.invoke(cli, ["sync", "status", "--community", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown community" in result.output

    def test_sync_shows_available_communities(self, _with_api_keys) -> None:
        """Error message should show available community IDs."""
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
        output = strip_ansi(result.output)
        assert "init" in output
        assert "github" in output
        assert "papers" in output
        assert "all" in output
        assert "status" in output
        assert "search" in output

    def test_sync_github_help_shows_community_option(self) -> None:
        """sync github --help should show --community option."""
        result = runner.invoke(cli, ["sync", "github", "--help"])
        assert result.exit_code == 0
        assert "--community" in strip_ansi(result.output)

    def test_sync_papers_help_shows_community_option(self) -> None:
        """sync papers --help should show --community option."""
        result = runner.invoke(cli, ["sync", "papers", "--help"])
        assert result.exit_code == 0
        assert "--community" in strip_ansi(result.output)

    def test_sync_all_help_shows_community_option(self) -> None:
        """sync all --help should show --community option."""
        result = runner.invoke(cli, ["sync", "all", "--help"])
        assert result.exit_code == 0
        assert "--community" in strip_ansi(result.output)

    def test_sync_papers_help_shows_citations_option(self) -> None:
        """sync papers --help should show --citations option."""
        result = runner.invoke(cli, ["sync", "papers", "--help"])
        assert result.exit_code == 0
        assert "--citations" in strip_ansi(result.output)

    def test_sync_all_help_shows_limit_option(self) -> None:
        """sync all --help should show --limit option."""
        result = runner.invoke(cli, ["sync", "all", "--help"])
        assert result.exit_code == 0
        assert "--limit" in strip_ansi(result.output)


class TestPapersSync:
    """Tests for papers sync functionality."""

    def test_sync_all_papers_returns_zero_with_empty_queries(self) -> None:
        """sync_all_papers should return zeros when no queries provided."""
        from src.knowledge.papers_sync import sync_all_papers

        # Empty queries should return zeros for all sources
        results = sync_all_papers(queries=[], project="test_empty")
        assert results == {"openalex": 0, "semanticscholar": 0, "pubmed": 0}

    def test_sync_all_papers_returns_zero_with_none_queries(self) -> None:
        """sync_all_papers should return zeros when queries is None."""
        from src.knowledge.papers_sync import sync_all_papers

        # None queries should return zeros for all sources
        results = sync_all_papers(queries=None, project="test_none")
        assert results == {"openalex": 0, "semanticscholar": 0, "pubmed": 0}


class TestCitationsSync:
    """Tests for citation tracking functionality."""

    def test_sync_citing_papers_function_exists(self) -> None:
        """sync_citing_papers should be importable and callable."""
        from src.knowledge.papers_sync import sync_citing_papers

        # Function should be callable
        assert callable(sync_citing_papers)

    def test_sync_citing_papers_returns_zero_for_empty_dois(self) -> None:
        """sync_citing_papers should return 0 for empty DOI list."""
        from src.knowledge.papers_sync import sync_citing_papers

        result = sync_citing_papers(dois=[], project="test_citations")
        assert result == 0

    def test_hed_has_dois_for_citation_tracking(self) -> None:
        """HED community should have DOIs configured for citation tracking."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert info.community_config.citations is not None
        dois = info.community_config.citations.dois
        assert len(dois) > 0
        # DOIs should be in bare format (no https://doi.org/ prefix)
        for doi in dois:
            assert not doi.startswith("http"), f"DOI should be bare format: {doi}"
            assert "/" in doi, f"DOI should contain a slash: {doi}"


class TestSyncOptions:
    """Tests for sync command options."""

    def test_sync_papers_citations_flag_in_help(self) -> None:
        """--citations flag should be documented in help."""
        result = runner.invoke(cli, ["sync", "papers", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "--citations" in output
        # Help should mention what the flag does
        assert "citing" in output.lower() or "DOI" in output

    def test_sync_all_limit_flag_in_help(self) -> None:
        """--limit flag should be documented in help."""
        result = runner.invoke(cli, ["sync", "all", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "--limit" in output
        # Help should mention what the flag does
        assert "max" in output.lower() or "limit" in output.lower()
