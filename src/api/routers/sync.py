"""Sync status and management API endpoints.

Provides endpoints for:
- Checking knowledge sync status
- Manually triggering sync jobs
- Health checks for monitoring
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.config import get_settings
from src.api.scheduler import get_scheduler, run_sync_now
from src.api.security import RequireAdminAuth
from src.assistants import registry
from src.knowledge.db import get_connection, get_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


class RepoStatus(BaseModel):
    """Status for a single repository."""

    items: int
    last_sync: str | None


class GitHubStatus(BaseModel):
    """GitHub sync status."""

    total_items: int
    issues: int
    prs: int
    open_items: int
    repos: dict[str, RepoStatus]


class PapersStatus(BaseModel):
    """Papers sync status."""

    total_items: int
    sources: dict[str, RepoStatus]


class SchedulerStatus(BaseModel):
    """Scheduler status."""

    enabled: bool
    running: bool
    jobs: dict[str, str | None]
    """Map of job_id to next_run_time (ISO) or None."""


class HealthStatus(BaseModel):
    """Health check status."""

    healthy: bool
    github_healthy: bool
    papers_healthy: bool
    github_age_hours: float | None
    papers_age_hours: float | None


class SyncItemStatus(BaseModel):
    """Status for a single sync type."""

    last_sync: str | None
    """ISO timestamp of the most recent successful sync, or None if never synced."""
    next_run: str | None
    """ISO timestamp of the next scheduled run, or None if not scheduled."""


class KnowledgeStats(BaseModel):
    """Counts of items in each knowledge category."""

    github_items: int = 0
    papers: int = 0
    docstrings: int = 0
    discourse_topics: int = 0
    faq_entries: int = 0
    mailing_list_messages: int = 0


class SyncStatusResponse(BaseModel):
    """Complete sync status response."""

    github: GitHubStatus
    papers: PapersStatus
    scheduler: SchedulerStatus
    health: HealthStatus
    syncs: dict[str, SyncItemStatus] = {}
    """Per-sync-type status: github, papers, docstrings, mailman, beps, faq, discourse."""
    knowledge: KnowledgeStats | None = None
    """Counts of items in each knowledge category."""


class TriggerRequest(BaseModel):
    """Request to trigger sync."""

    sync_type: str = (
        "all"  # "github", "papers", "docstrings", "mailman", "faq", "beps", "discourse", or "all"
    )


class TriggerResponse(BaseModel):
    """Response from sync trigger."""

    success: bool
    message: str
    items_synced: dict[str, int]


def _get_sync_metadata(project: str = "hed") -> dict[str, Any]:
    """Get all sync metadata from the community database.

    Returns a dict keyed by source_type (github, papers, beps, docstrings,
    mailman, faq), each containing a dict of source_name -> metadata.
    """
    metadata: dict[str, Any] = {}

    try:
        with get_connection(project) as conn:
            rows = conn.execute(
                "SELECT source_type, source_name, last_sync_at, items_synced FROM sync_metadata"
            ).fetchall()

            for row in rows:
                source_type = row["source_type"]
                source_name = row["source_name"]
                if source_type not in metadata:
                    metadata[source_type] = {}
                metadata[source_type][source_name] = {
                    "last_sync": row["last_sync_at"],
                    "items_synced": row["items_synced"],
                }
    except Exception as e:
        logger.warning("Failed to get sync metadata for %s: %s", project, e, exc_info=True)

    return metadata


def _get_repo_counts(project: str = "hed") -> dict[str, int]:
    """Get item counts per repository for a community."""
    counts: dict[str, int] = {}

    try:
        with get_connection(project) as conn:
            rows = conn.execute(
                "SELECT repo, COUNT(*) as count FROM github_items GROUP BY repo"
            ).fetchall()

            for row in rows:
                counts[row["repo"]] = row["count"]
    except Exception as e:
        logger.warning("Failed to get repo counts for %s: %s", project, e, exc_info=True)

    return counts


def _parse_iso_datetime(iso_str: str | None) -> datetime | None:
    """Parse ISO datetime string."""
    if not iso_str:
        return None
    try:
        # Handle various ISO formats
        if "+" in iso_str or iso_str.endswith("Z"):
            return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return datetime.fromisoformat(iso_str).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _calculate_health(metadata: dict[str, Any]) -> HealthStatus:
    """Calculate health status based on sync ages.

    Health is determined by sync freshness:
    - GitHub: Healthy if synced within 48 hours, or never synced (new install)
    - Papers: Healthy if synced within 2 weeks, or never synced (new install)

    New installations are considered healthy until the first sync should have run.
    """
    now = datetime.now(UTC)

    # Find most recent GitHub sync
    github_last: datetime | None = None
    for repo_data in metadata.get("github", {}).values():
        last = _parse_iso_datetime(repo_data.get("last_sync"))
        if last and (github_last is None or last > github_last):
            github_last = last

    # Find most recent papers sync
    papers_last: datetime | None = None
    for source_data in metadata.get("papers", {}).values():
        last = _parse_iso_datetime(source_data.get("last_sync"))
        if last and (papers_last is None or last > papers_last):
            papers_last = last

    # Calculate ages in hours
    github_age = (now - github_last).total_seconds() / 3600 if github_last else None
    papers_age = (now - papers_last).total_seconds() / 3600 if papers_last else None

    # Health thresholds: GitHub should sync daily (48h grace), papers weekly (2 weeks grace)
    # Never synced (None) is considered healthy - new installation grace period
    github_healthy = github_age is None or github_age < 48
    papers_healthy = papers_age is None or papers_age < (14 * 24)  # 2 weeks

    return HealthStatus(
        healthy=github_healthy,  # Papers are secondary, so health based on GitHub
        github_healthy=github_healthy,
        papers_healthy=papers_healthy,
        github_age_hours=round(github_age, 1) if github_age else None,
        papers_age_hours=round(papers_age, 1) if papers_age else None,
    )


def _get_most_recent_sync(metadata: dict[str, Any], source_type: str) -> str | None:
    """Return the most recent last_sync_at timestamp for a given source_type.

    Parses timestamps via _parse_iso_datetime for correct temporal comparison
    rather than relying on lexicographic string ordering.
    """
    entries = metadata.get(source_type, {})
    parsed: list[tuple[datetime, str]] = []
    for v in entries.values():
        raw = v.get("last_sync")
        if not raw:
            continue
        dt = _parse_iso_datetime(raw)
        if dt is not None:
            parsed.append((dt, raw))
    return max(parsed, key=lambda x: x[0])[1] if parsed else None


@router.get("/status", response_model=SyncStatusResponse)
async def get_sync_status(
    community_id: str | None = Query(default=None),
) -> SyncStatusResponse:
    """Get comprehensive sync status for a community.

    Args:
        community_id: Community to query. Defaults to 'hed' if not specified.

    Returns status of all knowledge sync jobs including:
    - GitHub issues/PRs counts and last sync times per repo
    - Papers counts and last sync times per source
    - All sync types (github, papers, docstrings, mailman, beps, faq) with
      last_sync and next_run timestamps
    - Scheduler status and next run times
    - Health check based on sync ages
    """
    project = community_id or "hed"

    if community_id is not None and registry.get(community_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Community '{community_id}' not found.",
        )

    settings = get_settings()
    stats = get_stats(project)
    metadata = _get_sync_metadata(project)
    repo_counts = _get_repo_counts(project)

    # Build GitHub repos status
    github_repos: dict[str, RepoStatus] = {}
    for repo, count in repo_counts.items():
        repo_meta = metadata.get("github", {}).get(repo, {})
        github_repos[repo] = RepoStatus(
            items=count,
            last_sync=repo_meta.get("last_sync"),
        )

    # Build papers sources status using prefix matching.
    # Stored names are like "openalex:query", "semanticscholar:query", "pubmed:query".
    # "citing_{doi}" entries track citation lookups; they are not included here.
    papers_sources: dict[str, RepoStatus] = {}
    for source in ["openalex", "semanticscholar", "pubmed"]:
        matching = {
            k: v for k, v in metadata.get("papers", {}).items() if k.startswith(f"{source}:")
        }
        parsed = [
            (dt, raw)
            for v in matching.values()
            if (raw := v.get("last_sync")) and (dt := _parse_iso_datetime(raw))
        ]
        last_sync = max(parsed, key=lambda x: x[0])[1] if parsed else None
        papers_sources[source] = RepoStatus(
            items=stats.get(f"papers_{source}", 0),
            last_sync=last_sync,
        )

    # Get scheduler info
    scheduler = get_scheduler()
    jobs: dict[str, str | None] = {}

    if scheduler and scheduler.running:
        try:
            for job in scheduler.get_jobs():
                next_run = job.next_run_time.isoformat() if job.next_run_time else None
                jobs[job.id] = next_run
        except Exception as e:
            logger.error("Failed to get next run times: %s", e, exc_info=True)

    # Build per-sync-type status for all known sync types
    all_sync_types = ("github", "papers", "docstrings", "mailman", "beps", "faq", "discourse")
    syncs: dict[str, SyncItemStatus] = {}
    for sync_type in all_sync_types:
        last_sync = _get_most_recent_sync(metadata, sync_type)
        next_run = jobs.get(f"{sync_type}_{project}")
        # Include if there is any data or a scheduled next run
        if last_sync is not None or next_run is not None:
            syncs[sync_type] = SyncItemStatus(last_sync=last_sync, next_run=next_run)

    return SyncStatusResponse(
        github=GitHubStatus(
            total_items=stats.get("github_total", 0),
            issues=stats.get("github_issues", 0),
            prs=stats.get("github_prs", 0),
            open_items=stats.get("github_open", 0),
            repos=github_repos,
        ),
        papers=PapersStatus(
            total_items=stats.get("papers_total", 0),
            sources=papers_sources,
        ),
        scheduler=SchedulerStatus(
            enabled=settings.sync_enabled,
            running=scheduler is not None and scheduler.running,
            jobs=jobs,
        ),
        health=_calculate_health(metadata),
        syncs=syncs,
        knowledge=KnowledgeStats(
            github_items=stats.get("github_total", 0),
            papers=stats.get("papers_total", 0),
            docstrings=stats.get("docstrings_total", 0),
            discourse_topics=stats.get("discourse_total", 0),
            faq_entries=stats.get("faq_total", 0),
            mailing_list_messages=stats.get("mailing_list_total", 0),
        ),
    )


@router.post("/trigger", response_model=TriggerResponse)
async def trigger_sync(
    request: TriggerRequest,
    _api_key: RequireAdminAuth,
) -> TriggerResponse:
    """Manually trigger a sync job.

    Requires API key authentication.

    Args:
        request: Sync type to trigger (one of "github", "papers", "docstrings", "mailman", "faq", "beps", "discourse", or "all")

    Returns:
        Result of the sync operation
    """
    valid_types = ("github", "papers", "docstrings", "mailman", "faq", "beps", "discourse", "all")
    if request.sync_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sync_type: {request.sync_type}. Must be one of {valid_types}",
        )

    try:
        results = run_sync_now(request.sync_type)
        total = sum(results.values())
        return TriggerResponse(
            success=True,
            message=f"Sync completed: {total} items synced",
            items_synced=results,
        )
    except Exception as e:
        logger.error("Sync trigger failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Sync failed: {e}") from e


@router.get("/health")
async def health_check(
    community_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Simple health check endpoint for monitoring.

    Args:
        community_id: Community to check. Defaults to 'hed' if not specified.

    Returns a simple status suitable for uptime monitors.
    Returns 200 if healthy, 503 if unhealthy.
    """
    project = community_id or "hed"

    if community_id is not None and registry.get(community_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Community '{community_id}' not found.",
        )

    stats = get_stats(project)
    metadata = _get_sync_metadata(project)
    health = _calculate_health(metadata)

    response = {
        "status": "healthy" if health.healthy else "unhealthy",
        "github_items": stats.get("github_total", 0),
        "papers_items": stats.get("papers_total", 0),
        "github_age_hours": health.github_age_hours,
        "papers_age_hours": health.papers_age_hours,
    }

    if not health.healthy:
        raise HTTPException(status_code=503, detail=response)

    return response
