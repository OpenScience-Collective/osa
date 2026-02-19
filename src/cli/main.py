"""OSA CLI - Thin HTTP client for Open Science Assistant.

This module is the entry point for the `osa` command. It imports ONLY
lightweight dependencies (typer, rich, httpx, pydantic, yaml) so that
`pip install open-science-assistant` stays small (~7 direct dependencies).

Server-side commands (serve, sync, validate) are conditionally registered
and require `pip install open-science-assistant[server]`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import httpx
import typer
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from src.cli import output
from src.cli.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    CREDENTIALS_FILE,
    CLIConfig,
    CredentialsConfig,
    get_data_dir,
    get_effective_config,
    get_user_id,
    is_first_run,
    load_config,
    load_credentials,
    mark_first_run_complete,
    save_config,
    save_credentials,
)
from src.version import __version__

if TYPE_CHECKING:
    from src.cli.client import OSAClient

# ---------------------------------------------------------------------------
# Main CLI app
# ---------------------------------------------------------------------------

cli = typer.Typer(
    name="osa",
    help="Open Science Assistant - AI assistants for open science projects",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# init command
# ---------------------------------------------------------------------------


@cli.command()
def init(
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            "-k",
            help="OpenRouter API key (get one at https://openrouter.ai/keys)",
        ),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
) -> None:
    """Initialize OSA CLI with your API key and preferences.

    Saves configuration to ~/.config/osa/ so you don't need to provide
    the API key for every command.

    Get an OpenRouter API key at: https://openrouter.ai/keys
    """
    config = load_config()
    creds = load_credentials()

    # Prompt for API key if not provided
    if not api_key:
        output.err_console.print()
        output.err_console.print("[bold]Welcome to OSA (Open Science Assistant)![/bold]")
        output.err_console.print()
        output.err_console.print("To use OSA, you need an OpenRouter API key.")
        output.err_console.print(
            "Get one at: [link=https://openrouter.ai/keys]https://openrouter.ai/keys[/link]"
        )
        output.err_console.print()
        api_key = typer.prompt("OpenRouter API key", hide_input=True)

    if api_key:
        creds.openrouter_api_key = api_key
    if api_url:
        config.api.url = api_url

    save_config(config)
    save_credentials(creds)

    output.print_success("Configuration saved!")
    output.print_info(f"  Config: {CONFIG_FILE}")
    output.print_info(f"  Credentials: {CREDENTIALS_FILE}")

    # Test connection
    if creds.openrouter_api_key:
        output.err_console.print()
        output.print_progress("Testing API connection")
        from src.cli.client import APIError, OSAClient

        try:
            client = OSAClient(
                api_url=config.api.url,
                openrouter_api_key=creds.openrouter_api_key,
            )
            result = client.health_check()
            status = result.get("status", "unknown")
            if status == "healthy":
                output.print_success(
                    f"Connected to {config.api.url} (v{result.get('version', '?')})"
                )
            else:
                output.print_info(f"API status: {status}")
        except APIError as e:
            output.print_error(
                f"Could not connect: {e}",
                hint="Check your API URL with --api-url",
            )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            output.print_error(f"Connection test failed: {e}")

    mark_first_run_complete()


# ---------------------------------------------------------------------------
# ask command
# ---------------------------------------------------------------------------


@cli.command()
def ask(
    question: Annotated[
        str,
        typer.Argument(help="Question to ask"),
    ],
    assistant: Annotated[
        str,
        typer.Option("--assistant", "-a", help="Community assistant ID (e.g., hed, bids, eeglab)"),
    ] = "hed",
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", help="OpenRouter API key (overrides saved config)"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: rich, json, plain"),
    ] = "rich",
    no_stream: Annotated[
        bool,
        typer.Option("--no-stream", help="Disable streaming (get full response at once)"),
    ] = False,
) -> None:
    """Ask a single question to a community assistant.

    Examples:
        osa ask "What is HED?" -a hed
        osa ask "How do I organize my dataset?" -a bids
        osa ask "What is pop_newset?" -a eeglab -o json
    """
    config, effective_key = get_effective_config(api_key=api_key, api_url=api_url)

    _check_api_key(effective_key)

    from src.cli.client import APIError, OSAClient

    client = OSAClient(
        api_url=config.api.url,
        openrouter_api_key=effective_key,
        user_id=get_user_id(),
    )

    use_streaming = not no_stream and not output.is_piped() and output_format != "json"

    try:
        if use_streaming:
            _ask_streaming(client, assistant, question)
        else:
            _ask_batch(client, assistant, question, output_format)
    except APIError as e:
        output.print_error(str(e), hint=e.detail)
        raise typer.Exit(code=1)
    except (httpx.ConnectError, httpx.TimeoutException):
        output.print_error(
            "Could not connect to API",
            hint=f"Check that {config.api.url} is reachable, or run 'osa health'",
        )
        raise typer.Exit(code=1)


def _ask_streaming(client: OSAClient, assistant: str, question: str) -> None:
    """Handle streaming ask response."""
    full_content = ""
    with output.streaming_status(f"Asking {assistant} assistant...") as status:
        for event_type, data in client.ask_stream(assistant, question):
            if event_type == "content":
                full_content += data.get("content", "")
            elif event_type == "tool_start":
                tool_name = data.get("name", "").replace("_", " ").title()
                status.update(f"[dim]Using tool: {tool_name}[/dim]")
            elif event_type == "error":
                output.print_error(data.get("message", "Unknown error"))
                raise typer.Exit(code=1)

    if full_content:
        output.print_markdown(full_content, title=assistant.upper())
    else:
        output.print_info("No response received.")


def _ask_batch(client: OSAClient, assistant: str, question: str, fmt: str) -> None:
    """Handle non-streaming ask response."""
    if not output.is_piped():
        output.print_progress(f"Asking {assistant} assistant")

    response = client.ask(assistant, question)

    if fmt == "json":
        output.print_json_output(response)
    else:
        content = response.get("answer", "No response")
        output.print_markdown(content, title=assistant.upper())


# ---------------------------------------------------------------------------
# chat command
# ---------------------------------------------------------------------------


@cli.command()
def chat(
    assistant: Annotated[
        str,
        typer.Option("--assistant", "-a", help="Community assistant ID (e.g., hed, bids, eeglab)"),
    ] = "hed",
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", help="OpenRouter API key (overrides saved config)"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
    no_stream: Annotated[
        bool,
        typer.Option("--no-stream", help="Disable streaming"),
    ] = False,
) -> None:
    """Start an interactive chat session with a community assistant.

    Examples:
        osa chat -a hed
        osa chat -a bids
        osa chat -a eeglab --no-stream
    """
    config, effective_key = get_effective_config(api_key=api_key, api_url=api_url)

    _check_api_key(effective_key)

    from src.cli.client import APIError, OSAClient

    client = OSAClient(
        api_url=config.api.url,
        openrouter_api_key=effective_key,
        user_id=get_user_id(),
    )

    use_streaming = not no_stream

    output.console.print(
        Panel(
            f"[bold]OSA Chat[/bold] - {assistant} assistant\n"
            f"[dim]Connected to {config.api.url}[/dim]\n"
            "[dim]Type 'quit' or 'exit' to end the session[/dim]",
            border_style="blue",
        )
    )

    session_id = None

    while True:
        try:
            user_input = output.console.input("[bold green]You:[/bold green] ").strip()

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                output.print_info("Goodbye!")
                break

            if use_streaming:
                session_id = _chat_turn_streaming(client, assistant, user_input, session_id)
            else:
                session_id = _chat_turn_batch(client, assistant, user_input, session_id)

        except KeyboardInterrupt:
            output.err_console.print("\n[dim]Interrupted. Goodbye![/dim]")
            break
        except APIError as e:
            output.print_error(str(e), hint=e.detail)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            output.print_error(
                f"Connection problem: {e}",
                hint=f"Check that {config.api.url} is reachable",
            )


def _chat_turn_streaming(
    client: OSAClient,
    assistant: str,
    message: str,
    session_id: str | None,
) -> str | None:
    """Handle one streaming chat turn. Returns the session_id."""
    full_content = ""
    new_session_id = session_id

    with output.streaming_status("Thinking...") as status:
        for event_type, data in client.chat_stream(assistant, message, session_id):
            if event_type == "content":
                full_content += data.get("content", "")
            elif event_type == "session":
                new_session_id = data.get("session_id", session_id)
            elif event_type == "tool_start":
                tool_name = data.get("name", "").replace("_", " ").title()
                status.update(f"[dim]Using tool: {tool_name}[/dim]")
            elif event_type == "done":
                new_session_id = data.get("session_id", new_session_id)
            elif event_type == "error":
                output.print_error(data.get("message", "Unknown error"))
                return new_session_id

    if full_content:
        output.console.print()
        output.console.print(f"[bold blue]{assistant}:[/bold blue]")
        output.console.print(Markdown(full_content))
        output.console.print()

    return new_session_id


def _chat_turn_batch(
    client: OSAClient,
    assistant: str,
    message: str,
    session_id: str | None,
) -> str | None:
    """Handle one non-streaming chat turn. Returns the session_id."""
    with output.streaming_status("Thinking..."):
        response = client.chat(assistant, message, session_id)

    new_session_id = response.get("session_id", session_id)

    tool_calls = response.get("tool_calls", [])
    if tool_calls:
        output.console.print()
        for tc in tool_calls:
            name = tc.get("name", "unknown").replace("_", " ").title()
            output.console.print(f"[dim](Used tool: {name})[/dim]")

    content = response.get("message", {}).get("content", "No response")
    output.console.print()
    output.console.print(f"[bold blue]{assistant}:[/bold blue]")
    output.console.print(Markdown(content))
    output.console.print()

    return new_session_id


# ---------------------------------------------------------------------------
# version command
# ---------------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Show OSA version information."""
    output.console.print(f"OSA v{__version__}")


