"""CLI commands that read the usage metrics database.

`osa metrics cost-report` prices each conversation of a community, the quick model
against the analysis model (plan E8.4). It only ever reads: the database is opened
read-only, so it is safe to point at a live file or at a copy pulled from a server.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from src.cli import output
from src.metrics.db import resolve_metrics_db_path
from src.metrics.queries import get_conversation_costs

metrics_app = typer.Typer(
    help="Report on the usage metrics database",
    no_args_is_help=True,
)

_SINCE_PATTERN = re.compile(r"^(\d+)\s*([hdw])$", re.IGNORECASE)
_SINCE_UNITS = {"h": "hours", "d": "days", "w": "weeks"}
_SHORT_ID_LENGTH = 8


def parse_since(text: str, now: datetime | None = None) -> datetime:
    """The point in time `--since` names: an age such as `7d`, `12h` or `2w`, or a date.

    Raises:
        ValueError: If the text is neither.
    """
    now = now or datetime.now(UTC)
    text = text.strip()
    match = _SINCE_PATTERN.match(text)
    if match:
        return now - timedelta(**{_SINCE_UNITS[match.group(2).lower()]: int(match.group(1))})
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"Cannot read --since {text!r}: use an age such as 7d, 12h or 2w, or a date "
            "such as 2026-11-01"
        ) from None
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


SNAPSHOT_NOTE = (
    "This file is in WAL mode and no -wal file is beside it. If it is a copy of a database "
    "that was in use, rows that were only in the -wal file are not included, so the report "
    "can show fewer requests than the server has. Copy metrics.db together with "
    "metrics.db-wal and metrics.db-shm, or take a consistent snapshot on the server with "
    "VACUUM INTO (see docs/community-widget.md)."
)


def _wal_mode_without_log(path: Path) -> bool:
    """Whether the file says it is in WAL mode while no `-wal` file is beside it.

    Whether SQLite opens such a file read-only depends on its version, so this is checked
    on the file itself: a copy of a live database made without its `-wal` file looks like
    this, and so does a database that was closed cleanly (then nothing is missing). A
    `VACUUM INTO` snapshot is not in WAL mode, so it is never flagged.
    """
    with path.open("rb") as f:
        header = f.read(20)
    is_wal = len(header) == 20 and header[:16] == b"SQLite format 3\x00" and header[18] == 2
    return is_wal and not path.with_name(path.name + "-wal").exists()


def open_read_only(path: Path) -> tuple[sqlite3.Connection, str | None]:
    """Open a metrics database that cannot be written to by this process.

    A database copied out of a running server is in WAL mode but may have no `-wal` or
    `-shm` file beside it. Depending on the SQLite version, read-only mode then either
    cannot open it ("unable to open database file"), in which case the file is opened as
    immutable instead, or opens it and sees only what is in the main file. Any other
    failure (a locked database, a file that is not a database) is raised as it is.

    Returns:
        The connection, and a note saying that rows still in a `-wal` file that was not
        copied are not included, when the file is in WAL mode with no `-wal` beside it (or
        had to be opened as immutable); None otherwise.
    """
    uri = path.resolve().as_uri()
    # Checked before connecting: opening a WAL database can create the `-wal` file.
    note = SNAPSHOT_NOTE if _wal_mode_without_log(path) else None
    try:
        conn = sqlite3.connect(f"{uri}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.OperationalError as e:
        if "unable to open" not in str(e):
            raise
        conn = sqlite3.connect(f"{uri}?mode=ro&immutable=1", uri=True)
        note = SNAPSHOT_NOTE
    conn.row_factory = sqlite3.Row
    return conn, note


def _dollars(amount: float) -> str:
    return f"${amount:,.4f}"


def _day_and_time(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(timestamp).astimezone(UTC).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return timestamp[:16]


def _conversation_table(report: dict[str, Any]) -> Table:
    other = report["totals"]["other"]
    has_other = other["turns"] + other["errors"] > 0
    table = Table(title=f"Conversations of {report['community_id']} since {report['since'][:16]}")
    table.add_column("Conversation")
    table.add_column("Start (UTC)")
    table.add_column("Turns quick/analysis", justify="right")
    table.add_column("Quick cost", justify="right")
    table.add_column("Analysis cost", justify="right")
    if has_other:
        table.add_column("Not routed", justify="right")
    table.add_column("Total", justify="right")
    for conversation in report["conversations"]:
        cells = [
            conversation["conversation_id"][:_SHORT_ID_LENGTH],
            _day_and_time(conversation["started"]),
            f"{conversation['quick']['turns']}/{conversation['analysis']['turns']}",
            _dollars(conversation["quick"]["cost"]),
            _dollars(conversation["analysis"]["cost"]),
        ]
        if has_other:
            cells.append(_dollars(conversation["other"]["cost"]))
        cells.append(_dollars(conversation["total_cost"]))
        table.add_row(*cells)
    return table


def _totals_table(report: dict[str, Any]) -> Table:
    totals = report["totals"]
    table = Table(title="Totals by tier")
    table.add_column("Tier")
    has_errors = any(totals[side]["errors"] for side in ("quick", "analysis", "other"))
    table.add_column("Turns", justify="right")
    if has_errors:
        table.add_column("Failed", justify="right")
    table.add_column("Input tokens", justify="right")
    table.add_column("Output tokens", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Share", justify="right")
    grand = totals["total_cost"]
    for side, label in (("quick", "Quick"), ("analysis", "Analysis"), ("other", "Not routed")):
        bucket = totals[side]
        if side == "other" and not (bucket["turns"] or bucket["errors"]):
            continue
        share = f"{bucket['cost'] / grand:.0%}" if grand else "-"
        cells = [label, str(bucket["turns"])]
        if has_errors:
            cells.append(str(bucket["errors"]))
        cells += [
            f"{bucket['input_tokens']:,}",
            f"{bucket['output_tokens']:,}",
            _dollars(bucket["cost"]),
            share,
        ]
        table.add_row(*cells)
    table.add_row("All", *([""] * (3 + has_errors)), _dollars(grand), "")
    return table


def _unassigned_table(report: dict[str, Any]) -> Table:
    table = Table(title="Requests without a conversation id, by model")
    table.add_column("Model")
    has_errors = any(bucket["errors"] for bucket in report["unassigned"])
    table.add_column("Requests", justify="right")
    if has_errors:
        table.add_column("Failed", justify="right")
    table.add_column("Input tokens", justify="right")
    table.add_column("Output tokens", justify="right")
    table.add_column("Cost", justify="right")
    for bucket in report["unassigned"]:
        table.add_row(
            bucket["model"],
            str(bucket["turns"]),
            *([str(bucket["errors"])] if has_errors else []),
            f"{bucket['input_tokens']:,}",
            f"{bucket['output_tokens']:,}",
            _dollars(bucket["cost"]),
        )
    return table


@metrics_app.command("cost-report")
def cost_report(
    community: Annotated[
        str,
        typer.Option("--community", "-c", help="Community to report on, such as nemar"),
    ],
    since: Annotated[
        str,
        typer.Option("--since", "-s", help="How far back: an age (7d, 12h, 2w) or a date"),
    ] = "7d",
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Metrics database to read (default: this machine's own)"),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the report as JSON"),
    ] = False,
) -> None:
    """Price each conversation, quick model against analysis model.

    Cost is what each request was priced at when it was logged. A request that hands a
    conversation over to the analysis model writes a quick row (the summary) and an
    analysis row, so it counts once on each side. Requests logged before conversation
    ids existed are listed by model. A failed request is counted as failed, not as a
    turn.

    Example: osa metrics cost-report --community nemar --since 7d
    """
    try:
        start = parse_since(since)
    except ValueError as e:
        output.print_error(str(e))
        raise typer.Exit(code=2) from None

    path = db or resolve_metrics_db_path()
    if not path.is_file():
        output.print_error(f"No metrics database at {path}", hint="Pass one with --db PATH")
        raise typer.Exit(code=1)

    try:
        conn, note = open_read_only(path)
        try:
            report = get_conversation_costs(conn, community, start)
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        output.print_error(f"Cannot read {path} as a metrics database: {e}")
        raise typer.Exit(code=1) from None

    if note:
        output.print_warning(note)
    if as_json:
        if note:
            report["note"] = note
        output.print_json_output(report)
        return
    if not report["conversations"] and not report["unassigned"]:
        output.print_info(f"No requests for {community} since {report['since'][:16]}.")
        return
    if report["conversations"]:
        output.console.print(_conversation_table(report))
        output.console.print(_totals_table(report))
    if report["unassigned"]:
        output.console.print(_unassigned_table(report))
