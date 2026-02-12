"""Tests for per-community background scheduler.

Tests ensure the scheduler correctly:
- Reads sync config from community YAML configs
- Registers per-community jobs with correct cron triggers
- Handles communities without sync config
- Seeds empty databases on startup
"""

import pytest

from src.api.scheduler import (
    _SYNC_TYPE_MAP,
    _failure_key,
    _reset_failure,
    _sync_failures,
    _track_failure,
)
from src.assistants import discover_assistants, registry
from src.core.config.community import SyncConfig, SyncTypeSchedule


@pytest.fixture(scope="module", autouse=True)
def _setup_registry():
    """Ensure registry is populated before tests."""
    registry._assistants.clear()
    discover_assistants()


class TestSyncConfig:
    """Tests for SyncConfig and SyncTypeSchedule models."""

    def test_valid_cron_expression(self):
        """Should accept valid 5-field cron expressions."""
        schedule = SyncTypeSchedule(cron="0 2 * * *")
        assert schedule.cron == "0 2 * * *"

    def test_invalid_cron_expression_too_few_fields(self):
        """Should reject cron expressions with wrong field count."""
        with pytest.raises(ValueError, match="Invalid cron expression"):
            SyncTypeSchedule(cron="0 2 * *")

    def test_invalid_cron_expression_too_many_fields(self):
        """Should reject cron expressions with too many fields."""
        with pytest.raises(ValueError, match="Invalid cron expression"):
            SyncTypeSchedule(cron="0 2 * * * *")

    def test_invalid_cron_expression_bad_field_values(self):
        """Should reject cron expressions with invalid field values."""
        with pytest.raises(ValueError, match="Invalid cron expression"):
            SyncTypeSchedule(cron="0 2 * * 8")  # weekday 8 is invalid
        with pytest.raises(ValueError, match="Invalid cron expression"):
            SyncTypeSchedule(cron="99 2 * * *")  # minute 99 is invalid

    def test_sync_config_all_types(self):
        """Should parse all sync types."""
        config = SyncConfig(
            github=SyncTypeSchedule(cron="0 2 * * *"),
            papers=SyncTypeSchedule(cron="0 3 * * 0"),
            docstrings=SyncTypeSchedule(cron="0 4 * * 1"),
            mailman=SyncTypeSchedule(cron="0 5 * * 1"),
            faq=SyncTypeSchedule(cron="0 6 1 * *"),
            beps=SyncTypeSchedule(cron="0 4 * * 1"),
        )
        assert config.github.cron == "0 2 * * *"
        assert config.beps.cron == "0 4 * * 1"

    def test_sync_config_partial(self):
        """Should allow partial sync config (only some types)."""
        config = SyncConfig(
            github=SyncTypeSchedule(cron="0 2 * * *"),
        )
        assert config.github.cron == "0 2 * * *"
        assert config.papers is None
        assert config.docstrings is None

    def test_sync_config_empty(self):
        """Should allow empty sync config."""
        config = SyncConfig()
        assert config.github is None
        assert config.papers is None


class TestCommunitySyncConfig:
    """Tests that community YAML configs have valid sync sections."""

    def test_communities_with_github_have_sync_github(self):
        """Communities with GitHub repos should have a github sync schedule."""
        for info in registry.list_all():
            config = info.community_config
            if config and config.github and config.github.repos:
                assert config.sync is not None, f"{info.id} has GitHub repos but no sync config"
                assert config.sync.github is not None, (
                    f"{info.id} has GitHub repos but no github sync schedule"
                )

    def test_communities_with_citations_have_sync_papers(self):
        """Communities with citations should have a papers sync schedule."""
        for info in registry.list_all():
            config = info.community_config
            if config and config.citations:
                assert config.sync is not None, f"{info.id} has citations but no sync config"
                assert config.sync.papers is not None, (
                    f"{info.id} has citations but no papers sync schedule"
                )

    def test_eeglab_has_all_sync_types(self):
        """EEGLAB should have all sync types configured."""
        info = registry.get("eeglab")
        assert info is not None
        config = info.community_config
        assert config.sync is not None
        assert config.sync.github is not None
        assert config.sync.papers is not None
        assert config.sync.docstrings is not None
        assert config.sync.mailman is not None
        assert config.sync.faq is not None

    def test_bids_has_beps_sync(self):
        """BIDS should have BEP sync configured."""
        info = registry.get("bids")
        assert info is not None
        config = info.community_config
        assert config.sync is not None
        assert config.sync.beps is not None

    def test_sync_config_in_get_sync_config(self):
        """get_sync_config() should include schedule information."""
        info = registry.get("hed")
        assert info is not None
        sync_config = info.community_config.get_sync_config()
        assert "schedules" in sync_config
        assert "github" in sync_config["schedules"]
        assert "papers" in sync_config["schedules"]

    def test_get_sync_config_schedules_match_cron(self):
        """get_sync_config() schedules should contain actual cron values from config."""
        for info in registry.list_all():
            config = info.community_config
            if not config or not config.sync:
                continue
            sync_config = config.get_sync_config()
            if "schedules" not in sync_config:
                continue
            for sync_type, cron_value in sync_config["schedules"].items():
                schedule = getattr(config.sync, sync_type, None)
                assert schedule is not None, (
                    f"{info.id}: schedule '{sync_type}' in get_sync_config but not in SyncConfig"
                )
                assert cron_value == schedule.cron, f"{info.id}: cron mismatch for {sync_type}"

    def test_get_sync_config_without_sync(self):
        """get_sync_config() should omit schedules when no sync config."""
        from src.core.config.community import CommunityConfig

        config = CommunityConfig(id="test", name="Test", description="Test community")
        result = config.get_sync_config()
        assert "schedules" not in result


class TestSyncTypeMap:
    """Tests for the sync type to job function mapping."""

    def test_all_sync_types_mapped(self):
        """All expected sync types should be in the mapping."""
        expected = {"github", "papers", "docstrings", "mailman", "faq", "beps"}
        assert set(_SYNC_TYPE_MAP.keys()) == expected

    def test_data_checks_return_bool(self):
        """Data check functions should return truthy/falsy values."""
        for info in registry.list_all():
            config = info.community_config
            if not config:
                continue
            for sync_type, (_, data_check) in _SYNC_TYPE_MAP.items():
                result = data_check(config)
                assert isinstance(result, (bool, list, type(None))), (
                    f"data_check for {sync_type} returned unexpected type: {type(result)}"
                )


class TestFailureTracking:
    """Tests for sync failure tracking."""

    def test_failure_key_format(self):
        """Failure keys should be sync_type_community_id."""
        assert _failure_key("github", "hed") == "github_hed"
        assert _failure_key("papers", "bids") == "papers_bids"

    def test_track_failure_increments(self):
        """track_failure should increment the failure count."""
        key = _failure_key("test_type", "test_community")
        _sync_failures.pop(key, None)  # Clean state

        _track_failure("test_type", "test_community", Exception("test"))
        assert _sync_failures[key] == 1

        _track_failure("test_type", "test_community", Exception("test"))
        assert _sync_failures[key] == 2

        # Clean up
        _sync_failures.pop(key, None)

    def test_reset_failure_clears(self):
        """reset_failure should remove the failure counter."""
        key = _failure_key("test_type", "test_community")
        _sync_failures[key] = 5

        _reset_failure("test_type", "test_community")
        assert key not in _sync_failures

    def test_reset_failure_noop_if_not_tracked(self):
        """reset_failure should not error if no failure was tracked."""
        _reset_failure("nonexistent", "nonexistent")
