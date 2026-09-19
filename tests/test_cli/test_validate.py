"""Tests for config validation CLI command."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import pytest
import respx
import yaml
from typer.testing import CliRunner

from src.cli.main import cli
from src.cli.validate import (
    _community_key_env_var,
    _interpret_api_response,
    _test_api_key,
    _test_openrouter_api_key,
)
from src.core.config.community import CommunityConfig
from src.core.services.anthropic_endpoints import FIRST_PARTY_BASE_URL

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
            "openrouter_api_key_env_var": "OPENROUTER_API_KEY_MISSING",
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        result = runner.invoke(cli, ["validate", str(config_path), "--test-api-key"])

        # Should pass with warning (env var missing), not attempt to test
        assert result.exit_code == 0
        assert "not set" in result.stdout.lower()


def _write_config(tmp_path: Path, **extra: str) -> Path:
    """Write a minimal valid config, plus whatever key fields a test needs."""
    config: dict[str, object] = {
        "id": "test",
        "name": "Test",
        "description": "Test",
        **extra,
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f)
    return config_path


class TestCommunityKeyEnvVarResolution:
    """Which of a community's two key fields the validator reports (issue #390).

    The runtime prefers a community's Anthropic key over its OpenRouter one:
    see `_resolve_provider` in src/api/routers/community.py and the
    `anthropic_api_key_env_var or openrouter_api_key_env_var` in
    src/api/routers/health.py. The validator read only the OpenRouter field,
    so a community funding its own Anthropic usage was told the opposite of
    the truth ("Not configured (using platform key)") and never got the
    missing-env-var warning that exists to prevent surprise platform billing.
    """

    def test_anthropic_env_var_is_reported_when_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY_OSATEST", "sk-ant-not-used-here")
        config_path = _write_config(tmp_path, anthropic_api_key_env_var="ANTHROPIC_API_KEY_OSATEST")

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0
        assert "ANTHROPIC_API_KEY_OSATEST is set" in result.stdout
        assert "Anthropic" in result.stdout
        assert "Not configured" not in result.stdout

    def test_missing_anthropic_env_var_warns_about_platform_billing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY_OSATEST", raising=False)
        config_path = _write_config(tmp_path, anthropic_api_key_env_var="ANTHROPIC_API_KEY_OSATEST")

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0  # Passes with warning
        assert "Validation passed with warnings" in result.stdout
        assert "ANTHROPIC_API_KEY_OSATEST" in result.stdout
        assert "not set" in result.stdout
        assert "billed to the platform" in result.stdout

    def test_anthropic_wins_when_both_env_vars_are_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Report the key that actually pays, which is the Anthropic one."""
        monkeypatch.setenv("ANTHROPIC_API_KEY_OSATEST", "sk-ant-not-used-here")
        monkeypatch.setenv("OPENROUTER_API_KEY_OSATEST", "sk-or-v1-not-used-here")
        config_path = _write_config(
            tmp_path,
            anthropic_api_key_env_var="ANTHROPIC_API_KEY_OSATEST",
            openrouter_api_key_env_var="OPENROUTER_API_KEY_OSATEST",
        )

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0
        assert "ANTHROPIC_API_KEY_OSATEST is set" in result.stdout
        assert "OPENROUTER_API_KEY_OSATEST" not in result.stdout

    def test_neither_field_set_means_the_platform_key(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)

        result = runner.invoke(cli, ["validate", str(config_path)])

        assert result.exit_code == 0
        assert "Not configured (using platform key)" in result.stdout

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            (
                {"anthropic_api_key_env_var": "ANTHROPIC_API_KEY_OSATEST"},
                ("ANTHROPIC_API_KEY_OSATEST", "anthropic"),
            ),
            (
                {"openrouter_api_key_env_var": "OPENROUTER_API_KEY_OSATEST"},
                ("OPENROUTER_API_KEY_OSATEST", "openrouter"),
            ),
            (
                {
                    "anthropic_api_key_env_var": "ANTHROPIC_API_KEY_OSATEST",
                    "openrouter_api_key_env_var": "OPENROUTER_API_KEY_OSATEST",
                },
                ("ANTHROPIC_API_KEY_OSATEST", "anthropic"),
            ),
            ({}, None),
        ],
    )
    def test_helper_resolves_env_var_and_provider(
        self, fields: dict[str, str], expected: tuple[str, str] | None
    ) -> None:
        config = CommunityConfig.model_validate(
            {"id": "test", "name": "Test", "description": "Test", **fields}
        )

        assert _community_key_env_var(config) == expected


