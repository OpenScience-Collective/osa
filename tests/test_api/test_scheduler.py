"""Tests for background scheduler and multi-community sync support.

These tests ensure the scheduler correctly handles multiple communities,
iterating over all registered communities rather than being hardcoded to HED.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.api.scheduler import (
    _get_communities_with_sync,
    _get_community_paper_dois,
    _get_community_paper_queries,
    _get_community_repos,
    _run_github_sync,
    _run_papers_sync,
    run_sync_now,
)


@pytest.fixture
def _mock_registry():
    """Mock registry with multiple communities for testing."""
    with patch("src.api.scheduler.registry") as mock_reg:
        # Create mock community info objects
        hed_info = MagicMock()
        hed_info.id = "hed"
        hed_info.sync_config = True
        hed_info.community_config = MagicMock()
        hed_info.community_config.github = MagicMock()
        hed_info.community_config.github.repos = [
            "hed-standard/hed-specification",
            "hed-standard/hed-python",
        ]
        hed_info.community_config.citations = MagicMock()
        hed_info.community_config.citations.queries = ["HED annotation"]
        hed_info.community_config.citations.dois = ["10.1234/hed.example"]

        bids_info = MagicMock()
        bids_info.id = "bids"
        bids_info.sync_config = True
        bids_info.community_config = MagicMock()
        bids_info.community_config.github = MagicMock()
        bids_info.community_config.github.repos = ["bids-standard/bids-specification"]
        bids_info.community_config.citations = MagicMock()
        bids_info.community_config.citations.queries = ["BIDS imaging"]
        bids_info.community_config.citations.dois = []

        no_sync_info = MagicMock()
        no_sync_info.id = "no-sync"
        no_sync_info.sync_config = False

        mock_reg.list_all.return_value = [hed_info, bids_info, no_sync_info]
        mock_reg.get.side_effect = lambda id: {
            "hed": hed_info,
            "bids": bids_info,
            "no-sync": no_sync_info,
        }.get(id)

        yield mock_reg


class TestGetCommunitiesWithSync:
    """Tests for _get_communities_with_sync helper."""

    def test_returns_only_communities_with_sync_enabled(self, _mock_registry):
        """Should return only community IDs that have sync_config=True."""
        communities = _get_communities_with_sync()
        assert communities == ["hed", "bids"]
        assert "no-sync" not in communities

    def test_returns_empty_list_when_no_sync_enabled(self):
        """Should return empty list when no communities have sync enabled."""
        with patch("src.api.scheduler.registry") as mock_reg:
            no_sync_info = MagicMock()
            no_sync_info.id = "test"
            no_sync_info.sync_config = False
            mock_reg.list_all.return_value = [no_sync_info]

            communities = _get_communities_with_sync()
            assert communities == []

    def test_handles_empty_registry(self):
        """Should return empty list when registry has no communities."""
        with patch("src.api.scheduler.registry") as mock_reg:
            mock_reg.list_all.return_value = []

            communities = _get_communities_with_sync()
            assert communities == []


class TestGetCommunityRepos:
    """Tests for _get_community_repos helper."""

    def test_returns_repos_for_valid_community(self, _mock_registry):
        """Should return GitHub repos for a registered community."""
        repos = _get_community_repos("hed")
        assert repos == ["hed-standard/hed-specification", "hed-standard/hed-python"]

    def test_returns_empty_for_community_without_github(self, _mock_registry):
        """Should return empty list for community without GitHub config."""
        # Modify bids to have no github config
        _mock_registry.get("bids").community_config.github = None

        repos = _get_community_repos("bids")
        assert repos == []

    def test_returns_empty_for_unknown_community(self, _mock_registry):
        """Should return empty list for unknown community ID."""
        _mock_registry.get.return_value = None

        repos = _get_community_repos("unknown")
        assert repos == []


class TestGetCommunityPaperQueries:
    """Tests for _get_community_paper_queries helper."""

    def test_returns_queries_for_valid_community(self, _mock_registry):
        """Should return paper queries for a registered community."""
        queries = _get_community_paper_queries("hed")
        assert queries == ["HED annotation"]

    def test_returns_empty_for_community_without_citations(self, _mock_registry):
        """Should return empty list for community without citations config."""
        _mock_registry.get("bids").community_config.citations = None

        queries = _get_community_paper_queries("bids")
        assert queries == []

    def test_returns_empty_for_unknown_community(self, _mock_registry):
        """Should return empty list for unknown community ID."""
        _mock_registry.get.return_value = None

        queries = _get_community_paper_queries("unknown")
        assert queries == []


class TestGetCommunityPaperDois:
    """Tests for _get_community_paper_dois helper."""

    def test_returns_dois_for_valid_community(self, _mock_registry):
        """Should return DOIs for a registered community."""
        dois = _get_community_paper_dois("hed")
        assert dois == ["10.1234/hed.example"]

    def test_returns_empty_for_community_without_dois(self, _mock_registry):
        """Should return empty list for community with no DOIs configured."""
        dois = _get_community_paper_dois("bids")
        assert dois == []

    def test_returns_empty_for_unknown_community(self, _mock_registry):
        """Should return empty list for unknown community ID."""
        _mock_registry.get.return_value = None

        dois = _get_community_paper_dois("unknown")
        assert dois == []


class TestRunGithubSync:
    """Tests for _run_github_sync scheduled job."""

    @patch("src.api.scheduler.sync_repos")
    @patch("src.api.scheduler.init_db")
    def test_syncs_all_communities_with_repos(self, _mock_init_db, mock_sync_repos, _mock_registry):
        """Should iterate over all communities and sync their repos."""
        mock_sync_repos.return_value = {"repo1": 5, "repo2": 3}

        _run_github_sync()

        # Should sync both HED and BIDS
        assert mock_sync_repos.call_count == 2
        mock_sync_repos.assert_any_call(
            ["hed-standard/hed-specification", "hed-standard/hed-python"],
            project="hed",
            incremental=True,
        )
        mock_sync_repos.assert_any_call(
            ["bids-standard/bids-specification"],
            project="bids",
            incremental=True,
        )

    @patch("src.api.scheduler.sync_repos")
    @patch("src.api.scheduler.init_db")
    def test_skips_communities_without_repos(self, _mock_init_db, mock_sync_repos, _mock_registry):
        """Should skip communities that have no GitHub repos configured."""
        # Remove repos from bids
        _mock_registry.get("bids").community_config.github.repos = []
        mock_sync_repos.return_value = {"repo1": 5}

        _run_github_sync()

        # Should only sync HED (bids has no repos)
        assert mock_sync_repos.call_count == 1
        mock_sync_repos.assert_called_once_with(
            ["hed-standard/hed-specification", "hed-standard/hed-python"],
            project="hed",
            incremental=True,
        )

    @patch("src.api.scheduler.sync_repos")
    @patch("src.api.scheduler.init_db")
    def test_handles_empty_communities_list(self, _mock_init_db, mock_sync_repos):
        """Should handle case where no communities have sync enabled."""
        with patch("src.api.scheduler.registry") as mock_reg:
            mock_reg.list_all.return_value = []

            _run_github_sync()

            # Should not call sync_repos
            mock_sync_repos.assert_not_called()

    @patch("src.api.scheduler.sync_repos")
    @patch("src.api.scheduler.init_db")
    def test_resets_failure_count_on_success(self, _mock_init_db, mock_sync_repos, _mock_registry):
        """Should reset failure counter after successful sync."""
        import src.api.scheduler as scheduler_module

        scheduler_module._github_sync_failures = 2
        mock_sync_repos.return_value = {"repo1": 5}

        _run_github_sync()

        assert scheduler_module._github_sync_failures == 0


class TestRunPapersSync:
    """Tests for _run_papers_sync scheduled job."""

    @patch("src.api.scheduler.sync_citing_papers")
    @patch("src.api.scheduler.sync_all_papers")
    @patch("src.api.scheduler.init_db")
    @patch("src.api.scheduler.get_settings")
    def test_syncs_all_communities_with_queries(
        self, mock_settings, _mock_init_db, mock_sync_all, mock_sync_citing, _mock_registry
    ):
        """Should iterate over all communities and sync their papers."""
        mock_settings.return_value.semantic_scholar_api_key = "test-key"
        mock_settings.return_value.pubmed_api_key = "test-key"
        mock_sync_all.return_value = {"source1": 5, "source2": 3}
        mock_sync_citing.return_value = 2

        _run_papers_sync()

        # Should sync papers for both HED and BIDS
        assert mock_sync_all.call_count == 2
        assert mock_sync_citing.call_count == 1  # Only HED has DOIs

    @patch("src.api.scheduler.sync_citing_papers")
    @patch("src.api.scheduler.sync_all_papers")
    @patch("src.api.scheduler.init_db")
    @patch("src.api.scheduler.get_settings")
    def test_syncs_citing_papers_for_communities_with_dois(
        self, mock_settings, _mock_init_db, mock_sync_all, mock_sync_citing, _mock_registry
    ):
        """Should sync citing papers only for communities with DOIs."""
        mock_settings.return_value.semantic_scholar_api_key = "test-key"
        mock_settings.return_value.pubmed_api_key = "test-key"
        mock_sync_all.return_value = {"source1": 5}
        mock_sync_citing.return_value = 2

        _run_papers_sync()

        # Should only sync citing papers for HED (has DOIs)
        mock_sync_citing.assert_called_once_with(
            ["10.1234/hed.example"],
            project="hed",
        )

    @patch("src.api.scheduler.sync_citing_papers")
    @patch("src.api.scheduler.sync_all_papers")
    @patch("src.api.scheduler.init_db")
    @patch("src.api.scheduler.get_settings")
    def test_skips_communities_without_queries_or_dois(
        self, mock_settings, _mock_init_db, mock_sync_all, mock_sync_citing, _mock_registry
    ):
        """Should skip communities that have neither queries nor DOIs."""
        # Remove all paper config from bids
        _mock_registry.get("bids").community_config.citations = None
        mock_settings.return_value.semantic_scholar_api_key = "test-key"
        mock_settings.return_value.pubmed_api_key = "test-key"
        mock_sync_all.return_value = {"source1": 5}
        mock_sync_citing.return_value = 2

        _run_papers_sync()

        # Should only sync HED (bids has no citations config)
        assert mock_sync_all.call_count == 1
        assert mock_sync_citing.call_count == 1


class TestRunSyncNow:
    """Tests for run_sync_now manual trigger."""

    @patch("src.api.scheduler.sync_citing_papers")
    @patch("src.api.scheduler.sync_all_papers")
    @patch("src.api.scheduler.sync_repos")
    @patch("src.api.scheduler.init_db")
    @patch("src.api.scheduler.get_settings")
    def test_syncs_all_types_for_all_communities(
        self,
        mock_settings,
        _mock_init_db,
        mock_sync_repos,
        mock_sync_all,
        mock_sync_citing,
        _mock_registry,
    ):
        """Should sync both GitHub and papers for all communities when sync_type='all'."""
        mock_settings.return_value.github_token = "test-token"
        mock_settings.return_value.semantic_scholar_api_key = "test-key"
        mock_settings.return_value.pubmed_api_key = "test-key"
        mock_sync_repos.return_value = {"repo1": 5}
        mock_sync_all.return_value = {"source1": 3}
        mock_sync_citing.return_value = 2

        results = run_sync_now(sync_type="all")

        # Should call both github and papers sync for both communities
        assert mock_sync_repos.call_count == 2
        assert mock_sync_all.call_count == 2
        assert mock_sync_citing.call_count == 1  # Only HED has DOIs
        assert "github" in results
        assert "papers" in results

    @patch("src.api.scheduler.sync_repos")
    @patch("src.api.scheduler.init_db")
    @patch("src.api.scheduler.get_settings")
    def test_syncs_only_github_when_requested(
        self, mock_settings, _mock_init_db, mock_sync_repos, _mock_registry
    ):
        """Should sync only GitHub when sync_type='github'."""
        mock_settings.return_value.github_token = "test-token"
        mock_sync_repos.return_value = {"repo1": 5}

        results = run_sync_now(sync_type="github")

        # Should sync github for both communities
        assert mock_sync_repos.call_count == 2
        assert results["github"] == 10  # 5 items x 2 communities
        assert results["papers"] == 0

    @patch("src.api.scheduler.sync_citing_papers")
    @patch("src.api.scheduler.sync_all_papers")
    @patch("src.api.scheduler.init_db")
    @patch("src.api.scheduler.get_settings")
    def test_syncs_only_papers_when_requested(
        self, mock_settings, _mock_init_db, mock_sync_all, mock_sync_citing, _mock_registry
    ):
        """Should sync only papers when sync_type='papers'."""
        mock_settings.return_value.github_token = None
        mock_settings.return_value.semantic_scholar_api_key = "test-key"
        mock_settings.return_value.pubmed_api_key = "test-key"
        mock_sync_all.return_value = {"source1": 3}
        mock_sync_citing.return_value = 2

        results = run_sync_now(sync_type="papers")

        # Should sync papers for both communities
        assert mock_sync_all.call_count == 2
        assert results["github"] == 0
        assert results["papers"] == 8  # 3+3+2 (hed: 3 query + 2 citing, bids: 3 query)

    @patch("src.api.scheduler.init_db")
    @patch("src.api.scheduler.get_settings")
    def test_handles_no_communities_with_sync(self, mock_settings, _mock_init_db):
        """Should return empty results when no communities have sync enabled."""
        mock_settings.return_value.github_token = None
        with patch("src.api.scheduler.registry") as mock_reg:
            mock_reg.list_all.return_value = []

            results = run_sync_now(sync_type="all")

            assert results == {"github": 0, "papers": 0}
