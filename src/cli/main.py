"""Typer CLI for Open Science Assistant."""

import threading
import time
from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from src.cli.client import OSAClient
from src.cli.config import (
    CLIConfig,
    get_config_dir,
    get_config_path,
    get_data_dir,
    load_config,
    save_config,
)

# Rich console for formatted output
console = Console()

# Available assistants registry
ASSISTANTS = {
    "hed": {
        "name": "HED",
        "description": "Hierarchical Event Descriptors - annotation standard for neuroimaging",
        "status": "available",
    },
    "bids": {
        "name": "BIDS",
        "description": "Brain Imaging Data Structure - data organization standard",
        "status": "coming soon",
    },
    "eeglab": {
        "name": "EEGLAB",
        "description": "EEG analysis toolbox for MATLAB",
        "status": "coming soon",
    },
}


def display_tool_calls(tool_calls: list[dict]) -> None:
    """Display tool calls in a user-friendly format."""
    if not tool_calls:
        return
    for tc in tool_calls:
        name = tc.get("name", "unknown")
        readable_name = name.replace("_", " ").title()
        console.print(f"[dim](Using tool: {readable_name})[/dim]")


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

_server_thread: threading.Thread | None = None
_server_started = threading.Event()


def _run_server(host: str, port: int) -> None:
    """Run the FastAPI server in a thread."""
    import os

    import uvicorn

    # Disable API auth for standalone mode (local CLI use only)
    # This is safe because:
    # 1. Server only binds to localhost (127.0.0.1)
    # 2. User still needs to provide LLM API key (BYOK) for actual responses
    # 3. Does NOT affect 'osa serve' which reads from .env
    os.environ["REQUIRE_API_AUTH"] = "false"

    from src.api.main import app

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    def signal_started() -> None:
        time.sleep(0.5)
        _server_started.set()

    threading.Thread(target=signal_started, daemon=True).start()
    server.run()


def start_standalone_server(host: str = "127.0.0.1", port: int = 38528) -> str:
    """Start the API server in standalone mode."""
    global _server_thread

    if _server_thread is not None and _server_thread.is_alive():
        return f"http://{host}:{port}"

    _server_started.clear()
    _server_thread = threading.Thread(target=_run_server, args=(host, port), daemon=True)
    _server_thread.start()
    _server_started.wait(timeout=5.0)
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Assistant command factory
# ---------------------------------------------------------------------------


