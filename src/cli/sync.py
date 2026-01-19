"""CLI commands for syncing knowledge sources."""

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from src.assistants import discover_assistants, registry
from src.cli.config import load_config
from src.knowledge.db import get_db_path, get_stats, init_db
from src.knowledge.github_sync import sync_repo, sync_repos
from src.knowledge.papers_sync import (
    HED_QUERIES,
    sync_all_papers,
    sync_openalex_papers,
    sync_pubmed_papers,
    sync_semanticscholar_papers,
)

# Discover assistants to populate registry
discover_assistants()

console = Console()


def _get_hed_repos() -> list[str]:
    """Get HED repos from the registry."""
    info = registry.get("hed")
    if info and info.community_config and info.community_config.github:
        return info.community_config.github.repos
    console.print("[yellow]Warning: HED repos not found in registry[/yellow]")
    return []


sync_app = typer.Typer(
    name="sync",
    help="Sync knowledge sources (GitHub issues/PRs, papers)",
    no_args_is_help=True,
)


@sync_app.command("init")
def sync_init() -> None:
    """Initialize the knowledge database."""
    init_db()
    db_path = get_db_path()
    console.print(f"[green]Knowledge database initialized at:[/green] {db_path}")


@sync_app.command("github")
def sync_github(
    repo: Annotated[
        str | None,
        typer.Option(
            "--repo", "-r", help="Specific repo to sync (e.g., hed-standard/hed-specification)"
        ),
    ] = None,
    full: Annotated[
        bool,
        typer.Option("--full", help="Full sync (not incremental)"),
    ] = False,
) -> None:
    """Sync GitHub issues and PRs from HED repositories."""
    init_db()

    if repo:
        if repo not in _get_hed_repos():
            console.print(f"[yellow]Note: {repo} is not in the default HED repos list[/yellow]")

        with console.status(f"[bold green]Syncing {repo}..."):
            count = sync_repo(repo, incremental=not full)

        console.print(f"[green]Synced {count} items from {repo}[/green]")
    else:
        with console.status("[bold green]Syncing all HED repositories..."):
            results = sync_repos(_get_hed_repos(), project="hed", incremental=not full)

        table = Table(title="GitHub Sync Results")
        table.add_column("Repository", style="cyan")
        table.add_column("Items Synced", style="green", justify="right")

        total = 0
        for repo_name, count in results.items():
            table.add_row(repo_name, str(count))
            total += count

        table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]")
        console.print(table)


