"""Output formatting for OSA CLI.

Status messages go to stderr. Results go to stdout.
This keeps piped output clean (e.g., osa ask "..." -o json | jq).
"""

import json
import logging
import math
import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel

logger = logging.getLogger(__name__)

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
    """A cost in US dollars, with "about": four decimals below a cent, three below a dollar,
    two from a dollar up, and "under $0.0001" for less.

    Rounded half up on whole micro-dollars (the server's own precision) with integer
    arithmetic, so that the widget's ``formatCost``, which cuts at the same places and does
    the same sums, prints the same digits even where a float formatter would round a tie
    the other way.
    """
    micro = math.floor(dollars * 1_000_000 + 0.5)
    if micro < 100:
        return "under $0.0001"
    if micro < 10_000:
        decimals = 4
    elif micro < 1_000_000:
        decimals = 3
    else:
        decimals = 2
    unit = 10 ** (6 - decimals)
    whole, fraction = divmod((micro + unit // 2) // unit, 10**decimals)
    return f"about ${whole}.{fraction:0{decimals}d}"


def _count(value: Any) -> int | None:
    """``value`` if it is a token count (a whole number, not a boolean, not negative)."""
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return value if ok else None


def format_usage(usage: dict[str, Any] | None) -> str | None:
    """What a reply used, in one line, from the ``usage`` object the server sends.

    For example ``1,240 in (980 cached), 310 out, about $0.0021``. The input count includes
    the cached tokens, and a reply some of whose model runs reported no tokens starts "at
    least". The widget words it the same way (``formatUsage`` in
    ``frontend/osa-chat-widget.js``), and the two are tested against every row of
    ``tests/fixtures/usage_lines.json``.

    Args:
        usage: The server's ``usage`` object, or None when it sent none.

    Returns:
        The line, or None when there is nothing to say: no usage, or an object this version
        cannot read (not an object, or without whole ``input_tokens`` and ``output_tokens``),
        which a server of another version could send and which is logged at debug level.
    """
    if usage is None:
        return None
    fields = usage if isinstance(usage, dict) else {}
    input_tokens = _count(fields.get("input_tokens"))
    output_tokens = _count(fields.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        logger.debug("Ignoring a usage object this version cannot read: %r", usage)
        return None
    cache_parts = []
    cache_read = _count(usage.get("cache_read_tokens"))
    cache_written = _count(usage.get("cache_creation_tokens"))
    if cache_read:
        cache_parts.append(f"{cache_read:,} cached")
    if cache_written:
        cache_parts.append(f"{cache_written:,} written to cache")
    cache = f" ({', '.join(cache_parts)})" if cache_parts else ""
    parts = [f"{input_tokens:,} in{cache}", f"{output_tokens:,} out"]
    cost = usage.get("estimated_cost")
    if isinstance(cost, int | float) and not isinstance(cost, bool) and 0 <= cost < math.inf:
        parts.append(_format_cost(float(cost)))
    line = ", ".join(parts)
    return f"at least {line}" if usage.get("partial") is True else line


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