def create_assistant_app(assistant_id: str, assistant_info: dict) -> typer.Typer:
    """Create a Typer app for an assistant with ask and chat commands."""
    app = typer.Typer(
        help=f"{assistant_info['name']} Assistant - {assistant_info['description']}",
        no_args_is_help=True,
    )

    @app.command()
    def ask(
        question: Annotated[
            str,
            typer.Argument(help="Question to ask the assistant"),
        ],
        standalone: Annotated[
            bool,
            typer.Option("--standalone", "-s", help="Run in standalone mode (no external server)"),
        ] = True,
        url: Annotated[
            str | None,
            typer.Option("--url", "-u", help="API URL (overrides standalone)"),
        ] = None,
    ) -> None:
        """Ask a single question.

        Example:
            osa hed ask "What is HED?"
            osa hed ask "How do I annotate events?"
        """
        if assistant_info["status"] != "available":
            console.print(
                f"[yellow]{assistant_info['name']} assistant is {assistant_info['status']}.[/yellow]"
            )
            raise typer.Exit(code=1)

        config = load_config()

        # Determine API URL
        if url:
            api_url = url
        elif standalone:
            with console.status("[bold green]Starting standalone server..."):
                api_url = start_standalone_server()
        else:
            api_url = config.api_url

        config.api_url = api_url
        client = OSAClient(config)

        with console.status(f"[bold green]Asking {assistant_info['name']} assistant..."):
            try:
                response = client.chat(
                    message=question,
                    assistant=assistant_id,
                    stream=False,
                )

                if "error" in response:
                    console.print(f"[red]Error:[/red] {response['error']}")
                    raise typer.Exit(code=1)

                tool_calls = response.get("tool_calls", [])
                if tool_calls:
                    console.print()
                    display_tool_calls(tool_calls)

                content = response.get("message", {}).get("content", "No response")
                console.print()
                console.print(
                    Panel(Markdown(content), title=f"[bold]{assistant_info['name']}[/bold]")
                )

            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                raise typer.Exit(code=1)

    @app.command()
    def chat(
        standalone: Annotated[
            bool,
            typer.Option("--standalone", "-s", help="Run in standalone mode (no external server)"),
        ] = True,
        url: Annotated[
            str | None,
            typer.Option("--url", "-u", help="API URL (overrides standalone)"),
        ] = None,
    ) -> None:
        """Start an interactive chat session.

        Example:
            osa hed chat
            osa hed chat --url http://localhost:38528
        """
        if assistant_info["status"] != "available":
            console.print(
                f"[yellow]{assistant_info['name']} assistant is {assistant_info['status']}.[/yellow]"
            )
            raise typer.Exit(code=1)

        config = load_config()

        # Determine API URL
        if url:
            api_url = url
        elif standalone:
            with console.status("[bold green]Starting standalone server..."):
                api_url = start_standalone_server()
            console.print(f"[dim]Server running at {api_url}[/dim]")
        else:
            api_url = config.api_url

        config.api_url = api_url
        client = OSAClient(config)

        console.print(
            Panel(
                f"[bold]OSA Chat[/bold] - {assistant_info['name']} Assistant\n"
                "[dim]Type 'quit' or 'exit' to end the session[/dim]",
                border_style="blue",
            )
        )

        session_id = None

        while True:
            try:
                user_input = console.input("[bold green]You:[/bold green] ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ("quit", "exit", "q"):
                    console.print("[dim]Goodbye![/dim]")
                    break

                with console.status("[bold green]Thinking..."):
                    response = client.chat(
                        message=user_input,
                        assistant=assistant_id,
                        session_id=session_id,
                        stream=False,
                    )

                if "error" in response:
                    console.print(f"[red]Error:[/red] {response['error']}")
                    continue

                session_id = response.get("session_id")

                tool_calls = response.get("tool_calls", [])
                if tool_calls:
                    console.print()
                    display_tool_calls(tool_calls)

                content = response.get("message", {}).get("content", "No response")
                console.print()
                console.print(f"[bold blue]{assistant_info['name']}:[/bold blue]")
                console.print(Markdown(content))
                console.print()

            except KeyboardInterrupt:
                console.print("\n[dim]Interrupted. Goodbye![/dim]")
                break
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")

    return app


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

cli = typer.Typer(
    name="osa",
    help="Open Science Assistant - AI assistants for open science projects",
    no_args_is_help=False,  # Allow bare `osa` to show assistants
    invoke_without_command=True,
)


@cli.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context) -> None:
    """Show available assistants when no command is given."""
    if ctx.invoked_subcommand is None:
        # Show available assistants
        console.print(
            Panel(
                "[bold]Open Science Assistant[/bold]\nAI assistants for open science projects",
                border_style="blue",
            )
        )
        console.print()

        table = Table(title="Available Assistants")
        table.add_column("Assistant", style="cyan", no_wrap=True)
        table.add_column("Description", style="white")
        table.add_column("Status", style="green")

        for assistant_id, info in ASSISTANTS.items():
            status_style = "green" if info["status"] == "available" else "yellow"
            table.add_row(
                f"osa {assistant_id}",
                info["description"],
                f"[{status_style}]{info['status']}[/{status_style}]",
            )

        console.print(table)
        console.print()
        console.print("[dim]Usage: osa <assistant> <command> [options][/dim]")
        console.print('[dim]Example: osa hed ask "What is HED?"[/dim]')
        console.print()
        console.print("[dim]Global commands: osa version, osa serve, osa config[/dim]")


# Register assistant subcommands
for assistant_id, assistant_info in ASSISTANTS.items():
    cli.add_typer(
        create_assistant_app(assistant_id, assistant_info),
        name=assistant_id,
    )


