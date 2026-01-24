"""Tests for config validation CLI command."""

from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from src.cli.main import cli

runner = CliRunner()


class TestValidateCommand:
    """Tests for osa validate command."""

    def test_valid_config_passes(self, tmp_path: Path) -> None:
        """Valid config should pass validation."""
        config = {
            "id": "test-community",
            "name": "Test Community",
            "description": "A test community for validation",
            "cors_origins": ["https://example.com"],
            "documentation": [
                {
                    "title": "Test Doc",
                    "url": "https://example.com/docs",
                }
            ],
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0
        assert "✓ Valid" in result.stdout
        assert "Validation passed" in result.stdout

    def test_invalid_yaml_syntax_fails(self, tmp_path: Path) -> None:
        """Invalid YAML syntax should fail with line number."""
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            f.write("id: test\ninvalid: yaml: syntax:\n")

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "YAML syntax error" in result.stdout

    def test_invalid_schema_fails(self, tmp_path: Path) -> None:
        """Invalid schema should fail with clear error messages."""
        config = {
            "id": "INVALID_ID",  # Should be kebab-case
            "name": "Test",
            "description": "Test",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "Schema Validation Errors" in result.stdout
        assert "kebab-case" in result.stdout

    def test_missing_api_key_warns(self, tmp_path: Path) -> None:
        """Missing API key env var should warn but not fail."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "openrouter_api_key_env_var": "OPENROUTER_API_KEY_NONEXISTENT",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0  # Passes with warning
        assert "Validation passed with warnings" in result.stdout
        assert "OPENROUTER_API_KEY_NONEXISTENT" in result.stdout
        assert "not set" in result.stdout

    def test_api_key_set_passes(self, tmp_path: Path) -> None:
        """Config with set API key env var should pass."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "openrouter_api_key_env_var": "OPENROUTER_API_KEY_TEST",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Set the env var
        with patch.dict("os.environ", {"OPENROUTER_API_KEY_TEST": "sk-test-key"}):
            result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0
        assert "Validation passed" in result.stdout
        assert "OPENROUTER_API_KEY_TEST is set" in result.stdout

    def test_api_key_test_flag(self, tmp_path: Path) -> None:
        """--test-api-key flag should test API key functionality."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "openrouter_api_key_env_var": "OPENROUTER_API_KEY_TEST",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Mock the API request
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY_TEST": "sk-test-key"}),
            patch("httpx.get") as mock_get,
        ):
            # Simulate successful API response
            mock_get.return_value.status_code = 200

            result = runner.invoke(cli, ["validate", str(config_path), "--test-api-key"])

        assert result.exit_code == 0
        assert "Testing API key" in result.stdout or "API Key Test" in result.stdout

    def test_file_not_found(self) -> None:
        """Non-existent file should fail with clear error."""
        result = runner.invoke(cli, ["validate", "/nonexistent/config.yaml"])

        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_displays_config_details(self, tmp_path: Path) -> None:
        """Should display configuration details in output."""
        config = {
            "id": "test-community",
            "name": "Test Community",
            "description": "Test",
            "cors_origins": ["https://example.com", "https://example.org"],
            "documentation": [
                {"title": "Doc 1", "url": "https://example.com/doc1"},
                {"title": "Doc 2", "url": "https://example.com/doc2"},
            ],
            "github": {"repos": ["org/repo1", "org/repo2"]},
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0
        assert "test-community" in result.stdout
        assert "Test Community" in result.stdout
        assert "2 configured" in result.stdout  # CORS origins
        assert "2 docs" in result.stdout  # Documentation
        assert "2 repos" in result.stdout  # GitHub repos

    def test_invalid_cors_origin_fails(self, tmp_path: Path) -> None:
        """Invalid CORS origin format should fail."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "cors_origins": ["invalid-origin"],  # Missing scheme
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "Invalid CORS origin" in result.stdout

    def test_preload_without_source_url_fails(self, tmp_path: Path) -> None:
        """Preloaded doc without source_url should fail."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "documentation": [
                {
                    "title": "Test Doc",
                    "url": "https://example.com",
                    "preload": True,
                    # Missing source_url
                }
            ],
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "preload" in result.stdout.lower()
        assert "source_url" in result.stdout.lower()

    def test_default_model_displayed(self, tmp_path: Path) -> None:
        """Default model should be displayed in output."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "default_model": "anthropic/claude-3.5-sonnet",
            "default_model_provider": "Cerebras",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0
        assert "anthropic/claude-3.5-sonnet" in result.stdout
        assert "Cerebras" in result.stdout


class TestAPIKeyTesting:
    """Tests for --test-api-key functionality."""

    def test_successful_api_key_test(self, tmp_path: Path) -> None:
        """Successful API key test should pass."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "openrouter_api_key_env_var": "TEST_KEY",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        with (
            patch.dict("os.environ", {"TEST_KEY": "sk-test"}),
            patch("httpx.get") as mock_get,
        ):
            mock_get.return_value.status_code = 200

            result = runner.invoke(cli, ["validate", str(config_path), "--test-api-key"])

        assert result.exit_code == 0
        assert "Key works" in result.stdout or "✓" in result.stdout

    def test_unauthorized_api_key_test(self, tmp_path: Path) -> None:
        """401 response should report invalid key."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "openrouter_api_key_env_var": "TEST_KEY",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        with (
            patch.dict("os.environ", {"TEST_KEY": "sk-invalid"}),
            patch("httpx.get") as mock_get,
        ):
            mock_get.return_value.status_code = 401

            result = runner.invoke(cli, ["validate", str(config_path), "--test-api-key"])

        assert result.exit_code == 1
        assert "401" in result.stdout or "Unauthorized" in result.stdout

    def test_network_error_api_key_test(self, tmp_path: Path) -> None:
        """Network error should be reported."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "openrouter_api_key_env_var": "TEST_KEY",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        with (
            patch.dict("os.environ", {"TEST_KEY": "sk-test"}),
            patch("httpx.get", side_effect=Exception("Network error")),
        ):
            result = runner.invoke(cli, ["validate", str(config_path), "--test-api-key"])

        assert result.exit_code == 1
        assert "error" in result.stdout.lower()