# ---------------------------------------------------------------------------
# health command
# ---------------------------------------------------------------------------


@cli.command()
def health(
    url: Annotated[
        str | None,
        typer.Option("--url", "-u", help="API URL to check"),
    ] = None,
) -> None:
    """Check API health status."""
    config = load_config()
    api_url = url or config.api.url

    from src.cli.client import APIError, OSAClient

    client = OSAClient(api_url=api_url)

    try:
        result = client.health_check()
        status = result.get("status", "unknown")
        ver = result.get("version", "unknown")
        environment = result.get("environment", "unknown")

        if status == "healthy":
            output.console.print(
                Panel(
                    f"[green]Status:[/green] {status}\n"
                    f"[blue]Version:[/blue] {ver}\n"
                    f"[yellow]Environment:[/yellow] {environment}",
                    title="[bold green]API Health[/bold green]",
                    border_style="green",
                )
            )
        else:
            output.print_info(f"Status: {status}")
    except APIError as e:
        output.print_error(f"API error: {e}", hint=e.detail)
        raise typer.Exit(code=1)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        output.print_error(
            f"Could not connect to {api_url}: {e}",
            hint="Is the server running? Check the URL with --url",
        )
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# config subcommands
# ---------------------------------------------------------------------------

config_app = typer.Typer(help="Manage CLI configuration")
cli.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    config = load_config()
    creds = load_credentials()

    table = Table(title="OSA Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    # Config settings (nested)
    table.add_row("api.url", config.api.url)
    table.add_row("output.format", config.output.format)
    table.add_row("output.verbose", str(config.output.verbose))
    table.add_row("output.streaming", str(config.output.streaming))

    # Credentials (masked)
    for field, value in creds.model_dump().items():
        if value:
            display = f"{value[:8]}..." if len(value) > 8 else "***"
        else:
            display = "[dim]not set[/dim]"
        table.add_row(field, display)

    output.console.print(table)
    output.console.print(f"\n[dim]Config: {CONFIG_FILE}[/dim]")
    output.console.print(f"[dim]Credentials: {CREDENTIALS_FILE}[/dim]")


@config_app.command("set")
def config_set(
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="API URL"),
    ] = None,
    openrouter_key: Annotated[
        str | None,
        typer.Option("--openrouter-key", help="OpenRouter API key"),
    ] = None,
    output_format: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Output format: rich, json, plain"),
    ] = None,
    verbose: Annotated[
        bool | None,
        typer.Option("--verbose/--no-verbose", "-v", help="Enable verbose output"),
    ] = None,
    streaming: Annotated[
        bool | None,
        typer.Option("--streaming/--no-streaming", help="Enable streaming"),
    ] = None,
) -> None:
    """Update configuration settings."""
    config = load_config()
    creds = load_credentials()
    updated = False

    if api_url is not None:
        config.api.url = api_url
        updated = True
    if output_format is not None:
        if output_format not in ("rich", "json", "plain"):
            output.print_error("Invalid output format. Use: rich, json, plain")
            raise typer.Exit(code=1)
        config.output.format = output_format
        updated = True
    if verbose is not None:
        config.output.verbose = verbose
        updated = True
    if streaming is not None:
        config.output.streaming = streaming
        updated = True
    if openrouter_key is not None:
        creds.openrouter_api_key = openrouter_key
        save_credentials(creds)
        updated = True

    if updated:
        save_config(config)
        output.print_success("Configuration updated.")
    else:
        output.print_info("No changes made. Use --help to see available options.")


