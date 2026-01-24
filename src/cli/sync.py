"""CLI commands for syncing knowledge sources.

Sync commands require admin access (API_KEYS environment variable) because
they modify the knowledge database that all users rely on. Regular users
interact via API endpoints; only backend servers and admins run sync.

Read-only commands (status, search) do not require admin access.
"""

import logging
import os
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from src.assistants import registry
from src.cli.config import load_config
from src.knowledge.db import get_db_path, get_stats, init_db
from src.knowledge.github_sync import sync_repo, sync_repos
from src.knowledge.papers_sync import (
    sync_all_papers,
    sync_citing_papers,
    sync_openalex_papers,
    sync_pubmed_papers,
    sync_semanticscholar_papers,
)

logger = logging.getLogger(__name__)

console = Console()


def _require_admin() -> None:
    """Check that API_KEYS is set, exit if not.

    Sync commands modify the knowledge database and require admin access.
    Only backend servers and administrators should have this key.
    The API_KEYS environment variable is the same one used for API authentication.
    """
    if not os.environ.get("API_KEYS"):
        console.print("[red]Error: API_KEYS required for sync commands[/red]")
        console.print(
            "[dim]Sync commands modify the knowledge database and require admin access.[/dim]"
        )
        console.print("[dim]Set API_KEYS environment variable to proceed.[/dim]")
        raise typer.Exit(1)


def _get_community_repos(community_id: str) -> list[str]:
    """Get GitHub repos for a community from the registry."""
    info = registry.get(community_id)
    if info and info.community_config and info.community_config.github:
        return info.community_config.github.repos
    console.print(f"[yellow]Warning: No GitHub repos found for community '{community_id}'[/yellow]")
    return []


def _get_community_paper_queries(community_id: str) -> list[str]:
    """Get paper search queries for a community from the registry."""
    info = registry.get(community_id)
    if info and info.community_config and info.community_config.citations:
        return info.community_config.citations.queries
    console.print(
        f"[yellow]Warning: No paper queries found for community '{community_id}'[/yellow]"
    )
    return []


def _get_community_paper_dois(community_id: str) -> list[str]:
    """Get paper DOIs for citation tracking from the registry."""
    info = registry.get(community_id)
    if info and info.community_config and info.community_config.citations:
        return info.community_config.citations.dois
    console.print(f"[yellow]Warning: No paper DOIs found for community '{community_id}'[/yellow]")
    return []


def _get_all_community_ids() -> list[str]:
    """Get all registered community IDs."""
    return [info.id for info in registry.list_all()]


def _validate_community(community_id: str) -> None:
    """Validate community exists, exit with error if not.

    Args:
        community_id: The community ID to validate.

    Raises:
        typer.Exit: If community is not found in registry.
    """
    if registry.get(community_id) is None:
        available = ", ".join(_get_all_community_ids())
        console.print(f"[red]Error: Unknown community '{community_id}'[/red]")
        console.print(f"[dim]Available communities: {available}[/dim]")
        raise typer.Exit(1)


def _resolve_communities(community: str | None) -> list[str] | None:
    """Resolve community option to list of community IDs.

    Args:
        community: Single community ID or None for all.

    Returns:
        List of community IDs, or None if no communities available.

    Raises:
        typer.Exit: If specified community is invalid.
    """
    if community:
        _validate_community(community)
        return [community]

    communities = _get_all_community_ids()
    if not communities:
        console.print("[yellow]No communities registered[/yellow]")
        return None
    return communities


def _safe_init_db(community_id: str) -> bool:
    """Initialize database with error handling.

    Args:
        community_id: The community ID for database isolation.

    Returns:
        True if successful, False on error.
    """
    try:
        init_db(community_id)
        return True
    except Exception as e:
        console.print(f"[red]Error: Failed to initialize database for '{community_id}': {e}[/red]")
        console.print("[dim]Check disk space and permissions for the data directory.[/dim]")
        logger.exception("Database initialization failed for %s", community_id)
        return False


def _safe_load_config() -> tuple[str | None, str | None]:
    """Load config with error handling, returning API keys.

    Returns:
        Tuple of (semantic_scholar_key, pubmed_key), both may be None.
    """
    try:
        config = load_config()
        semantic_scholar_key = getattr(config, "semantic_scholar_api_key", None)
        pubmed_key = getattr(config, "pubmed_api_key", None)
        if semantic_scholar_key:
            logger.debug("Loaded Semantic Scholar API key from config")
        if pubmed_key:
            logger.debug("Loaded PubMed API key from config")
        return semantic_scholar_key, pubmed_key
    except Exception as e:
        console.print(f"[yellow]Warning: Could not load config: {e}[/yellow]")
        console.print("[dim]Continuing without API keys (reduced rate limits).[/dim]")
        logger.warning("Config load failed: %s", e)
        return None, None


