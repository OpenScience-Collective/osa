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
from rich.markup import escape
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


def print_warning(message: str) -> None:
    """Print a warning to stderr: the answer is shown, and the reader should know something."""
    err_console.print(f"[bold yellow]Warning:[/] {escape(message)}", highlight=False)


def print_success(message: str) -> None:
    """Print success message to stderr."""
    err_console.print(f"[bold green]OK:[/] {message}")


def _format_cost(dollars: float) -> str:
    """A cost in US dollars, with as many decimals as its size needs to say something."""
    if dollars < 0.0001:
        return "under $0.0001"
    if dollars < 0.01:
        return f"about ${dollars:.4f}"
    if dollars < 1:
        return f"about ${dollars:.3f}"
    return f"about ${dollars:.2f}"


def format_usage(usage: dict[str, Any] | None) -> str | None:
    """What a reply used, in one line, from the ``usage`` object the server sends.

    For example ``1,240 in (980 cached), 310 out, about $0.0021``. The input count includes
    the cached tokens. The widget words it the same way (``formatUsage`` in
    ``frontend/osa-chat-widget.js``), and the two are tested against one table.

    Args:
        usage: The server's ``usage`` object, or None when it sent none.

    Returns:
        The line, or None when there is nothing to say (no usage, or one that is not an
        object, which a server of another version could send).
    """
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    cache_parts = []
    cache_read = usage.get("cache_read_tokens")
    cache_written = usage.get("cache_creation_tokens")
    if isinstance(cache_read, int) and cache_read > 0:
        cache_parts.append(f"{cache_read:,} cached")
    if isinstance(cache_written, int) and cache_written > 0:
        cache_parts.append(f"{cache_written:,} written to cache")
    cache = f" ({', '.join(cache_parts)})" if cache_parts else ""
    parts = [f"{input_tokens:,} in{cache}", f"{output_tokens:,} out"]
    cost = usage.get("estimated_cost")
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        parts.append(_format_cost(float(cost)))
    return ", ".join(parts)


def print_usage(usage: dict[str, Any] | None) -> None:
    """Print what a reply used, under it, to stderr: a status line, so stdout stays the
    answer alone for a pipe."""
    line = format_usage(usage)
    if line:
        err_console.print(f"Usage: {line}", style="dim", markup=False, highlight=False)


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
