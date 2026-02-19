"""Tests for CLI configuration management.

These tests use real file I/O operations against temporary directories.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.cli.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    CREDENTIALS_FILE,
    CLIConfig,
    CredentialsConfig,
    get_data_dir,
    get_effective_config,
    get_user_id,
    load_config,
    load_credentials,
    save_config,
    save_credentials,
)


@pytest.fixture
def temp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory and patch CONFIG_DIR and file paths."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return config_dir


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


class TestCLIConfig:
    """Tests for CLIConfig model."""

    def test_default_values(self) -> None:
        """CLIConfig should have sensible defaults."""
        config = CLIConfig()
        assert config.api.url == "https://api.osc.earth/osa"
        assert config.output.format == "rich"
        assert config.output.verbose is False
        assert config.output.streaming is True

    def test_custom_values(self) -> None:
        """CLIConfig should accept nested custom values."""
        config = CLIConfig(
            api={"url": "https://example.com"},
            output={"format": "json", "verbose": True},
        )
        assert config.api.url == "https://example.com"
        assert config.output.format == "json"
        assert config.output.verbose is True

    def test_model_dump(self) -> None:
        """CLIConfig should serialize to dict."""
        config = CLIConfig(api={"url": "https://example.com"})
        data = config.model_dump()
        assert isinstance(data, dict)
        assert data["api"]["url"] == "https://example.com"


class TestCredentialsConfig:
    """Tests for CredentialsConfig model."""

    def test_default_values(self) -> None:
        """CredentialsConfig should default to no keys."""
        creds = CredentialsConfig()
        assert creds.openrouter_api_key is None
        assert creds.openai_api_key is None
        assert creds.anthropic_api_key is None

    def test_custom_values(self) -> None:
        """CredentialsConfig should accept custom values."""
        creds = CredentialsConfig(openrouter_api_key="sk-or-test")
        assert creds.openrouter_api_key == "sk-or-test"


class TestConfigPaths:
    """Tests for config path constants."""

    def test_config_dir_is_path(self) -> None:
        """CONFIG_DIR should be a Path object."""
        assert isinstance(CONFIG_DIR, Path)

    def test_config_file_is_yaml(self) -> None:
        """CONFIG_FILE should be a YAML file."""
        assert CONFIG_FILE.name == "config.yaml"

    def test_credentials_file_is_yaml(self) -> None:
        """CREDENTIALS_FILE should be a YAML file."""
        assert CREDENTIALS_FILE.name == "credentials.yaml"

    def test_get_data_dir_returns_path(self) -> None:
        """get_data_dir should return a Path object."""
        result = get_data_dir()
        assert isinstance(result, Path)


class TestLoadSaveConfig:
    """Tests for load_config and save_config functions."""

    def test_load_config_returns_defaults_when_no_file(self, temp_config_dir: Path) -> None:
        """load_config should return defaults when file doesn't exist."""
        config_file = temp_config_dir / "config.yaml"
        legacy_file = temp_config_dir / "config.json"
        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            config = load_config()
            assert config.api.url == "https://api.osc.earth/osa"

    def test_save_and_load_config(self, temp_config_dir: Path) -> None:
        """save_config and load_config should round-trip correctly."""
        config_file = temp_config_dir / "config.yaml"
        legacy_file = temp_config_dir / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CONFIG_DIR", temp_config_dir),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            original = CLIConfig(
                api={"url": "https://custom.example.com"},
                output={"verbose": True},
            )
            save_config(original)

            assert config_file.exists()

            loaded = load_config()
            assert loaded.api.url == "https://custom.example.com"
            assert loaded.output.verbose is True

    def test_load_config_handles_invalid_yaml(self, temp_config_dir: Path) -> None:
        """load_config should return defaults on invalid YAML."""
        config_file = temp_config_dir / "config.yaml"
        config_file.write_text(": invalid: yaml: [")
        legacy_file = temp_config_dir / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            config = load_config()
            assert config.api.url == "https://api.osc.earth/osa"


class TestLoadSaveCredentials:
    """Tests for credentials I/O."""

    def test_save_and_load_credentials(self, temp_config_dir: Path) -> None:
        """save_credentials and load_credentials should round-trip."""
        creds_file = temp_config_dir / "credentials.yaml"

        with (
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.CONFIG_DIR", temp_config_dir),
        ):
            creds = CredentialsConfig(openrouter_api_key="sk-or-test-key")
            save_credentials(creds)

            assert creds_file.exists()

            loaded = load_credentials()
            assert loaded.openrouter_api_key == "sk-or-test-key"

    def test_load_credentials_returns_defaults_when_no_file(self, temp_config_dir: Path) -> None:
        """load_credentials should return defaults when file doesn't exist."""
        creds_file = temp_config_dir / "nonexistent.yaml"

        with patch("src.cli.config.CREDENTIALS_FILE", creds_file):
            creds = load_credentials()
            assert creds.openrouter_api_key is None