@config_app.command("path")
def config_path() -> None:
    """Show configuration and data directory paths."""
    output.console.print(f"[cyan]Config directory:[/cyan] {CONFIG_DIR}")
    output.console.print(f"[cyan]Config file:[/cyan] {CONFIG_FILE}")
    output.console.print(f"[cyan]Credentials file:[/cyan] {CREDENTIALS_FILE}")
    output.console.print(f"[cyan]Data directory:[/cyan] {get_data_dir()}")


@config_app.command("reset")
def config_reset(
    confirm: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Reset configuration to defaults."""
    if not confirm:
        confirm = typer.confirm("Reset configuration to defaults?")

    if confirm:
        save_config(CLIConfig())
        save_credentials(CredentialsConfig())
        output.print_success("Configuration reset to defaults.")
    else:
        output.print_info("Cancelled.")


# ---------------------------------------------------------------------------
# Server-only commands (conditionally registered)
# ---------------------------------------------------------------------------


def _register_server_commands() -> None:
    """Register commands that require server dependencies.

    These commands need the [server] extra:
      pip install open-science-assistant[server]
    """

    # serve command (uvicorn is a server dep)
    @cli.command()
    def serve(
        host: Annotated[
            str,
            typer.Option("--host", "-h", help="Host to bind to"),
        ] = "0.0.0.0",
        port: Annotated[
            int,
            typer.Option("--port", "-p", help="Port to bind to"),
        ] = 38528,
        reload: Annotated[
            bool,
            typer.Option("--reload", "-r", help="Enable auto-reload"),
        ] = False,
    ) -> None:
        """Start the OSA API server (requires server dependencies)."""
        try:
            import uvicorn
        except ImportError:
            output.print_error(
                "Server dependencies not installed.",
                hint=r"Install with: pip install 'open-science-assistant\[server]'",
            )
            raise typer.Exit(code=1)

        output.print_info(f"Starting OSA server on {host}:{port}")
        uvicorn.run("src.api.main:app", host=host, port=port, reload=reload)

    _SERVER_DEP_HINT = r"Install with: pip install 'open-science-assistant\[server]'"

    # sync commands
    try:
        from src.cli.sync import sync_app

        cli.add_typer(sync_app, name="sync")
    except ImportError:

        @cli.command(name="sync", hidden=True)
        def sync_stub() -> None:
            """Sync knowledge sources (requires server dependencies)."""
            output.print_error("Server dependencies not installed.", hint=_SERVER_DEP_HINT)
            raise typer.Exit(code=1)

    # validate command
    try:
        from src.cli.validate import validate as validate_command

        cli.command(name="validate")(validate_command)
    except ImportError:

        @cli.command(name="validate", hidden=True)
        def validate_stub() -> None:
            """Validate community config (requires server dependencies)."""
            output.print_error("Server dependencies not installed.", hint=_SERVER_DEP_HINT)
            raise typer.Exit(code=1)


_register_server_commands()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_api_key(key: str | None) -> None:
    """Check that an API key is available, exit with helpful message if not."""
    if not key:
        output.print_error(
            "No API key configured.",
            hint="Run 'osa init' to set up your API key, or pass --api-key",
        )
        raise typer.Exit(code=1)

    if is_first_run():
        mark_first_run_complete()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