@sync_app.command("papers")
def sync_papers(
    query: Annotated[
        str | None,
        typer.Option("--query", "-q", help="Custom search query"),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option("--source", "-s", help="Source: openalex, semanticscholar, pubmed"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Max papers per query"),
    ] = 100,
) -> None:
    """Sync papers from OpenALEX, Semantic Scholar, and PubMed."""
    init_db()

    # Load API keys from config
    config = load_config()
    semantic_scholar_key = getattr(config, "semantic_scholar_api_key", None)
    pubmed_key = getattr(config, "pubmed_api_key", None)

    queries = [query] if query else HED_QUERIES
    sources = [source] if source else ["openalex", "semanticscholar", "pubmed"]

    total = 0
    results_by_source: dict[str, int] = {}

    for q in queries:
        console.print(f"[dim]Query: {q}[/dim]")
        for src in sources:
            with console.status(f"  [green]Syncing from {src}...[/green]"):
                if src == "openalex":
                    count = sync_openalex_papers(q, limit)
                elif src == "semanticscholar":
                    count = sync_semanticscholar_papers(q, limit, semantic_scholar_key)
                elif src == "pubmed":
                    count = sync_pubmed_papers(q, limit, pubmed_key)
                else:
                    console.print(f"  [red]Unknown source: {src}[/red]")
                    continue

                results_by_source[src] = results_by_source.get(src, 0) + count
                total += count
                console.print(f"  [dim]{src}: {count} papers[/dim]")

    console.print(f"\n[green]Total papers synced: {total}[/green]")


@sync_app.command("all")
def sync_all(
    full: Annotated[
        bool,
        typer.Option("--full", help="Full sync (not incremental)"),
    ] = False,
) -> None:
    """Sync all knowledge sources (GitHub + papers)."""
    init_db()

    # Load API keys from config
    config = load_config()
    semantic_scholar_key = getattr(config, "semantic_scholar_api_key", None)
    pubmed_key = getattr(config, "pubmed_api_key", None)

    # GitHub
    console.print("[bold]Syncing GitHub repositories...[/bold]")
    with console.status("[green]Syncing GitHub...[/green]"):
        github_results = sync_repos(_get_hed_repos(), project="hed", incremental=not full)
    github_total = sum(github_results.values())
    console.print(f"[green]GitHub: {github_total} items[/green]")

    # Papers
    console.print("\n[bold]Syncing papers...[/bold]")
    with console.status("[green]Syncing papers...[/green]"):
        paper_results = sync_all_papers(
            semantic_scholar_api_key=semantic_scholar_key,
            pubmed_api_key=pubmed_key,
        )
    paper_total = sum(paper_results.values())
    console.print(f"[green]Papers: {paper_total} items[/green]")

    console.print(
        f"\n[bold green]Sync complete: {github_total + paper_total} total items[/bold green]"
    )


@sync_app.command("status")
def sync_status() -> None:
    """Show sync status and statistics."""
    db_path = get_db_path()

    if not db_path.exists():
        console.print("[yellow]Database not initialized. Run 'osa sync init' first.[/yellow]")
        return

    stats = get_stats()

    table = Table(title="Knowledge Database Status")
    table.add_column("Source", style="cyan")
    table.add_column("Count", style="green", justify="right")

    # GitHub section
    table.add_row("[bold]GitHub[/bold]", "")
    table.add_row("  Issues", str(stats["github_issues"]))
    table.add_row("  PRs", str(stats["github_prs"]))
    table.add_row("  Open", str(stats["github_open"]))
    table.add_row("  [dim]Total[/dim]", f"[dim]{stats['github_total']}[/dim]")

    # Papers section
    table.add_row("[bold]Papers[/bold]", "")
    table.add_row("  OpenALEX", str(stats["papers_openalex"]))
    table.add_row("  Semantic Scholar", str(stats["papers_semanticscholar"]))
    table.add_row("  PubMed", str(stats["papers_pubmed"]))
    table.add_row("  [dim]Total[/dim]", f"[dim]{stats['papers_total']}[/dim]")

    # Grand total
    grand_total = stats["github_total"] + stats["papers_total"]
    table.add_row("[bold]Grand Total[/bold]", f"[bold]{grand_total}[/bold]")

    console.print(table)
    console.print(f"\n[dim]Database: {db_path}[/dim]")


@sync_app.command("search")
def sync_search(
    query: Annotated[
        str,
        typer.Argument(help="Search query"),
    ],
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Max results"),
    ] = 5,
    source: Annotated[
        str | None,
        typer.Option(
            "--source", "-s", help="Filter by source: github, openalex, semanticscholar, pubmed"
        ),
    ] = None,
) -> None:
    """Search the knowledge database (for testing)."""
    from src.knowledge.search import search_github_items, search_papers

    db_path = get_db_path()
    if not db_path.exists():
        console.print("[yellow]Database not initialized. Run 'osa sync init' first.[/yellow]")
        return

    if source == "github" or source is None:
        console.print("[bold]GitHub Results:[/bold]")
        github_results = search_github_items(query, limit=limit)
        if github_results:
            for r in github_results:
                status_style = "green" if r.status == "open" else "dim"
                console.print(f"  [{status_style}][{r.item_type}][/{status_style}] {r.title}")
                console.print(f"    [dim]{r.url}[/dim]")
        else:
            console.print("  [dim]No results[/dim]")

    if source != "github":
        console.print("\n[bold]Paper Results:[/bold]")
        paper_source = source if source and source != "github" else None
        paper_results = search_papers(query, limit=limit, source=paper_source)
        if paper_results:
            for r in paper_results:
                console.print(f"  [{r.source}] {r.title}")
                console.print(f"    [dim]{r.url}[/dim]")
        else:
            console.print("  [dim]No results[/dim]")
