"""Tests for CLI commands.

These tests use Typer's CliRunner to test CLI commands
with real output verification.
"""

from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import httpx
import respx
from click import unstyle
from typer.testing import CliRunner

from src.cli.client import OSAClient
from src.cli.config import CLIConfig, load_credentials, save_config
from src.cli.main import _ask_streaming, _chat_turn_streaming, cli
from tests.test_cli.test_config import patched_config_paths

runner = CliRunner()


class TestVersionCommand:
    """Tests for the version command."""

    def test_version_shows_version(self) -> None:
        """version command should display version number."""
        from src.version import __version__

        result = runner.invoke(cli, ["version"])
        assert result.exit_code == 0
        # Unstyled: Rich colors part of the version when FORCE_COLOR is set (#507).
        assert f"OSA v{__version__}" in unstyle(result.output)

    def test_version_reads_the_same_when_styled(self, monkeypatch) -> None:
        """A styled console, as under FORCE_COLOR=1, still shows the version (#507)."""
        from rich.console import Console

        from src.cli import output
        from src.version import __version__

        monkeypatch.setattr(
            output, "console", Console(force_terminal=True, color_system="standard")
        )
        result = runner.invoke(cli, ["version"])
        assert result.exit_code == 0
        assert "\x1b[" in result.output, (
            "the console did not style its output, so this test proves nothing"
        )
        assert f"OSA v{__version__}" in unstyle(result.output)


class TestHealthCommand:
    """Tests for the health command."""

    def test_health_with_invalid_url_shows_error(self, tmp_path: Path) -> None:
        """health command should show error for invalid URL."""
        with patched_config_paths(tmp_path):
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

        with (
            patched_config_paths(tmp_path),
            patch("src.cli.main.CONFIG_FILE", config_file),
            patch("src.cli.main.CREDENTIALS_FILE", creds_file),
        ):
            save_config(CLIConfig(api={"url": "https://test.example.com"}))
            result = runner.invoke(cli, ["config", "show"])

        assert result.exit_code == 0
        assert "api.url" in result.output

    def test_config_set_updates_api_url(self, tmp_path: Path) -> None:
        """config set should update api_url."""
        with patched_config_paths(tmp_path):
            result = runner.invoke(cli, ["config", "set", "--api-url", "https://new-url.com"])

        assert result.exit_code == 0
        assert "updated" in result.output.lower()

    def test_config_set_validates_output_format(self, tmp_path: Path) -> None:
        """config set should validate output format values."""
        with patched_config_paths(tmp_path):
            result = runner.invoke(cli, ["config", "set", "--output", "invalid"])

        assert result.exit_code == 1
        assert "Invalid output format" in result.output

    def test_config_set_accepts_valid_output_formats(self, tmp_path: Path) -> None:
        """config set should accept valid output format values."""
        for format_type in ["rich", "json", "plain"]:
            with patched_config_paths(tmp_path):
                result = runner.invoke(cli, ["config", "set", "--output", format_type])
            assert result.exit_code == 0, f"Failed for format: {format_type}"

    def test_config_set_no_options_shows_message(self, tmp_path: Path) -> None:
        """config set with no options should show help message."""
        with patched_config_paths(tmp_path):
            result = runner.invoke(cli, ["config", "set"])

        assert result.exit_code == 0
        assert "No changes made" in result.output

    def test_config_set_saves_anthropic_key(self, tmp_path: Path) -> None:
        """config set --anthropic-key persists to the Anthropic slot.

        Without this flag the only way to persist an Anthropic key was to
        hand-edit credentials.yaml or export the env var in every shell.
        """
        with patched_config_paths(tmp_path):
            result = runner.invoke(cli, ["config", "set", "--anthropic-key", "sk-ant-api03-saved"])
            creds = load_credentials()

        assert result.exit_code == 0, result.output
        assert creds.anthropic_api_key == "sk-ant-api03-saved"
        assert creds.openrouter_api_key is None

    def test_config_set_keys_do_not_overwrite_each_other(self, tmp_path: Path) -> None:
        """Setting one provider's key leaves the other provider's key alone."""
        with patched_config_paths(tmp_path):
            runner.invoke(cli, ["config", "set", "--openrouter-key", "sk-or-v1-saved"])
            runner.invoke(cli, ["config", "set", "--anthropic-key", "sk-ant-api03-saved"])
            creds = load_credentials()

        assert creds.openrouter_api_key == "sk-or-v1-saved"
        assert creds.anthropic_api_key == "sk-ant-api03-saved"

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
        with patched_config_paths(tmp_path):
            result = runner.invoke(cli, ["config", "reset", "--yes"])

        assert result.exit_code == 0
        assert "reset to defaults" in result.output.lower()


