"""Sync status and management API endpoints.

Provides endpoints for:
- Checking knowledge sync status
- Manually triggering sync jobs
- Health checks for monitoring
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.config import get_settings
from src.api.scheduler import get_scheduler, run_sync_now
from src.api.security import verify_api_key
from src.knowledge.db import get_connection, get_db_path, get_stats

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
    github_cron: str
    papers_cron: str
    next_github_sync: str | None
    next_papers_sync: str | None


class HealthStatus(BaseModel):
    """Health check status."""

    healthy: bool
    github_healthy: bool
    papers_healthy: bool
    github_age_hours: float | None
    papers_age_hours: float | None


class SyncStatusResponse(BaseModel):
    """Complete sync status response."""

    github: GitHubStatus
    papers: PapersStatus
    scheduler: SchedulerStatus
    health: HealthStatus
    database_path: str


class TriggerRequest(BaseModel):
    """Request to trigger sync."""

    sync_type: str = "all"  # "github", "papers", or "all"


class TriggerResponse(BaseModel):
    """Response from sync trigger."""

    success: bool
    message: str
    items_synced: dict[str, int]


def _get_sync_metadata() -> dict[str, Any]:
    """Get all sync metadata from database."""
    metadata: dict[str, Any] = {"github": {}, "papers": {}}

    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT source_type, source_name, last_sync_at, items_synced FROM sync_metadata"
            ).fetchall()

            for row in rows:
                source_type = row["source_type"]
                source_name = row["source_name"]
                if source_type in metadata:
                    metadata[source_type][source_name] = {
                        "last_sync": row["last_sync_at"],
                        "items_synced": row["items_synced"],
                    }
    except Exception as e:
        logger.warning("Failed to get sync metadata: %s", e)

    return metadata


def _get_repo_counts() -> dict[str, int]:
    """Get item counts per repository."""
    counts: dict[str, int] = {}

    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT repo, COUNT(*) as count FROM github_items GROUP BY repo"
            ).fetchall()

            for row in rows:
                counts[row["repo"]] = row["count"]
    except Exception as e:
        logger.warning("Failed to get repo counts: %s", e)

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
    """Calculate health status based on sync ages."""
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
    github_healthy = github_age is not None and github_age < 48
    papers_healthy = papers_age is not None and papers_age < (14 * 24)  # 2 weeks

    return HealthStatus(
        healthy=github_healthy,  # Papers are secondary, so health based on GitHub
        github_healthy=github_healthy,
        papers_healthy=papers_healthy,
        github_age_hours=round(github_age, 1) if github_age else None,
        papers_age_hours=round(papers_age, 1) if papers_age else None,
    )


@router.get("/status", response_model=SyncStatusResponse)
async def get_sync_status() -> SyncStatusResponse:
    """Get comprehensive sync status.

    Returns status of all knowledge sync jobs including:
    - GitHub issues/PRs counts and last sync times per repo
    - Papers counts and last sync times per source
    - Scheduler status and next run times
    - Health check based on sync ages
    """
    settings = get_settings()
    stats = get_stats()
    metadata = _get_sync_metadata()
    repo_counts = _get_repo_counts()

    # Build GitHub repos status
    github_repos: dict[str, RepoStatus] = {}
    for repo, count in repo_counts.items():
        repo_meta = metadata.get("github", {}).get(repo, {})
        github_repos[repo] = RepoStatus(
            items=count,
            last_sync=repo_meta.get("last_sync"),
        )

    # Build papers sources status
    papers_sources: dict[str, RepoStatus] = {}
    for source in ["openalex", "semanticscholar", "pubmed"]:
        source_meta = metadata.get("papers", {}).get(source, {})
        papers_sources[source] = RepoStatus(
            items=stats.get(f"papers_{source}", 0),
            last_sync=source_meta.get("last_sync"),
        )

    # Get scheduler info
    scheduler = get_scheduler()
    next_github: str | None = None
    next_papers: str | None = None

    if scheduler and scheduler.running:
        try:
            github_job = scheduler.get_job("github_sync")
            if github_job and github_job.next_run_time:
                next_github = github_job.next_run_time.isoformat()

            papers_job = scheduler.get_job("papers_sync")
            if papers_job and papers_job.next_run_time:
                next_papers = papers_job.next_run_time.isoformat()
        except Exception as e:
            logger.warning("Failed to get next run times: %s", e)

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
            github_cron=settings.sync_github_cron,
            papers_cron=settings.sync_papers_cron,
            next_github_sync=next_github,
            next_papers_sync=next_papers,
        ),
        health=_calculate_health(metadata),
        database_path=str(get_db_path()),
    )


@router.post("/trigger", response_model=TriggerResponse)
async def trigger_sync(
    request: TriggerRequest,
    _api_key: str = Depends(verify_api_key),
) -> TriggerResponse:
    """Manually trigger a sync job.

    Requires API key authentication.

    Args:
        request: Sync type to trigger ("github", "papers", or "all")

    Returns:
        Result of the sync operation
    """
    if request.sync_type not in ("github", "papers", "all"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sync_type: {request.sync_type}. Must be 'github', 'papers', or 'all'",
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
        logger.error("Sync trigger failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Sync failed: {e}") from e


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Simple health check endpoint for monitoring.

    Returns a simple status suitable for uptime monitors.
    Returns 200 if healthy, 503 if unhealthy.
    """
    stats = get_stats()
    metadata = _get_sync_metadata()
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
