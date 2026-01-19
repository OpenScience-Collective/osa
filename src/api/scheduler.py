"""Background scheduler for automated knowledge sync.

Uses APScheduler to run periodic sync jobs for:
- GitHub issues/PRs (daily by default)
- Academic papers (weekly by default)
"""

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.api.config import get_settings
from src.assistants import discover_assistants, registry
from src.knowledge.db import init_db
from src.knowledge.github_sync import sync_repos
from src.knowledge.papers_sync import sync_all_papers

logger = logging.getLogger(__name__)

# Discover assistants at module load to populate registry
discover_assistants()

# Global scheduler instance
_scheduler: BackgroundScheduler | None = None


def _get_hed_repos() -> list[str]:
    """Get HED repos from the registry."""
    info = registry.get("hed")
    if info and info.community_config and info.community_config.github:
        return info.community_config.github.repos
    logger.warning("HED repos not found in registry, using empty list")
    return []


# Failure tracking for alerting
_github_sync_failures = 0
_papers_sync_failures = 0
MAX_CONSECUTIVE_FAILURES = 3


def _run_github_sync() -> None:
    """Run GitHub sync job."""
    global _github_sync_failures
    logger.info("Starting scheduled GitHub sync")
    try:
        results = sync_repos(_get_hed_repos(), project="hed", incremental=True)
        total = sum(results.values())
        logger.info("GitHub sync complete: %d items synced", total)
        _github_sync_failures = 0  # Reset on success
    except Exception as e:
        _github_sync_failures += 1
        logger.error(
            "GitHub sync failed (attempt %d/%d): %s",
            _github_sync_failures,
            MAX_CONSECUTIVE_FAILURES,
            e,
            exc_info=True,
        )
        if _github_sync_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.critical(
                "GitHub sync has failed %d times consecutively. Manual intervention required.",
                _github_sync_failures,
            )


def _run_papers_sync() -> None:
    """Run papers sync job."""
    global _papers_sync_failures
    settings = get_settings()
    logger.info("Starting scheduled papers sync")
    try:
        results = sync_all_papers(
            semantic_scholar_api_key=settings.semantic_scholar_api_key,
            pubmed_api_key=settings.pubmed_api_key,
        )
        total = sum(results.values())
        logger.info("Papers sync complete: %d items synced", total)
        _papers_sync_failures = 0  # Reset on success
    except Exception as e:
        _papers_sync_failures += 1
        logger.error(
            "Papers sync failed (attempt %d/%d): %s",
            _papers_sync_failures,
            MAX_CONSECUTIVE_FAILURES,
            e,
            exc_info=True,
        )
        if _papers_sync_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.critical(
                "Papers sync has failed %d times consecutively. Manual intervention required.",
                _papers_sync_failures,
            )


def start_scheduler() -> BackgroundScheduler | None:
    """Start the background scheduler with configured sync jobs.

    Returns:
        The scheduler instance, or None if sync is disabled.
    """
    global _scheduler

    settings = get_settings()

    if not settings.sync_enabled:
        logger.info("Sync scheduling is disabled")
        return None

    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return _scheduler

    # Set GITHUB_TOKEN for gh CLI if provided
    if settings.github_token:
        os.environ["GITHUB_TOKEN"] = settings.github_token
        logger.info("GITHUB_TOKEN set for gh CLI")

    # Initialize database
    logger.info("Initializing knowledge database")
    init_db()

    # Create scheduler
    _scheduler = BackgroundScheduler()

    # Add GitHub sync job
    try:
        github_trigger = CronTrigger.from_crontab(settings.sync_github_cron)
        _scheduler.add_job(
            _run_github_sync,
            trigger=github_trigger,
            id="github_sync",
            name="GitHub Issues/PRs Sync",
            replace_existing=True,
        )
        logger.info("GitHub sync scheduled: %s", settings.sync_github_cron)
    except ValueError as e:
        logger.error("Invalid GitHub sync cron expression: %s", e)

    # Add papers sync job
    try:
        papers_trigger = CronTrigger.from_crontab(settings.sync_papers_cron)
        _scheduler.add_job(
            _run_papers_sync,
            trigger=papers_trigger,
            id="papers_sync",
            name="Academic Papers Sync",
            replace_existing=True,
        )
        logger.info("Papers sync scheduled: %s", settings.sync_papers_cron)
    except ValueError as e:
        logger.error("Invalid papers sync cron expression: %s", e)

    # Start the scheduler
    _scheduler.start()
    logger.info("Background scheduler started")

    return _scheduler


def stop_scheduler() -> None:
    """Stop the background scheduler."""
    global _scheduler

    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Background scheduler stopped")


def get_scheduler() -> BackgroundScheduler | None:
    """Get the current scheduler instance."""
    return _scheduler


def run_sync_now(sync_type: str = "all") -> dict[str, int]:
    """Run sync immediately (for manual triggers or initial sync).

    Args:
        sync_type: "github", "papers", or "all"

    Returns:
        Dict mapping source to items synced
    """
    settings = get_settings()
    results: dict[str, int] = {}

    # Set GITHUB_TOKEN for gh CLI if provided
    if settings.github_token:
        os.environ["GITHUB_TOKEN"] = settings.github_token

    # Initialize database
    init_db()

    if sync_type in ("github", "all"):
        logger.info("Running GitHub sync")
        github_results = sync_repos(_get_hed_repos(), project="hed", incremental=True)
        results["github"] = sum(github_results.values())

    if sync_type in ("papers", "all"):
        logger.info("Running papers sync")
        papers_results = sync_all_papers(
            semantic_scholar_api_key=settings.semantic_scholar_api_key,
            pubmed_api_key=settings.pubmed_api_key,
        )
        results["papers"] = sum(papers_results.values())

    return results
