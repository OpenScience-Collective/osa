"""CLI commands for managing ephemeral database mirrors.

Mirrors are short-lived copies of community knowledge databases on the
remote server. They allow developers to iterate on data and prompts
without affecting production, and can be downloaded locally for offline
development with a local server.
"""

from __future__ import annotations

from typing import Annotated

import httpx
import typer
from rich.table import Table

from src.cli import output
from src.cli.config import get_data_dir, get_effective_config, get_user_id

mirror_app = typer.Typer(
    help="Manage ephemeral database mirrors for development",
    no_args_is_help=True,
)


def _get_client(
    api_key: str | None = None,
    api_url: str | None = None,
) -> tuple:
    """Create an OSAClient with effective config. Returns (client, config)."""
    from src.cli.client import OSAClient

    config, effective_key = get_effective_config(api_key=api_key, api_url=api_url)
    if not effective_key:
        output.print_error(
            "No API key configured.",
            hint="Run 'osa init' to set up your API key, or pass --api-key",
        )
        raise typer.Exit(code=1)

    client = OSAClient(
        api_url=config.api.url,
        openrouter_api_key=effective_key,
        user_id=get_user_id(),
    )
    return client, config


@mirror_app.command("create")
def create(
    community: Annotated[
        list[str],
        typer.Option("--community", "-c", help="Community ID to include (repeatable)"),
    ],
    label: Annotated[
        str | None,
        typer.Option("--label", "-l", help="Human-readable label for the mirror"),
    ] = None,
    ttl: Annotated[
        int,
        typer.Option("--ttl", help="Hours until mirror expires (1-168)"),
    ] = 48,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", help="OpenRouter API key"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
) -> None:
    """Create a new ephemeral database mirror.

    Examples:
        osa mirror create -c hed -c bids
        osa mirror create -c hed --label "testing-new-prompt" --ttl 24
    """
    from src.cli.client import APIError

    client, _ = _get_client(api_key, api_url)

    try:
        with output.streaming_status("Creating mirror..."):
            result = client.create_mirror(
                community_ids=community,
                ttl_hours=ttl,
                label=label,
            )
        output.print_success(f"Mirror created: {result['mirror_id']}")
        output.print_info(f"  Communities: {', '.join(result['community_ids'])}")
        output.print_info(f"  Expires: {result['expires_at']}")
        if result.get("label"):
            output.print_info(f"  Label: {result['label']}")
        output.console.print()
        output.console.print(
            f'[dim]Use with: osa ask "question" -a hed --mirror {result["mirror_id"]}[/dim]'
        )
    except APIError as e:
        output.print_error(str(e), hint=e.detail)
        raise typer.Exit(code=1)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        output.print_error(f"Connection failed: {e}")
        raise typer.Exit(code=1)


@mirror_app.command("list")
def list_cmd(
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", help="OpenRouter API key"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
) -> None:
    """List active mirrors."""
    from src.cli.client import APIError

    client, _ = _get_client(api_key, api_url)

    try:
        mirrors = client.list_mirrors()
    except APIError as e:
        output.print_error(str(e), hint=e.detail)
        raise typer.Exit(code=1)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        output.print_error(f"Connection failed: {e}")
        raise typer.Exit(code=1)

    if not mirrors:
        output.print_info("No active mirrors.")
        return

    table = Table(title="Active Mirrors")
    table.add_column("ID", style="cyan")
    table.add_column("Communities", style="green")
    table.add_column("Label")
    table.add_column("Expires", style="yellow")
    table.add_column("Size", style="dim")

    for m in mirrors:
        size_kb = m.get("size_bytes", 0) / 1024
        size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        table.add_row(
            m["mirror_id"],
            ", ".join(m["community_ids"]),
            m.get("label") or "",
            m["expires_at"][:19],
            size_str,
        )

    output.console.print(table)


@mirror_app.command("info")
def info(
    mirror_id: Annotated[str, typer.Argument(help="Mirror ID")],
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", help="OpenRouter API key"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
) -> None:
    """Show detailed information about a mirror."""
    from src.cli.client import APIError

    client, _ = _get_client(api_key, api_url)

    try:
        m = client.get_mirror(mirror_id)
    except APIError as e:
        output.print_error(str(e), hint=e.detail)
        raise typer.Exit(code=1)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        output.print_error(f"Connection failed: {e}")
        raise typer.Exit(code=1)

    output.console.print(f"[bold]Mirror:[/bold] {m['mirror_id']}")
    output.console.print(f"  Communities: {', '.join(m['community_ids'])}")
    output.console.print(f"  Created: {m['created_at']}")
    output.console.print(f"  Expires: {m['expires_at']}")
    if m.get("label"):
        output.console.print(f"  Label: {m['label']}")
    if m.get("owner_id"):
        output.console.print(f"  Owner: {m['owner_id']}")
    size_kb = m.get("size_bytes", 0) / 1024
    size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
    output.console.print(f"  Size: {size_str}")
    expired = m.get("expired", False)
    status = "[red]expired[/red]" if expired else "[green]active[/green]"
    output.console.print(f"  Status: {status}")


