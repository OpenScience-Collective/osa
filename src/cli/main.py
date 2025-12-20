"""Typer CLI for Open Science Assistant."""

from typing import Annotated

import typer
from rich.console import Console
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

# Create CLI app
cli = typer.Typer(
    name="osa",
    help="Open Science Assistant - AI assistant for open science projects",
    no_args_is_help=True,
)

# Rich console for formatted output
console = Console()


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
        version = result.get("version", "unknown")
        environment = result.get("environment", "unknown")

        if status == "healthy":
            console.print(
                Panel(
                    f"[green]Status:[/green] {status}\n"
                    f"[blue]Version:[/blue] {version}\n"
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


# Configuration subcommand group
config_app = typer.Typer(help="Manage CLI configuration")
cli.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    config = load_config()

    table = Table(title="OSA Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    # Show all config fields
    for field, value in config.model_dump().items():
        # Mask API keys for security
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
