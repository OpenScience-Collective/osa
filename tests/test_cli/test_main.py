"""Tests for CLI commands.

These tests use Typer's CliRunner to test CLI commands
with real output verification.
"""

from pathlib import Path
from unittest.mock import patch

from click import unstyle
from typer.testing import CliRunner

from src.cli.config import CLIConfig, save_config
from src.cli.main import cli

runner = CliRunner()


class TestVersionCommand:
    """Tests for the version command."""

    def test_version_shows_version(self) -> None:
        """version command should display version number."""
        from src.version import __version__

        result = runner.invoke(cli, ["version"])
        assert result.exit_code == 0
        assert "OSA v" in result.output
        assert __version__ in result.output


class TestHealthCommand:
    """Tests for the health command."""

    def test_health_with_invalid_url_shows_error(self, tmp_path: Path) -> None:
        """health command should show error for invalid URL."""
        config_file = tmp_path / "config.yaml"
        creds_file = tmp_path / "credentials.yaml"
        legacy_file = tmp_path / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CONFIG_DIR", tmp_path),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            save_config(CLIConfig(api={"url": "http://invalid-host:99999"}))
            result = runner.invoke(cli, ["health"])
            assert result.exit_code == 1
            assert "Error" in result.output or "error" in result.output.lower()


class TestConfigCommands:
    """Tests for config subcommands."""

    def test_config_show_displays_settings(self, tmp_path: Path) -> None:
        """config show should display current settings."""
        config_file = tmp_path / "config.yaml"
        creds_file = tmp_path / "credentials.yaml"
        legacy_file = tmp_path / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CONFIG_DIR", tmp_path),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
            patch("src.cli.main.CONFIG_FILE", config_file),
            patch("src.cli.main.CREDENTIALS_FILE", creds_file),
        ):
            save_config(CLIConfig(api={"url": "https://test.example.com"}))
            result = runner.invoke(cli, ["config", "show"])

        assert result.exit_code == 0
        assert "api.url" in result.output

    def test_config_set_updates_api_url(self, tmp_path: Path) -> None:
        """config set should update api_url."""
        config_file = tmp_path / "config.yaml"
        creds_file = tmp_path / "credentials.yaml"
        legacy_file = tmp_path / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CONFIG_DIR", tmp_path),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            result = runner.invoke(cli, ["config", "set", "--api-url", "https://new-url.com"])

        assert result.exit_code == 0
        assert "updated" in result.output.lower()

    def test_config_set_validates_output_format(self, tmp_path: Path) -> None:
        """config set should validate output format values."""
        config_file = tmp_path / "config.yaml"
        creds_file = tmp_path / "credentials.yaml"
        legacy_file = tmp_path / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CONFIG_DIR", tmp_path),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            result = runner.invoke(cli, ["config", "set", "--output", "invalid"])

        assert result.exit_code == 1
        assert "Invalid output format" in result.output

    def test_config_set_accepts_valid_output_formats(self, tmp_path: Path) -> None:
        """config set should accept valid output format values."""
        config_file = tmp_path / "config.yaml"
        creds_file = tmp_path / "credentials.yaml"
        legacy_file = tmp_path / "config.json"

        for format_type in ["rich", "json", "plain"]:
            with (
                patch("src.cli.config.CONFIG_FILE", config_file),
                patch("src.cli.config.CONFIG_DIR", tmp_path),
                patch("src.cli.config.CREDENTIALS_FILE", creds_file),
                patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
            ):
                result = runner.invoke(cli, ["config", "set", "--output", format_type])
            assert result.exit_code == 0, f"Failed for format: {format_type}"

    def test_config_set_no_options_shows_message(self, tmp_path: Path) -> None:
        """config set with no options should show help message."""
        config_file = tmp_path / "config.yaml"
        creds_file = tmp_path / "credentials.yaml"
        legacy_file = tmp_path / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CONFIG_DIR", tmp_path),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            result = runner.invoke(cli, ["config", "set"])

        assert result.exit_code == 0
        assert "No changes made" in result.output

    def test_config_path_shows_directories(self) -> None:
        """config path should show config and data directories."""
        result = runner.invoke(cli, ["config", "path"])
        assert result.exit_code == 0
        assert "Config directory" in result.output
        assert "Data directory" in result.output

    def test_config_reset_requires_confirmation(self) -> None:
        """config reset should require confirmation."""
        result = runner.invoke(cli, ["config", "reset"], input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.output

    def test_config_reset_with_yes_flag(self, tmp_path: Path) -> None:
        """config reset with --yes should skip confirmation."""
        config_file = tmp_path / "config.yaml"
        creds_file = tmp_path / "credentials.yaml"
        legacy_file = tmp_path / "config.json"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CONFIG_DIR", tmp_path),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
        ):
            result = runner.invoke(cli, ["config", "reset", "--yes"])

        assert result.exit_code == 0
        assert "reset to defaults" in result.output.lower()


class TestCLIHelp:
    """Tests for CLI help messages."""

    def test_main_help(self) -> None:
        """Main CLI should show help with --help."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Open Science Assistant" in result.output

    def test_config_help(self) -> None:
        """config subcommand should show help."""
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0
        assert "Manage CLI configuration" in result.output


class TestAskCommand:
    """Tests for the ask command."""

    def test_ask_help_shows_options(self) -> None:
        """ask --help should show assistant and output options."""
        result = runner.invoke(cli, ["ask", "--help"])
        assert result.exit_code == 0
        clean = unstyle(result.output)
        assert "--assistant" in clean
        assert "--api-key" in clean
        assert "QUESTION" in clean or "question" in clean.lower()

    def test_ask_without_api_key_shows_error(self, tmp_path: Path) -> None:
        """ask without API key should show init hint."""
        config_file = tmp_path / "config.yaml"
        creds_file = tmp_path / "credentials.yaml"
        legacy_file = tmp_path / "config.json"
        first_run_file = tmp_path / ".first_run"

        with (
            patch("src.cli.config.CONFIG_FILE", config_file),
            patch("src.cli.config.CONFIG_DIR", tmp_path),
            patch("src.cli.config.CREDENTIALS_FILE", creds_file),
            patch("src.cli.config.LEGACY_CONFIG_FILE", legacy_file),
            patch("src.cli.config.FIRST_RUN_FILE", first_run_file),
            patch.dict("os.environ", {}, clear=True),
        ):
            result = runner.invoke(cli, ["ask", "test question"])

        assert result.exit_code == 1
        assert "No API key" in result.output


class TestChatCommand:
    """Tests for the chat command."""

    def test_chat_help_shows_options(self) -> None:
        """chat --help should show assistant options."""
        result = runner.invoke(cli, ["chat", "--help"])
        assert result.exit_code == 0
        clean = unstyle(result.output)
        assert "--assistant" in clean
        assert "--api-key" in clean