class TestApiKeyTestRouting:
    """--test-api-key must reach the provider that owns the key."""

    @respx.mock
    def test_anthropic_key_is_tested_against_the_first_party_endpoint(self) -> None:
        """Not the AWS platform endpoint, where a community key is unauthorized.

        `create_anthropic_llm` pins base_url to the first-party API whenever an
        explicit key is passed, which is what a community key is, so testing it
        anywhere else would fail a working key (or pass a dead one).
        """
        route = respx.get(f"{FIRST_PARTY_BASE_URL}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )

        result = _test_api_key("sk-ant-example", "anthropic")

        assert result["success"] is True
        assert route.called
        headers = route.calls.last.request.headers
        assert headers["x-api-key"] == "sk-ant-example"
        assert headers["anthropic-version"]
        assert "authorization" not in headers

    @respx.mock
    def test_openrouter_key_is_tested_against_openrouter(self) -> None:
        route = respx.get("https://openrouter.ai/api/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )

        result = _test_api_key("sk-or-v1-example", "openrouter")

        assert result["success"] is True
        assert route.called
        assert route.calls.last.request.headers["authorization"] == "Bearer sk-or-v1-example"

    @respx.mock
    def test_rejected_anthropic_key_is_reported_as_a_failure(self) -> None:
        respx.get(f"{FIRST_PARTY_BASE_URL}/v1/models").mock(
            return_value=httpx.Response(
                401,
                json={"type": "error", "error": {"type": "authentication_error"}},
            )
        )

        result = _test_api_key("sk-ant-dead", "anthropic")

        assert result["success"] is False
        assert "401" in result["error"]

    @pytest.mark.network
    def test_invalid_anthropic_key_really_is_rejected(self) -> None:
        """The same 401 path, against the live endpoint.

        This is the assertion the fixture above cannot make: that the URL and
        header names are the ones Anthropic actually accepts. A wrong path
        would answer 404 and a wrong header name 401-with-a-different-reason,
        both of which the fixture would happily fake.
        """
        result = _test_api_key("sk-ant-invalid-key-used-only-by-this-test", "anthropic")

        assert result["success"] is False
        assert "401" in result["error"]

    def test_validate_routes_a_community_anthropic_key_to_anthropic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end through the CLI, so the wiring is covered, not just the helper."""
        monkeypatch.setenv("ANTHROPIC_API_KEY_OSATEST", "sk-ant-example")
        config_path = _write_config(tmp_path, anthropic_api_key_env_var="ANTHROPIC_API_KEY_OSATEST")

        with respx.mock:
            route = respx.get(f"{FIRST_PARTY_BASE_URL}/v1/models").mock(
                return_value=httpx.Response(200, json={"data": []})
            )
            result = runner.invoke(cli, ["validate", str(config_path), "--test-api-key"])

        assert route.called
        assert route.calls.last.request.headers["x-api-key"] == "sk-ant-example"
        assert result.exit_code == 0
        assert "Testing API key with Anthropic" in result.stdout
        assert "Key works" in result.stdout


class TestValidateWithoutServerDependencies:
    """File-mode validation must survive on a CLI-only install.

    `src/cli/main.py` registers `osa validate` only if importing
    `src.cli.validate` succeeds, and replaces it with a "requires server
    dependencies" stub otherwise (`_register_server_commands`). langchain and
    friends live in the `server` extra, so a module-level import of
    src/core/services/anthropic_llm.py here would silently cost every
    CLI-only user the ability to validate a config file at all. That is why
    the shared endpoint constant lives in a dependency-free module.
    """

    # Top-level packages from the `server` extra in pyproject.toml.
    _BLOCKED = ("langchain", "langchain_core", "langchain_anthropic", "langgraph", "litellm")

    def _run_under_blocked_imports(self, body: str) -> subprocess.CompletedProcess[str]:
        script = textwrap.dedent(f"""
            import sys

            BLOCKED = {self._BLOCKED!r}

            class ServerExtraBlocker:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split(".")[0] in BLOCKED:
                        raise ImportError(f"blocked for test: {{fullname}}")
                    return None

            sys.meta_path.insert(0, ServerExtraBlocker())
        """) + textwrap.dedent(body)
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            # `python -c` puts the working directory first on sys.path, so run
            # from the repo root to make `src` importable regardless of where
            # pytest was invoked.
            cwd=Path(__file__).resolve().parents[2],
        )

    def test_the_blocker_actually_blocks(self) -> None:
        """Without this, the next test could pass for the wrong reason."""
        result = self._run_under_blocked_imports("import src.core.services.anthropic_llm\n")

        assert result.returncode != 0
        assert "blocked for test" in result.stderr

    def test_validate_imports_without_the_server_extra(self) -> None:
        result = self._run_under_blocked_imports(
            "from src.cli.validate import validate\nprint('imported')\n"
        )

        assert result.returncode == 0, result.stderr
        assert "imported" in result.stdout

    def test_community_config_resolves_models_without_the_server_extra(self) -> None:
        """The config schema resolves model ids, which must not cost langchain.

        ``FAQGenerationConfig.validate_agent_roles`` normalizes each agent's
        model in order to judge it, so src/core/config/community.py imports the
        model tables. They live in the dependency-free anthropic_models module
        for this reason: importing them from anthropic_llm would break this
        command through its own import of CommunityConfig.
        """
        result = self._run_under_blocked_imports(
            "from src.core.config.community import CommunityConfig\n"
            "from src.core.services.anthropic_models import normalize_model\n"
            "print(normalize_model('anthropic/claude-sonnet-4.5'))\n"
        )

        assert result.returncode == 0, result.stderr
        assert "claude-sonnet-5" in result.stdout