# ---------------------------------------------------------------------------
# Global commands
# ---------------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Show OSA version information."""
    from src.api.config import get_settings

    settings = get_settings()
    console.print(f"OSA v{settings.app_version}")


@cli.command()
def health(
    url: Annotated[
        str | None,
        typer.Option("--url", "-u", help="API URL to check"),
    ] = None,
) -> None:
    """Check API health status."""
    config = load_config()
    if url:
        config.api_url = url

    client = OSAClient(config)

    try:
        result = client.health_check()
        status = result.get("status", "unknown")
        ver = result.get("version", "unknown")
        environment = result.get("environment", "unknown")

        if status == "healthy":
            console.print(
                Panel(
                    f"[green]Status:[/green] {status}\n"
                    f"[blue]Version:[/blue] {ver}\n"
                    f"[yellow]Environment:[/yellow] {environment}",
                    title="[bold green]API Health[/bold green]",
                    border_style="green",
                )
            )
        else:
            console.print(f"[yellow]Status: {status}[/yellow]")
    except Exception as e:
        console.print(f"[red]Error connecting to API:[/red] {e}")
        raise typer.Exit(code=1)


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
        typer.Option("--reload", "-r", help="Enable auto-reload for development"),
    ] = False,
) -> None:
    """Start the OSA API server."""
    import uvicorn

    console.print(f"[green]Starting OSA server on {host}:{port}[/green]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    uvicorn.run(
        "src.api.main:app",
        host=host,
        port=port,
        reload=reload,
    )


# ---------------------------------------------------------------------------
# Configuration subcommands
# ---------------------------------------------------------------------------

config_app = typer.Typer(help="Manage CLI configuration")
cli.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    config = load_config()

    table = Table(title="OSA Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    for field, value in config.model_dump().items():
        if "api_key" in field.lower() and value:
            display_value = f"{value[:8]}..." if len(value) > 8 else "***"
        elif value is None:
            display_value = "[dim]not set[/dim]"
        else:
            display_value = str(value)
        table.add_row(field, display_value)

    console.print(table)
    console.print(f"\n[dim]Config file: {get_config_path()}[/dim]")


@config_app.command("set")
def config_set(
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="API URL"),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="API key for authentication"),
    ] = None,
    openai_key: Annotated[
        str | None,
        typer.Option("--openai-key", help="OpenAI API key"),
    ] = None,
    anthropic_key: Annotated[
        str | None,
        typer.Option("--anthropic-key", help="Anthropic API key"),
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
) -> None:
    """Update configuration settings."""
    config = load_config()
    updated = False

    if api_url is not None:
        config.api_url = api_url
        updated = True
    if api_key is not None:
        config.api_key = api_key
        updated = True
    if openai_key is not None:
        config.openai_api_key = openai_key
        updated = True
    if anthropic_key is not None:
        config.anthropic_api_key = anthropic_key
        updated = True
    if openrouter_key is not None:
        config.openrouter_api_key = openrouter_key
        updated = True
    if output_format is not None:
        if output_format not in ("rich", "json", "plain"):
            console.print("[red]Invalid output format. Use: rich, json, plain[/red]")
            raise typer.Exit(code=1)
        config.output_format = output_format
        updated = True
    if verbose is not None:
        config.verbose = verbose
        updated = True

    if updated:
        save_config(config)
        console.print("[green]Configuration updated.[/green]")
    else:
        console.print("[yellow]No changes made. Use --help to see available options.[/yellow]")


@config_app.command("path")
def config_path() -> None:
    """Show configuration and data directory paths."""
    console.print(f"[cyan]Config directory:[/cyan] {get_config_dir()}")
    console.print(f"[cyan]Data directory:[/cyan] {get_data_dir()}")
    console.print(f"[cyan]Config file:[/cyan] {get_config_path()}")


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
        console.print("[green]Configuration reset to defaults.[/green]")
    else:
        console.print("[yellow]Cancelled.[/yellow]")


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