class TestGetEffectiveConfig:
    """Tests for get_effective_config."""

    def test_cli_flag_overrides_saved_key(self, temp_config_dir: Path) -> None:
        """CLI --api-key flag should override saved credentials."""
        config_file = temp_config_dir / "config.yaml"
        creds_file = temp_config_dir / "credentials.yaml"
        legacy_file = temp_config_dir / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.CONFIG_DIR", temp_config_dir),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            save_credentials(CredentialsConfig(openrouter_api_key="saved-key"))

            _, effective_key = get_effective_config(api_key="cli-key")
            assert effective_key == "cli-key"

    def test_env_var_overrides_saved_key(self, temp_config_dir: Path) -> None:
        """OPENROUTER_API_KEY env var should override saved credentials."""
        config_file = temp_config_dir / "config.yaml"
        creds_file = temp_config_dir / "credentials.yaml"
        legacy_file = temp_config_dir / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.CONFIG_DIR", temp_config_dir),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "env-key"}),
        ):
            save_credentials(CredentialsConfig(openrouter_api_key="saved-key"))

            _, effective_key = get_effective_config()
            assert effective_key == "env-key"

    def test_saved_key_used_as_fallback(self, temp_config_dir: Path) -> None:
        """Saved credentials should be used if no CLI flag or env var."""
        config_file = temp_config_dir / "config.yaml"
        creds_file = temp_config_dir / "credentials.yaml"
        legacy_file = temp_config_dir / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.CONFIG_DIR", temp_config_dir),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
            patch.dict("os.environ", {}, clear=True),
        ):
            save_credentials(CredentialsConfig(openrouter_api_key="saved-key"))

            _, effective_key = get_effective_config()
            assert effective_key == "saved-key"

    def test_api_url_override(self, temp_config_dir: Path) -> None:
        """api_url parameter should override saved config."""
        config_file = temp_config_dir / "config.yaml"
        creds_file = temp_config_dir / "credentials.yaml"
        legacy_file = temp_config_dir / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.CONFIG_DIR", temp_config_dir),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            config, _ = get_effective_config(api_url="https://custom.example.com")
            assert config.api.url == "https://custom.example.com"


class TestLegacyMigration:
    """Tests for migration from legacy config.json format."""

    def test_migrate_from_json(self, temp_config_dir: Path) -> None:
        """Should migrate from legacy config.json to new YAML format."""
        import json

        config_file = temp_config_dir / "config.yaml"
        creds_file = temp_config_dir / "credentials.yaml"
        legacy_file = temp_config_dir / "config.json"

        # Write legacy JSON config
        legacy_data = {
            "api_url": "https://legacy-api.example.com",
            "openrouter_api_key": "sk-or-legacy",
            "output_format": "json",
            "verbose": True,
        }
        legacy_file.write_text(json.dumps(legacy_data))

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.CONFIG_DIR", temp_config_dir),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            config = load_config()

            assert config.api.url == "https://legacy-api.example.com"
            assert config.output.format == "json"
            assert config.output.verbose is True

            # Credentials should also be migrated
            creds = load_credentials()
            assert creds.openrouter_api_key == "sk-or-legacy"


class TestUserID:
    """Tests for user ID generation."""

    def test_get_user_id_format(self, temp_config_dir: Path) -> None:
        """get_user_id should return a 16-char hex string."""
        user_id_file = temp_config_dir / "user_id"

        with (
            patch("src.cli.config.USER_ID_FILE", user_id_file),
            patch("src.cli.config.CONFIG_DIR", temp_config_dir),
        ):
            user_id = get_user_id()
            assert len(user_id) == 16
            assert all(c in "0123456789abcdef" for c in user_id)

    def test_get_user_id_is_stable(self, temp_config_dir: Path) -> None:
        """get_user_id should return the same ID on subsequent calls."""
        user_id_file = temp_config_dir / "user_id"

        with (
            patch("src.cli.config.USER_ID_FILE", user_id_file),
            patch("src.cli.config.CONFIG_DIR", temp_config_dir),
        ):
            first = get_user_id()
            second = get_user_id()
            assert first == second
