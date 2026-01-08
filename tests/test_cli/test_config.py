"""Tests for CLI configuration management.

These tests use real file I/O operations against temporary directories.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.cli.config import (
    CLIConfig,
    get_config_dir,
    get_config_path,
    get_data_dir,
    load_config,
    save_config,
    update_config,
)


@pytest.fixture
def temp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory."""
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
        assert config.api_url == "http://localhost:38528"
        assert config.api_key is None
        assert config.openai_api_key is None
        assert config.anthropic_api_key is None
        assert config.openrouter_api_key is None
        assert config.output_format == "rich"
        assert config.verbose is False

    def test_custom_values(self) -> None:
        """CLIConfig should accept custom values."""
        config = CLIConfig(
            api_url="https://example.com",
            api_key="test-key",
            openai_api_key="sk-test",
            verbose=True,
        )
        assert config.api_url == "https://example.com"
        assert config.api_key == "test-key"
        assert config.openai_api_key == "sk-test"
        assert config.verbose is True

    def test_model_dump(self) -> None:
        """CLIConfig should serialize to dict."""
        config = CLIConfig(api_url="https://example.com")
        data = config.model_dump()
        assert isinstance(data, dict)
        assert data["api_url"] == "https://example.com"


class TestConfigPaths:
    """Tests for config path functions."""

    def test_get_config_dir_returns_path(self) -> None:
        """get_config_dir should return a Path object."""
        result = get_config_dir()
        assert isinstance(result, Path)

    def test_get_data_dir_returns_path(self) -> None:
        """get_data_dir should return a Path object."""
        result = get_data_dir()
        assert isinstance(result, Path)

    def test_get_config_path_returns_json_path(self) -> None:
        """get_config_path should return path to config.json."""
        result = get_config_path()
        assert result.name == "config.json"


class TestLoadSaveConfig:
    """Tests for load_config and save_config functions."""

    def test_load_config_returns_defaults_when_no_file(self, temp_config_dir: Path) -> None:
        """load_config should return defaults when file doesn't exist."""
        with patch("src.cli.config.get_config_path") as mock_path:
            mock_path.return_value = temp_config_dir / "config.json"
            config = load_config()
            assert config.api_url == "http://localhost:38528"

    def test_save_and_load_config(self, temp_config_dir: Path) -> None:
        """save_config and load_config should round-trip correctly."""
        config_path = temp_config_dir / "config.json"

        with patch("src.cli.config.get_config_path") as mock_path:
            mock_path.return_value = config_path

            # Save custom config
            original = CLIConfig(
                api_url="https://custom.example.com",
                api_key="my-secret-key",
                verbose=True,
            )
            save_config(original)

            # Verify file was created
            assert config_path.exists()

            # Load and verify
            loaded = load_config()
            assert loaded.api_url == "https://custom.example.com"
            assert loaded.api_key == "my-secret-key"
            assert loaded.verbose is True

    def test_load_config_handles_invalid_json(self, temp_config_dir: Path) -> None:
        """load_config should return defaults on invalid JSON."""
        config_path = temp_config_dir / "config.json"
        config_path.write_text("not valid json")

        with patch("src.cli.config.get_config_path") as mock_path:
            mock_path.return_value = config_path
            config = load_config()
            # Should return defaults
            assert config.api_url == "http://localhost:38528"

    def test_save_config_creates_parent_dirs(self, tmp_path: Path) -> None:
        """save_config should create parent directories if needed."""
        config_path = tmp_path / "nested" / "dir" / "config.json"

        with patch("src.cli.config.get_config_path") as mock_path:
            mock_path.return_value = config_path
            save_config(CLIConfig())
            assert config_path.exists()


class TestUpdateConfig:
    """Tests for update_config function."""

    def test_update_config_updates_single_field(self, temp_config_dir: Path) -> None:
        """update_config should update a single field."""
        config_path = temp_config_dir / "config.json"

        with patch("src.cli.config.get_config_path") as mock_path:
            mock_path.return_value = config_path

            # First save a base config
            save_config(CLIConfig())

            # Update single field
            result = update_config(api_url="https://new-url.com")

            assert result.api_url == "https://new-url.com"
            # Other fields should remain default
            assert result.verbose is False

    def test_update_config_preserves_existing_values(self, temp_config_dir: Path) -> None:
        """update_config should preserve fields not being updated."""
        config_path = temp_config_dir / "config.json"

        with patch("src.cli.config.get_config_path") as mock_path:
            mock_path.return_value = config_path

            # Save config with custom values
            save_config(
                CLIConfig(
                    api_url="https://original.com",
                    api_key="original-key",
                )
            )

            # Update only api_url
            result = update_config(api_url="https://updated.com")

            assert result.api_url == "https://updated.com"
            # api_key should be preserved
            assert result.api_key == "original-key"

    def test_update_config_ignores_none_values(self, temp_config_dir: Path) -> None:
        """update_config should not update fields with None values."""
        config_path = temp_config_dir / "config.json"

        with patch("src.cli.config.get_config_path") as mock_path:
            mock_path.return_value = config_path

            save_config(CLIConfig(api_url="https://original.com"))

            # Pass None for api_url (should not change it)
            result = update_config(api_url=None, verbose=True)

            assert result.api_url == "https://original.com"
            assert result.verbose is True