sync_app = typer.Typer(
    name="sync",
    help="Sync knowledge sources (GitHub issues/PRs, papers)",
    no_args_is_help=True,
)


@sync_app.command("init")
def sync_init(
    community: Annotated[
        str | None,
        typer.Option("--community", "-c", help="Community ID to initialize (omit for all)"),
    ] = None,
) -> None:
    """Initialize the knowledge database for one or all communities."""
    _require_admin()

    communities = _resolve_communities(community)
    if communities is None:
        return

    for comm_id in communities:
        if _safe_init_db(comm_id):
            db_path = get_db_path(comm_id)
            console.print(f"[green]{comm_id}:[/green] Database initialized at {db_path}")


@sync_app.command("github")
def sync_github(
    community: Annotated[
        str,
        typer.Option("--community", "-c", help="Community ID to sync (e.g., hed, bids)"),
    ] = "hed",
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
    """Sync GitHub issues and PRs from community repositories."""
    _require_admin()
    _validate_community(community)

    if not _safe_init_db(community):
        raise typer.Exit(1)

    if repo:
        community_repos = _get_community_repos(community)
        if repo not in community_repos:
            console.print(f"[yellow]Note: {repo} is not in {community}'s configured repos[/yellow]")

        with console.status(f"[bold green]Syncing {repo}..."):
            count = sync_repo(repo, project=community, incremental=not full)

        console.print(f"[green]Synced {count} items from {repo}[/green]")
    else:
        community_repos = _get_community_repos(community)
        if not community_repos:
            console.print(
                f"[yellow]No GitHub repos configured for community '{community}'[/yellow]"
            )
            return

        with console.status(f"[bold green]Syncing {community} repositories..."):
            results = sync_repos(community_repos, project=community, incremental=not full)

        table = Table(title=f"GitHub Sync Results ({community})")
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
    community: Annotated[
        str,
        typer.Option("--community", "-c", help="Community ID to sync (e.g., hed, bids)"),
    ] = "hed",
    query: Annotated[
        str | None,
        typer.Option("--query", "-q", help="Custom search query (overrides community config)"),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option("--source", "-s", help="Source: openalex, semanticscholar, pubmed"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Max papers per query"),
    ] = 100,
    include_citations: Annotated[
        bool,
        typer.Option("--citations", help="Also sync papers citing community DOIs"),
    ] = True,
) -> None:
    """Sync papers from OpenALEX, Semantic Scholar, and PubMed."""
    _require_admin()
    _validate_community(community)

    if not _safe_init_db(community):
        raise typer.Exit(1)

    # Load API keys from config
    semantic_scholar_key, pubmed_key = _safe_load_config()

    # Get queries from community config, or use custom query
    if query:
        queries = [query]
    else:
        queries = _get_community_paper_queries(community)
        if not queries:
            console.print(
                f"[yellow]No paper queries configured for community '{community}'[/yellow]"
            )
            queries = []

    sources = [source] if source else ["openalex", "semanticscholar", "pubmed"]

    total = 0
    results_by_source: dict[str, int] = {}

    # Sync papers by query
    for q in queries:
        console.print(f"[dim]Query: {q}[/dim]")
        for src in sources:
            with console.status(f"  [green]Syncing from {src}...[/green]"):
                if src == "openalex":
                    count = sync_openalex_papers(q, limit, project=community)
                elif src == "semanticscholar":
                    count = sync_semanticscholar_papers(
                        q, limit, semantic_scholar_key, project=community
                    )
                elif src == "pubmed":
                    count = sync_pubmed_papers(q, limit, pubmed_key, project=community)
                else:
                    console.print(f"  [red]Unknown source: {src}[/red]")
                    continue

                results_by_source[src] = results_by_source.get(src, 0) + count
                total += count
                console.print(f"  [dim]{src}: {count} papers[/dim]")

    # Sync citing papers if DOIs are configured
    if include_citations:
        dois = _get_community_paper_dois(community)
        if dois:
            console.print(f"\n[dim]Syncing papers citing {len(dois)} DOI(s)...[/dim]")
            with console.status("[green]Syncing citing papers...[/green]"):
                citing_count = sync_citing_papers(dois, limit, project=community)
            results_by_source["citing"] = citing_count
            total += citing_count
            console.print(f"[dim]Citing papers: {citing_count}[/dim]")

    console.print(f"\n[green]Total papers synced for {community}: {total}[/green]")


@sync_app.command("all")
def sync_all(
    community: Annotated[
        str | None,
        typer.Option(
            "--community", "-c", help="Community ID to sync (omit to sync all communities)"
        ),
    ] = None,
    full: Annotated[
        bool,
        typer.Option("--full", help="Full sync (not incremental)"),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Max papers per query"),
    ] = 100,
) -> None:
    """Sync all knowledge sources (GitHub + papers) for one or all communities."""
    _require_admin()

    # Load API keys from config
    semantic_scholar_key, pubmed_key = _safe_load_config()

    # Determine which communities to sync
    communities = _resolve_communities(community)
    if communities is None:
        return

    grand_github_total = 0
    grand_paper_total = 0

    for comm_id in communities:
        console.print(f"\n[bold cyan]═══ Syncing {comm_id} ═══[/bold cyan]")
        if not _safe_init_db(comm_id):
            console.print(f"[red]Skipping {comm_id} due to database error[/red]")
            continue

        # GitHub
        repos = _get_community_repos(comm_id)
        if repos:
            console.print("[bold]Syncing GitHub repositories...[/bold]")
            with console.status(f"[green]Syncing {comm_id} GitHub...[/green]"):
                github_results = sync_repos(repos, project=comm_id, incremental=not full)
            github_total = sum(github_results.values())
            console.print(f"[green]GitHub: {github_total} items[/green]")
            grand_github_total += github_total
        else:
            console.print("[dim]No GitHub repos configured[/dim]")

        # Papers
        queries = _get_community_paper_queries(comm_id)
        dois = _get_community_paper_dois(comm_id)

        if queries or dois:
            console.print("[bold]Syncing papers...[/bold]")
            paper_total = 0

            # Sync by queries
            if queries:
                with console.status(f"[green]Syncing {comm_id} papers...[/green]"):
                    paper_results = sync_all_papers(
                        queries=queries,
                        max_results=limit,
                        semantic_scholar_api_key=semantic_scholar_key,
                        pubmed_api_key=pubmed_key,
                        project=comm_id,
                    )
                paper_total += sum(paper_results.values())

            # Sync citing papers
            if dois:
                with console.status("[green]Syncing citing papers...[/green]"):
                    citing_count = sync_citing_papers(dois, max_results=limit, project=comm_id)
                paper_total += citing_count

            console.print(f"[green]Papers: {paper_total} items[/green]")
            grand_paper_total += paper_total
        else:
            console.print("[dim]No paper queries/DOIs configured[/dim]")

    total_items = grand_github_total + grand_paper_total
    community_word = "community" if len(communities) == 1 else "communities"
    console.print(
        f"\n[bold green]Sync complete: {total_items} total items "
        f"across {len(communities)} {community_word}[/bold green]"
    )


@sync_app.command("status")
def sync_status(
    community: Annotated[
        str | None,
        typer.Option("--community", "-c", help="Community ID to show status for (omit for all)"),
    ] = None,
) -> None:
    """Show sync status and statistics."""
    # Note: status is read-only, no admin check needed

    communities = _resolve_communities(community)
    if communities is None:
        return

    for comm_id in communities:
        db_path = get_db_path(comm_id)

        if not db_path.exists():
            console.print(
                f"[yellow]{comm_id}: Database not initialized. Run 'osa sync init' first.[/yellow]"
            )
            continue

        stats = get_stats(comm_id)

        table = Table(title=f"Knowledge Database Status ({comm_id})")
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
        console.print(f"[dim]Database: {db_path}[/dim]\n")


@sync_app.command("search")
def sync_search(
    query: Annotated[
        str,
        typer.Argument(help="Search query"),
    ],
    community: Annotated[
        str,
        typer.Option("--community", "-c", help="Community ID to search (e.g., hed, bids)"),
    ] = "hed",
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
    # Note: search is read-only, no admin check needed
    from src.knowledge.search import search_github_items, search_papers

    _validate_community(community)

    db_path = get_db_path(community)
    if not db_path.exists():
        console.print(
            f"[yellow]Database not initialized for '{community}'. "
            "Run 'osa sync init' first.[/yellow]"
        )
        return

    console.print(f"[dim]Searching {community} knowledge database...[/dim]\n")

    if source == "github" or source is None:
        console.print("[bold]GitHub Results:[/bold]")
        github_results = search_github_items(query, project=community, limit=limit)
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
        paper_results = search_papers(query, project=community, limit=limit, source=paper_source)
        if paper_results:
            for r in paper_results:
                console.print(f"  [{r.source}] {r.title}")
                console.print(f"    [dim]{r.url}[/dim]")
        else:
            console.print("  [dim]No results[/dim]")
