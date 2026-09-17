"""Tests for the `osa mirror` command group.

The focus here is BYOK key resolution in `_get_client`, which every mirror
subcommand routes through. Before the Claude Platform migration it resolved
an OpenRouter key only, so an Anthropic-only user hit "No API key configured"
on commands that would have worked.

Assertions are made against the outgoing request rather than against
`_get_client`'s return value: what matters is which header lands on the wire,
and a test that only inspected the client object would still pass if the
header names were wrong. respx mocks the HTTP boundary so these run offline,
since `src/cli/client.py` builds its own `httpx.Client` per call with no
transport injection point.
"""

from pathlib import Path
from unittest.mock import patch

import httpx
import respx
from typer.testing import CliRunner

from src.cli.main import cli
from tests.test_cli.test_config import patched_config_paths

runner = CliRunner()

ANTHROPIC_KEY = "sk-ant-mirror-test-key"
OPENROUTER_KEY = "sk-or-v1-mirror-test-key"

MIRROR_LIST_RESPONSE: list[dict[str, object]] = [
    {
        "mirror_id": "abc123def456",
        "community_ids": ["hed"],
        "label": "testing",
        "expires_at": "2026-09-19T00:00:00Z",
        "size_bytes": 1024,
    }
]


class TestMirrorByokKeyResolution:
    """`_get_client` must accept either provider's key and send the right header."""

    def test_list_with_only_anthropic_key_sends_anthropic_header(self, tmp_path: Path) -> None:
        """An Anthropic-only user must reach the API, with X-Anthropic-API-Key."""
        with (
            patched_config_paths(tmp_path),
            patch("src.cli.config.FIRST_RUN_FILE", tmp_path / ".first_run"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": ANTHROPIC_KEY}, clear=True),
            respx.mock,
        ):
            route = respx.get("https://api.osc.earth/osa/mirrors").mock(
                return_value=httpx.Response(200, json=MIRROR_LIST_RESPONSE)
            )
            result = runner.invoke(cli, ["mirror", "list"])

        assert "No API key configured" not in result.output
        assert result.exit_code == 0, result.output
        assert route.called
        sent = route.calls.last.request
        assert sent.headers["X-Anthropic-API-Key"] == ANTHROPIC_KEY
        assert "X-OpenRouter-Key" not in sent.headers

    def test_create_with_only_anthropic_key_sends_anthropic_header(self, tmp_path: Path) -> None:
        """Every subcommand shares `_get_client`; check a second one on a POST."""
        with (
            patched_config_paths(tmp_path),
            patch("src.cli.config.FIRST_RUN_FILE", tmp_path / ".first_run"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": ANTHROPIC_KEY}, clear=True),
            respx.mock,
        ):
            route = respx.post("https://api.osc.earth/osa/mirrors").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "mirror_id": "abc123def456",
                        "community_ids": ["hed"],
                        "expires_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
            result = runner.invoke(cli, ["mirror", "create", "-c", "hed"])

        assert result.exit_code == 0, result.output
        assert route.called
        sent = route.calls.last.request
        assert sent.headers["X-Anthropic-API-Key"] == ANTHROPIC_KEY
        assert "X-OpenRouter-Key" not in sent.headers

    def test_list_with_only_openrouter_key_still_sends_openrouter_header(
        self, tmp_path: Path
    ) -> None:
        """The pre-migration path must be untouched: OpenRouter keys still work."""
        with (
            patched_config_paths(tmp_path),
            patch("src.cli.config.FIRST_RUN_FILE", tmp_path / ".first_run"),
            patch.dict("os.environ", {"OPENROUTER_API_KEY": OPENROUTER_KEY}, clear=True),
            respx.mock,
        ):
            route = respx.get("https://api.osc.earth/osa/mirrors").mock(
                return_value=httpx.Response(200, json=MIRROR_LIST_RESPONSE)
            )
            result = runner.invoke(cli, ["mirror", "list"])

        assert result.exit_code == 0, result.output
        assert route.called
        sent = route.calls.last.request
        assert sent.headers["X-OpenRouter-Key"] == OPENROUTER_KEY
        assert "X-Anthropic-API-Key" not in sent.headers

    def test_explicit_anthropic_flag_beats_an_exported_openrouter_key(self, tmp_path: Path) -> None:
        """`-k sk-ant-...` selects its own provider and empties the other slot.

        Without that, the ambient OpenRouter key would also be sent, and
        `OSAClient` prefers Anthropic when both are present, so the request
        would carry a key the user did not ask for.
        """
        with (
            patched_config_paths(tmp_path),
            patch("src.cli.config.FIRST_RUN_FILE", tmp_path / ".first_run"),
            patch.dict("os.environ", {"OPENROUTER_API_KEY": OPENROUTER_KEY}, clear=True),
            respx.mock,
        ):
            route = respx.get("https://api.osc.earth/osa/mirrors").mock(
                return_value=httpx.Response(200, json=MIRROR_LIST_RESPONSE)
            )
            result = runner.invoke(cli, ["mirror", "list", "-k", ANTHROPIC_KEY])

        assert result.exit_code == 0, result.output
        assert route.called
        sent = route.calls.last.request
        assert sent.headers["X-Anthropic-API-Key"] == ANTHROPIC_KEY
        assert "X-OpenRouter-Key" not in sent.headers

    def test_list_without_any_key_still_errors(self, tmp_path: Path) -> None:
        """Resolving two providers must not make the no-key check unreachable."""
        with (
            patched_config_paths(tmp_path),
            patch("src.cli.config.FIRST_RUN_FILE", tmp_path / ".first_run"),
            patch.dict("os.environ", {}, clear=True),
        ):
            result = runner.invoke(cli, ["mirror", "list"])

        assert result.exit_code == 1
        assert "No API key configured" in result.output


class TestMirrorHelpNamesBothProviders:
    """The `--api-key` help text should not advertise OpenRouter alone."""

    def test_help_strings_mention_anthropic(self) -> None:
        """Every mirror subcommand that takes --api-key names both providers."""
        from click import unstyle

        subcommands = ["create", "list", "info", "delete", "refresh", "pull"]
        for name in subcommands:
            result = runner.invoke(cli, ["mirror", name, "--help"])
            assert result.exit_code == 0, f"{name}: {result.output}"
            clean = unstyle(result.output)
            if "--api-key" not in clean:
                continue
            assert "Anthropic" in clean, f"{name} --api-key help omits Anthropic"
