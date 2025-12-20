"""Tests for CLI commands.

These tests use Typer's CliRunner to test CLI commands
with real output verification.
"""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from src.cli.config import CLIConfig, save_config
from src.cli.main import cli

runner = CliRunner()


class TestVersionCommand:
    """Tests for the version command."""

    def test_version_shows_version(self) -> None:
        """version command should display version number."""
        result = runner.invoke(cli, ["version"])
        assert result.exit_code == 0
        assert "OSA v" in result.output
        assert "0.1.0" in result.output


class TestHealthCommand:
    """Tests for the health command."""

    def test_health_with_invalid_url_shows_error(self, tmp_path: Path) -> None:
        """health command should show error for invalid URL."""
        tmp_path / "config.json"

        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = CLIConfig(api_url="http://invalid-host:99999")
            result = runner.invoke(cli, ["health"])
            assert result.exit_code == 1
            assert "Error" in result.output


class TestConfigCommands:
    """Tests for config subcommands."""

    def test_config_show_displays_settings(self, tmp_path: Path) -> None:
        """config show should display current settings."""
        config_path = tmp_path / "config.json"

        with patch("src.cli.config.get_config_path") as mock_path:
            mock_path.return_value = config_path
            save_config(CLIConfig(api_url="https://test.example.com"))

            with patch("src.cli.main.get_config_path") as mock_main_path:
                mock_main_path.return_value = config_path
                result = runner.invoke(cli, ["config", "show"])

        assert result.exit_code == 0
        assert "api_url" in result.output

    def test_config_set_updates_api_url(self, tmp_path: Path) -> None:
        """config set should update api_url."""
        config_path = tmp_path / "config.json"

        with (
            patch("src.cli.config.get_config_path") as mock_path,
            patch("src.cli.main.load_config") as mock_load,
            patch("src.cli.main.save_config"),
        ):
            mock_path.return_value = config_path
            mock_load.return_value = CLIConfig()

            result = runner.invoke(cli, ["config", "set", "--api-url", "https://new-url.com"])

        assert result.exit_code == 0
        assert "updated" in result.output.lower()

    def test_config_set_validates_output_format(self) -> None:
        """config set should validate output format values."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = CLIConfig()

            result = runner.invoke(cli, ["config", "set", "--output", "invalid"])

        assert result.exit_code == 1
        assert "Invalid output format" in result.output

    def test_config_set_accepts_valid_output_formats(self, tmp_path: Path) -> None:
        """config set should accept valid output format values."""
        config_path = tmp_path / "config.json"

        for format_type in ["rich", "json", "plain"]:
            with (
                patch("src.cli.config.get_config_path") as mock_path,
                patch("src.cli.main.load_config") as mock_load,
                patch("src.cli.main.save_config"),
            ):
                mock_path.return_value = config_path
                mock_load.return_value = CLIConfig()

                result = runner.invoke(cli, ["config", "set", "--output", format_type])

            assert result.exit_code == 0, f"Failed for format: {format_type}"

    def test_config_set_no_options_shows_message(self) -> None:
        """config set with no options should show help message."""
        with patch("src.cli.main.load_config") as mock_load:
            mock_load.return_value = CLIConfig()
            result = runner.invoke(cli, ["config", "set"])

        assert result.exit_code == 0
        assert "No changes made" in result.output

    def test_config_path_shows_directories(self) -> None:
        """config path should show config and data directories."""
        result = runner.invoke(cli, ["config", "path"])
        assert result.exit_code == 0
        assert "Config directory" in result.output
        assert "Data directory" in result.output
        assert "Config file" in result.output

    def test_config_reset_requires_confirmation(self) -> None:
        """config reset should require confirmation."""
        result = runner.invoke(cli, ["config", "reset"], input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.output

    def test_config_reset_with_yes_flag(self, tmp_path: Path) -> None:
        """config reset with --yes should skip confirmation."""
        config_path = tmp_path / "config.json"

        with (
            patch("src.cli.config.get_config_path") as mock_path,
            patch("src.cli.main.save_config"),
        ):
            mock_path.return_value = config_path

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
