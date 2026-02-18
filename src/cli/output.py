"""Output formatting for OSA CLI.

Status messages go to stderr. Results go to stdout.
This keeps piped output clean (e.g., osa ask "..." -o json | jq).
"""

import json
import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

# stdout for results
console = Console()
# stderr for status messages, errors, progress
err_console = Console(stderr=True)


def print_error(message: str, hint: str | None = None) -> None:
    """Print error to stderr."""
    err_console.print(f"[bold red]Error:[/] {message}")
    if hint:
        # Use markup=False to avoid interpreting brackets in hint text
        err_console.print(f"Hint: {hint}", style="dim")


def print_success(message: str) -> None:
    """Print success message to stderr."""
    err_console.print(f"[bold green]OK:[/] {message}")


def print_info(message: str) -> None:
    """Print info message to stderr."""
    err_console.print(f"[dim]{message}[/]")


def print_progress(message: str) -> None:
    """Print progress message to stderr."""
    err_console.print(f"[dim]{message}...[/]")


def print_markdown(content: str, title: str | None = None) -> None:
    """Print markdown content in a Rich panel to stdout."""
    md = Markdown(content)
    if title:
        panel = Panel(md, title=f"[bold]{title}[/bold]", border_style="blue")
        console.print(panel)
    else:
        console.print(md)


def print_json_output(data: dict[str, Any]) -> None:
    """Print JSON to stdout for piped output."""
    print(json.dumps(data, indent=2))


def print_table(title: str, rows: list[dict[str, str]], columns: list[str]) -> None:
    """Print a Rich table to stdout."""
    table = Table(title=title)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[row.get(col, "") for col in columns])
    console.print(table)


@contextmanager
def streaming_status(
    initial_message: str = "Connecting...",
) -> Generator[Any, None, None]:
    """Context manager for a streaming status spinner on stderr."""
    with err_console.status(f"[dim]{initial_message}[/]", spinner="dots") as status:
        yield status


def is_piped() -> bool:
    """Check if stdout is being piped (not a TTY)."""
    return not sys.stdout.isatty()
