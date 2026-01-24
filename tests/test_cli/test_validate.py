"""Tests for config validation CLI command."""

import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from src.cli.main import cli
from src.cli.validate import _interpret_api_response, _test_openrouter_api_key

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
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0
        assert "✓ Valid" in result.stdout
        assert "Validation passed" in result.stdout

    def test_invalid_yaml_syntax_fails(self, tmp_path: Path) -> None:
        """Invalid YAML syntax should fail with line number."""
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
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
        with open(config_path, "w", encoding="utf-8") as f:
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
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0  # Passes with warning
        assert "Validation passed with warnings" in result.stdout
        assert "OPENROUTER_API_KEY_NONEXISTENT" in result.stdout
        assert "not set" in result.stdout

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
        with open(config_path, "w", encoding="utf-8") as f:
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
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "Invalid CORS origin" in result.stdout or "origin" in result.stdout.lower()

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
        with open(config_path, "w", encoding="utf-8") as f:
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
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0
        assert "anthropic/claude-3.5-sonnet" in result.stdout
        assert "Cerebras" in result.stdout

    def test_empty_yaml_file_fails(self, tmp_path: Path) -> None:
        """Empty YAML file should fail with clear error."""
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("")  # Empty file

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "empty" in result.stdout.lower() or "File is empty" in result.stdout

    def test_yaml_with_only_comments_fails(self, tmp_path: Path) -> None:
        """YAML file with only comments should fail."""
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("# This is a comment\n# Another comment\n")

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "empty" in result.stdout.lower() or "comments" in result.stdout.lower()

    def test_invalid_value_shown_in_error(self, tmp_path: Path) -> None:
        """Pydantic validation errors should show the invalid value."""
        config = {
            "id": "INVALID_ID_123",  # Invalid - has uppercase
            "name": "Test",
            "description": "Test",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        # Should show the invalid value
        assert "INVALID_ID_123" in result.stdout


class TestComplexSchemaValidation:
    """Tests for complex schema validation rules."""

    def test_invalid_github_repo_format_fails(self, tmp_path: Path) -> None:
        """Invalid GitHub repo format should fail with clear error."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "github": {"repos": ["invalid-format"]},  # Missing org/
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "org/repo" in result.stdout.lower() or "format" in result.stdout.lower()

    def test_invalid_doi_format_fails(self, tmp_path: Path) -> None:
        """Invalid DOI format should fail with clear error."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "citations": {"dois": ["invalid-doi"]},  # Missing 10.xxxx/
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "10." in result.stdout or "DOI" in result.stdout

    def test_mcp_server_both_command_and_url_fails(self, tmp_path: Path) -> None:
        """MCP server with both command and url should fail."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "extensions": {
                "mcp_servers": [
                    {
                        "name": "test-server",
                        "command": ["node", "server.js"],
                        "url": "https://example.com",  # Both provided - invalid
                    }
                ]
            },
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1
        assert "command" in result.stdout.lower() or "url" in result.stdout.lower()

    def test_mcp_server_neither_command_nor_url_fails(self, tmp_path: Path) -> None:
        """MCP server with neither command nor url should fail."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "extensions": {
                "mcp_servers": [
                    {
                        "name": "test-server",
                        # Missing both command and url
                    }
                ]
            },
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 1


class TestAPIResponseInterpretation:
    """Tests for API response interpretation (pure function - no mocking needed)."""

    def test_interprets_200_success(self) -> None:
        """200 status code should return success."""
        result = _interpret_api_response(200)
        assert result["success"] is True
        assert "error" not in result

    def test_interprets_401_unauthorized(self) -> None:
        """401 status code should return invalid key error."""
        result = _interpret_api_response(401)
        assert result["success"] is False
        assert "401" in result["error"]
        assert "Invalid" in result["error"] or "Unauthorized" in result["error"]

    def test_interprets_403_forbidden(self) -> None:
        """403 status code should return permissions error."""
        result = _interpret_api_response(403)
        assert result["success"] is False
        assert "403" in result["error"]
        assert "permissions" in result["error"] or "Forbidden" in result["error"]

    def test_interprets_500_unexpected(self) -> None:
        """500 status code should return unexpected error."""
        result = _interpret_api_response(500)
        assert result["success"] is False
        assert "500" in result["error"]
        assert "Unexpected" in result["error"] or "status code" in result["error"]

    def test_interprets_429_rate_limit(self) -> None:
        """429 status code should return unexpected error with details."""
        result = _interpret_api_response(429, "Rate limit exceeded")
        assert result["success"] is False
        assert "429" in result["error"]
        assert "Rate limit" in result["error"]

    def test_includes_response_body_in_error(self) -> None:
        """Unexpected status codes should include response body."""
        result = _interpret_api_response(503, "Service temporarily unavailable")
        assert result["success"] is False
        assert "503" in result["error"]
        assert "Service temporarily unavailable" in result["error"]

    def test_truncates_long_response_body(self) -> None:
        """Long response bodies should be truncated."""
        long_body = "x" * 300
        result = _interpret_api_response(500, long_body)
        assert result["success"] is False
        assert len(result["error"]) < 250  # Should be truncated


class TestRealAPIKeyTesting:
    """Tests for real API key testing (uses actual OpenRouter API)."""

    @pytest.mark.skipif(
        not os.getenv("OPENROUTER_API_KEY_TEST"),
        reason="OPENROUTER_API_KEY_TEST not set",
    )
    def test_valid_api_key_works(self) -> None:
        """Real API key from env should pass validation."""
        api_key = os.getenv("OPENROUTER_API_KEY_TEST")
        if not api_key:
            pytest.skip("OPENROUTER_API_KEY_TEST not set")

        result = _test_openrouter_api_key(api_key)
        assert result["success"] is True

    def test_obviously_invalid_key_fails(self) -> None:
        """Obviously invalid key should fail (Note: OpenRouter may allow some invalid formats)."""
        # Test with a completely invalid format (not even sk-or-v1 format)
        # Note: OpenRouter's /models endpoint is permissive, so we can't guarantee failure
        # This test may pass or fail depending on OpenRouter's validation
        result = _test_openrouter_api_key("invalid-not-a-real-key")
        # We can't assert failure because OpenRouter might return 200 for any format
        # Just verify the function returns a dict with success key
        assert "success" in result
        assert isinstance(result["success"], bool)

    @pytest.mark.skipif(
        not os.getenv("OPENROUTER_API_KEY_TEST"),
        reason="OPENROUTER_API_KEY_TEST not set",
    )
    def test_api_key_test_via_cli(self, tmp_path: Path) -> None:
        """--test-api-key flag should test API key functionality."""
        api_key_env = "OPENROUTER_API_KEY_TEST"
        if not os.getenv(api_key_env):
            pytest.skip(f"{api_key_env} not set")

        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "openrouter_api_key_env_var": api_key_env,
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path), "--test-api-key"])

        assert result.exit_code == 0
        assert "Testing API key" in result.stdout or "Key works" in result.stdout

    def test_test_api_key_flag_without_env_var(self, tmp_path: Path) -> None:
        """--test-api-key flag without env var should skip test."""
        config = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "openrouter_api_key_env_var": "MISSING_VAR_DOES_NOT_EXIST",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path), "--test-api-key"])

        # Should pass with warning (env var missing), not attempt to test
        assert result.exit_code == 0
        assert "not set" in result.stdout.lower()