@mirror_app.command("delete")
def delete(
    mirror_id: Annotated[str, typer.Argument(help="Mirror ID")],
    confirm: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation"),
    ] = False,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", help="OpenRouter API key"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
) -> None:
    """Delete a mirror and its databases."""
    from src.cli.client import APIError

    if not confirm:
        confirm = typer.confirm(f"Delete mirror {mirror_id}?")
    if not confirm:
        output.print_info("Cancelled.")
        return

    client, _ = _get_client(api_key, api_url)

    try:
        client.delete_mirror(mirror_id)
        output.print_success(f"Mirror {mirror_id} deleted.")
    except APIError as e:
        output.print_error(str(e), hint=e.detail)
        raise typer.Exit(code=1)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        output.print_error(f"Connection failed: {e}")
        raise typer.Exit(code=1)


@mirror_app.command("refresh")
def refresh(
    mirror_id: Annotated[str, typer.Argument(help="Mirror ID")],
    community: Annotated[
        list[str] | None,
        typer.Option("--community", "-c", help="Specific community to refresh"),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", help="OpenRouter API key"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
) -> None:
    """Re-copy production databases into an existing mirror.

    Resets mirror data to match current production state.
    """
    from src.cli.client import APIError

    client, _ = _get_client(api_key, api_url)

    try:
        with output.streaming_status("Refreshing mirror..."):
            result = client.refresh_mirror(mirror_id, community_ids=community)
        output.print_success(f"Mirror {mirror_id} refreshed.")
        output.print_info(f"  Communities: {', '.join(result['community_ids'])}")
    except APIError as e:
        output.print_error(str(e), hint=e.detail)
        raise typer.Exit(code=1)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        output.print_error(f"Connection failed: {e}")
        raise typer.Exit(code=1)


@mirror_app.command("sync")
def sync(
    mirror_id: Annotated[str, typer.Argument(help="Mirror ID")],
    sync_type: Annotated[
        str,
        typer.Option(
            "--type",
            "-t",
            help="Sync type: github, papers, docstrings, mailman, faq, beps, or all",
        ),
    ] = "all",
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", help="OpenRouter API key"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
) -> None:
    """Run sync pipeline against a mirror's databases.

    Populates or refreshes the mirror's data from public sources
    (GitHub, papers, etc.) using the server's sync pipeline.

    Examples:
        osa mirror sync abc123def456
        osa mirror sync abc123def456 --type github
    """
    from src.cli.client import APIError

    client, _ = _get_client(api_key, api_url)

    try:
        with output.streaming_status(f"Syncing {sync_type} into mirror..."):
            result = client.sync_mirror(mirror_id, sync_type=sync_type)
        if result.get("success"):
            output.print_success(result.get("message", "Sync completed"))
            items = result.get("items_synced", {})
            if items:
                for st, count in items.items():
                    output.print_info(f"  {st}: {count} communities synced")
        else:
            output.print_error(result.get("message", "Sync failed"))
    except APIError as e:
        output.print_error(str(e), hint=e.detail)
        raise typer.Exit(code=1)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        output.print_error(f"Connection failed: {e}")
        raise typer.Exit(code=1)


@mirror_app.command("pull")
def pull(
    mirror_id: Annotated[str, typer.Argument(help="Mirror ID")],
    community: Annotated[
        str | None,
        typer.Option("--community", "-c", help="Specific community to download"),
    ] = None,
    output_dir: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Output directory (default: local data/knowledge)"),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", help="OpenRouter API key"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override API URL"),
    ] = None,
) -> None:
    """Download mirror databases locally for offline development.

    Downloads SQLite files so you can run `osa serve` locally with the
    mirror's data. Useful for testing code changes or using a local LLM.

    Examples:
        osa mirror pull abc123def456
        osa mirror pull abc123def456 -c hed -o ./data/knowledge
    """
    from src.cli.client import APIError

    client, _ = _get_client(api_key, api_url)
    dest = output_dir or str(get_data_dir() / "knowledge")

    # Get mirror info to know which communities to download
    try:
        mirror_info = client.get_mirror(mirror_id)
    except APIError as e:
        output.print_error(str(e), hint=e.detail)
        raise typer.Exit(code=1)

    communities = [community] if community else mirror_info["community_ids"]

    failures = 0
    for cid in communities:
        try:
            with output.streaming_status(f"Downloading {cid}.db..."):
                path = client.download_mirror_db(mirror_id, cid, dest)
            output.print_success(f"Downloaded: {path}")
        except APIError as e:
            output.print_error(f"Failed to download {cid}: {e}", hint=e.detail)
            failures += 1
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            output.print_error(f"Connection failed downloading {cid}: {e}")
            failures += 1

    output.console.print()
    if failures:
        output.print_error(f"{failures} download(s) failed. Local data may be incomplete.")
        raise typer.Exit(code=1)
    output.console.print("[dim]Start local server with: osa serve[/dim]")
