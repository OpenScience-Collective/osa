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

# stdout for results
console = Console()
# stderr for status messages, errors, progress
err_console = Console(stderr=True)


def print_error(message: str, hint: str | None = None) -> None:
    """Print error to stderr."""
    err_console.print(f"[bold red]Error:[/] {message}")
    if hint:
        err_console.print(f"Hint: {hint}", style="dim", markup=False)


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