class TestInitCommand:
    """`osa init` is the first thing a new CLI user runs.

    Before the Claude Platform migration it prompted for an OpenRouter key
    and saved whatever it got into the OpenRouter slot, so an Anthropic key
    typed at that prompt would have gone out on the wrong header and failed.
    The provider now comes from the key's own prefix.
    """

    def test_init_saves_anthropic_key_to_anthropic_slot(self, tmp_path: Path) -> None:
        with (
            patched_config_paths(tmp_path),
            patch.dict("os.environ", {}, clear=True),
            respx.mock,
        ):
            respx.get("https://api.osc.earth/osa/health").mock(
                return_value=httpx.Response(200, json={"status": "healthy", "version": "0.0.0"})
            )
            result = runner.invoke(cli, ["init", "--api-key", "sk-ant-api03-typed"])
            creds = load_credentials()

        assert result.exit_code == 0, result.output
        assert creds.anthropic_api_key == "sk-ant-api03-typed"
        assert creds.openrouter_api_key is None

    def test_init_saves_openrouter_key_to_openrouter_slot(self, tmp_path: Path) -> None:
        with (
            patched_config_paths(tmp_path),
            patch.dict("os.environ", {}, clear=True),
            respx.mock,
        ):
            respx.get("https://api.osc.earth/osa/health").mock(
                return_value=httpx.Response(200, json={"status": "healthy", "version": "0.0.0"})
            )
            result = runner.invoke(cli, ["init", "--api-key", "sk-or-v1-typed"])
            creds = load_credentials()

        assert result.exit_code == 0, result.output
        assert creds.openrouter_api_key == "sk-or-v1-typed"
        assert creds.anthropic_api_key is None

    def test_init_connection_test_uses_the_key_it_just_saved(self, tmp_path: Path) -> None:
        """init's own connection test must go out on the saved key's header.

        Asserting on the header rather than merely that the request happened:
        the previous code stored an Anthropic key in the OpenRouter slot and
        then built its test client from that slot, so a "was the health
        endpoint called" assertion passed even though the request carried the
        key on the wrong header, where the server could not use it.
        """
        with (
            patched_config_paths(tmp_path),
            patch.dict("os.environ", {}, clear=True),
            respx.mock,
        ):
            route = respx.get("https://api.osc.earth/osa/health").mock(
                return_value=httpx.Response(200, json={"status": "healthy", "version": "9.9.9"})
            )
            result = runner.invoke(cli, ["init", "--api-key", "sk-ant-api03-typed"])

        assert route.called, "init skipped its connection test for an Anthropic-only setup"
        sent = route.calls.last.request
        assert sent.headers["X-Anthropic-API-Key"] == "sk-ant-api03-typed"
        assert "X-OpenRouter-Key" not in sent.headers
        assert "9.9.9" in unstyle(result.output)

    def test_init_help_points_at_both_providers(self) -> None:
        result = runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0
        plain = unstyle(result.output)
        assert "Anthropic" in plain
        assert "OpenRouter" in plain


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
        with (
            patched_config_paths(tmp_path),
            patch("src.cli.config.FIRST_RUN_FILE", tmp_path / ".first_run"),
            patch.dict("os.environ", {}, clear=True),
        ):
            result = runner.invoke(cli, ["ask", "test question"])

        assert result.exit_code == 1
        assert "No API key" in result.output

    def test_ask_with_only_anthropic_key_sends_anthropic_header(self, tmp_path: Path) -> None:
        """A user with only ANTHROPIC_API_KEY configured must not take the
        "No API key" path, and the outgoing request must carry
        X-Anthropic-API-Key.

        get_effective_byok_keys() resolves both providers and _check_api_key
        accepts either, so an Anthropic-only user should sail through.
        respx mocks the HTTP boundary so this runs offline: the CLI's own
        client (src/cli/client.py) builds its own httpx.Client per call with
        no transport injection point, so respx (rather than
        httpx.MockTransport) is what the project rules call for here.
        """
        with (
            patched_config_paths(tmp_path),
            patch("src.cli.config.FIRST_RUN_FILE", tmp_path / ".first_run"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-cli-test-key"}, clear=True),
            respx.mock,
        ):
            route = respx.post("https://api.osc.earth/osa/hed/ask").mock(
                return_value=httpx.Response(
                    200, json={"answer": "Mocked answer.", "tool_calls": [], "model": "test-model"}
                )
            )
            result = runner.invoke(cli, ["ask", "test question", "-a", "hed"])

        assert "No API key" not in result.output
        assert result.exit_code == 0, result.output
        assert route.called
        sent_request = route.calls.last.request
        assert sent_request.headers["X-Anthropic-API-Key"] == "sk-ant-cli-test-key"
        assert "X-OpenRouter-Key" not in sent_request.headers


class TestChatCommand:
    """Tests for the chat command."""

    def test_chat_help_shows_options(self) -> None:
        """chat --help should show assistant options."""
        result = runner.invoke(cli, ["chat", "--help"])
        assert result.exit_code == 0
        clean = unstyle(result.output)
        assert "--assistant" in clean
        assert "--api-key" in clean

    def test_chat_with_only_anthropic_key_sends_anthropic_header(self, tmp_path: Path) -> None:
        """chat resolves BYOK keys separately from ask, so it needs its own check.

        The REPL is driven by feeding one question and then "quit" on stdin,
        with --no-stream so the turn is a single POST that respx can inspect.

        The response body is serialized from the server's own ChatResponse
        model rather than hand-written, because hand-writing it gets the shape
        wrong: `message` is a nested ChatMessage object, not a string, and the
        CLI reads `response["message"]["content"]`. Building from the model
        means the fixture cannot drift from the contract.
        """
        from src.api.routers.community import ChatMessage, ChatResponse

        chat_response = ChatResponse(
            session_id="sess-1",
            message=ChatMessage(role="assistant", content="Mocked reply."),
            model="test-model",
        )

        with (
            patched_config_paths(tmp_path),
            patch("src.cli.config.FIRST_RUN_FILE", tmp_path / ".first_run"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-cli-test-key"}, clear=True),
            respx.mock,
        ):
            route = respx.post("https://api.osc.earth/osa/hed/chat").mock(
                return_value=httpx.Response(200, json=chat_response.model_dump(mode="json"))
            )
            result = runner.invoke(
                cli,
                ["chat", "-a", "hed", "--no-stream"],
                input="test question\nquit\n",
            )

        assert "No API key" not in result.output
        assert result.exit_code == 0, result.output
        assert route.called
        sent_request = route.calls.last.request
        assert sent_request.headers["X-Anthropic-API-Key"] == "sk-ant-cli-test-key"
        assert "X-OpenRouter-Key" not in sent_request.headers


class TestStreamingCitationContent:
    """Streaming clients must replace raw chunks with authoritative done content."""

    def test_ask_uses_normalized_done_content(self) -> None:
        with (
            respx.mock,
            patch("src.cli.main.output.streaming_status", return_value=nullcontext()),
            patch("src.cli.main.output.print_markdown") as print_markdown,
        ):
            route = respx.post("https://test.example/hed/ask").mock(
                return_value=httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=(
                        b'data: {"event":"content","content":"Infomax in ru[1]nica.m"}\n\n'
                        b'data: {"event":"done","content":"Infomax in runica.m.[1]"}\n\n'
                    ),
                )
            )
            _ask_streaming(OSAClient("https://test.example", user_id="test-user"), "hed", "How?")

        assert route.called
        print_markdown.assert_called_once_with("Infomax in runica.m.[1]", title="HED")

    def test_chat_uses_normalized_done_content_and_keeps_session(self) -> None:
        with (
            respx.mock,
            patch("src.cli.main.output.streaming_status", return_value=nullcontext()),
            patch("src.cli.main.output.console.print"),
            patch("src.cli.main.Markdown", return_value="final-markdown") as markdown,
        ):
            route = respx.post("https://test.example/hed/chat").mock(
                return_value=httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=(
                        b'data: {"event":"content","content":"ru[1]nica.m"}\n\n'
                        b'data: {"event":"done","session_id":"session-2",'
                        b'"content":"runica.m.[1]"}\n\n'
                    ),
                )
            )
            session_id = _chat_turn_streaming(
                OSAClient("https://test.example", user_id="test-user"),
                "hed",
                "How?",
                "session-1",
            )

        assert route.called
        assert session_id == "session-2"
        markdown.assert_called_once_with("runica.m.[1]")
