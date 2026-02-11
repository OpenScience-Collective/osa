"""Background scheduler for automated tasks.

Uses APScheduler to run periodic jobs for:
- GitHub issues/PRs sync (daily by default)
- Academic papers sync (weekly by default)
- BEP (BIDS Extension Proposals) sync (weekly by default)
- Community budget checks with alert issue creation (every 15 minutes)
"""

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.api.config import get_settings
from src.assistants import registry
from src.knowledge.bep_sync import sync_beps
from src.knowledge.db import init_db
from src.knowledge.github_sync import sync_repos
from src.knowledge.papers_sync import sync_all_papers, sync_citing_papers
from src.metrics.alerts import create_budget_alert_issue
from src.metrics.budget import check_budget
from src.metrics.db import metrics_connection

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: BackgroundScheduler | None = None


def _get_communities_with_sync() -> list[str]:
    """Get all community IDs that have sync configuration.

    Returns:
        List of community IDs with GitHub repos or citation config.
    """
    return [info.id for info in registry.list_all() if info.sync_config]


def _get_community_repos(community_id: str) -> list[str]:
    """Get GitHub repos for a community from the registry."""
    info = registry.get(community_id)
    if info and info.community_config and info.community_config.github:
        return info.community_config.github.repos
    return []


def _get_community_paper_queries(community_id: str) -> list[str]:
    """Get paper queries for a community from the registry."""
    info = registry.get(community_id)
    if info and info.community_config and info.community_config.citations:
        return info.community_config.citations.queries
    return []


def _get_community_paper_dois(community_id: str) -> list[str]:
    """Get paper DOIs for a community from the registry."""
    info = registry.get(community_id)
    if info and info.community_config and info.community_config.citations:
        return info.community_config.citations.dois
    return []


# Failure tracking for alerting
_github_sync_failures = 0
_papers_sync_failures = 0
_beps_sync_failures = 0
_budget_check_failures = 0
MAX_CONSECUTIVE_FAILURES = 3


def _run_github_sync() -> None:
    """Run GitHub sync job for all communities."""
    global _github_sync_failures
    logger.info("Starting scheduled GitHub sync for all communities")
    try:
        communities = _get_communities_with_sync()
        if not communities:
            logger.info("No communities with sync configuration found")
            return

        grand_total = 0
        for community_id in communities:
            repos = _get_community_repos(community_id)
            if not repos:
                logger.debug("No GitHub repos configured for %s", community_id)
                continue

            logger.info("Syncing GitHub for community: %s", community_id)
            results = sync_repos(repos, project=community_id, incremental=True)
            total = sum(results.values())
            grand_total += total
            logger.info("GitHub sync complete for %s: %d items", community_id, total)

        logger.info("GitHub sync complete for all communities: %d items synced", grand_total)
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
    """Run papers sync job for all communities."""
    global _papers_sync_failures
    settings = get_settings()
    logger.info(
        "Starting scheduled papers sync for all communities "
        "(OpenAlex key: %s, S2 key: %s, PubMed key: %s)",
        "configured" if settings.openalex_api_key else "none",
        "configured" if settings.semantic_scholar_api_key else "none",
        "configured" if settings.pubmed_api_key else "none",
    )
    try:
        communities = _get_communities_with_sync()
        if not communities:
            logger.info("No communities with sync configuration found")
            return

        grand_total = 0
        for community_id in communities:
            queries = _get_community_paper_queries(community_id)
            dois = _get_community_paper_dois(community_id)

            if not queries and not dois:
                logger.debug("No paper queries/DOIs configured for %s", community_id)
                continue

            logger.info("Syncing papers for community: %s", community_id)
            community_total = 0

            # Sync papers by query
            if queries:
                results = sync_all_papers(
                    queries=queries,
                    semantic_scholar_api_key=settings.semantic_scholar_api_key,
                    pubmed_api_key=settings.pubmed_api_key,
                    openalex_api_key=settings.openalex_api_key,
                    openalex_email=settings.openalex_email,
                    project=community_id,
                )
                community_total += sum(results.values())

            # Sync citing papers by DOI
            if dois:
                citing_count = sync_citing_papers(
                    dois,
                    project=community_id,
                    openalex_api_key=settings.openalex_api_key,
                    openalex_email=settings.openalex_email,
                )
                community_total += citing_count

            grand_total += community_total
            logger.info("Papers sync complete for %s: %d items", community_id, community_total)

        logger.info("Papers sync complete for all communities: %d items synced", grand_total)
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


def _run_beps_sync() -> None:
    """Run BEP sync job for the BIDS community."""
    global _beps_sync_failures
    logger.info("Starting scheduled BEP sync")
    try:
        init_db("bids")
        stats = sync_beps("bids")
        logger.info(
            "BEP sync complete: %d total, %d with content",
            stats["total"],
            stats["with_content"],
        )
        _beps_sync_failures = 0
    except Exception as e:
        _beps_sync_failures += 1
        logger.error(
            "BEP sync failed (attempt %d/%d): %s",
            _beps_sync_failures,
            MAX_CONSECUTIVE_FAILURES,
            e,
            exc_info=True,
        )
        if _beps_sync_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.critical(
                "BEP sync has failed %d times consecutively. Manual intervention required.",
                _beps_sync_failures,
            )


def _check_community_budgets() -> None:
    """Check budget limits for all communities and create alert issues if exceeded."""
    global _budget_check_failures
    logger.info("Starting scheduled budget check for all communities")
    try:
        communities_checked = 0
        communities_failed = 0
        alerts_created = 0

        with metrics_connection() as conn:
            for info in registry.list_all():
                if not info.community_config or not info.community_config.budget:
                    continue

                budget_cfg = info.community_config.budget
                maintainers = info.community_config.maintainers

                try:
                    budget_status = check_budget(
                        community_id=info.id,
                        config=budget_cfg,
                        conn=conn,
                    )
                    communities_checked += 1

                    if budget_status.needs_alert:
                        issue_url = create_budget_alert_issue(
                            budget_status=budget_status,
                            maintainers=maintainers,
                        )
                        if issue_url:
                            alerts_created += 1
                            logger.warning(
                                "Budget alert created for %s: %s",
                                info.id,
                                issue_url,
                            )
                except Exception:
                    communities_failed += 1
                    logger.exception("Failed to check budget for community %s", info.id)

        log_level = logging.WARNING if communities_failed else logging.INFO
        logger.log(
            log_level,
            "Budget check complete: %d checked, %d failed, %d alerts created",
            communities_checked,
            communities_failed,
            alerts_created,
        )
        if communities_failed:
            _budget_check_failures += 1
            if _budget_check_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.critical(
                    "Budget check has had community failures for %d consecutive runs. "
                    "Manual intervention required.",
                    _budget_check_failures,
                )
        else:
            _budget_check_failures = 0
    except Exception:
        _budget_check_failures += 1
        logger.error(
            "Budget check job failed (attempt %d/%d)",
            _budget_check_failures,
            MAX_CONSECUTIVE_FAILURES,
            exc_info=True,
        )
        if _budget_check_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.critical(
                "Budget check has failed %d times consecutively. Manual intervention required.",
                _budget_check_failures,
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

    # Set GITHUB_TOKEN for GitHub API requests (gh CLI, sync jobs, BEP fetching)
    if settings.github_token:
        os.environ["GITHUB_TOKEN"] = settings.github_token
        logger.info("GITHUB_TOKEN set for GitHub API requests")

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

    # Add BEP sync job (weekly, for BIDS community)
    try:
        beps_trigger = CronTrigger.from_crontab(settings.sync_beps_cron)
        _scheduler.add_job(
            _run_beps_sync,
            trigger=beps_trigger,
            id="beps_sync",
            name="BIDS Extension Proposals Sync",
            replace_existing=True,
        )
        logger.info("BEP sync scheduled: %s", settings.sync_beps_cron)
    except ValueError as e:
        logger.error("Invalid BEP sync cron expression: %s", e)

    # Add budget check job (every 15 minutes)
    try:
        budget_trigger = CronTrigger(minute="*/15")
        _scheduler.add_job(
            _check_community_budgets,
            trigger=budget_trigger,
            id="budget_check",
            name="Community Budget Check",
            replace_existing=True,
        )
        logger.info("Budget check scheduled: every 15 minutes")
    except ValueError as e:
        logger.error("Failed to schedule budget check: %s", e)

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
    """Run sync immediately for all communities (for manual triggers or initial sync).

    Args:
        sync_type: "github", "papers", "beps", or "all"

    Returns:
        Dict mapping source to items synced across all communities
    """
    settings = get_settings()
    results: dict[str, int] = {}

    # Set GITHUB_TOKEN for gh CLI if provided
    if settings.github_token:
        os.environ["GITHUB_TOKEN"] = settings.github_token

    # Initialize database
    init_db()

    github_total = 0
    papers_total = 0

    communities = _get_communities_with_sync()
    if not communities:
        logger.info("No communities with sync configuration found")
        results["github"] = 0
        results["papers"] = 0
        return results

    for community_id in communities:
        if sync_type in ("github", "all"):
            repos = _get_community_repos(community_id)
            if repos:
                logger.info("Running GitHub sync for %s", community_id)
                github_results = sync_repos(repos, project=community_id, incremental=True)
                github_total += sum(github_results.values())

        if sync_type in ("papers", "all"):
            queries = _get_community_paper_queries(community_id)
            dois = _get_community_paper_dois(community_id)

            if queries or dois:
                logger.info("Running papers sync for %s", community_id)
                community_papers = 0

                if queries:
                    papers_results = sync_all_papers(
                        queries=queries,
                        semantic_scholar_api_key=settings.semantic_scholar_api_key,
                        pubmed_api_key=settings.pubmed_api_key,
                        openalex_api_key=settings.openalex_api_key,
                        openalex_email=settings.openalex_email,
                        project=community_id,
                    )
                    community_papers += sum(papers_results.values())

                if dois:
                    citing_count = sync_citing_papers(
                        dois,
                        project=community_id,
                        openalex_api_key=settings.openalex_api_key,
                        openalex_email=settings.openalex_email,
                    )
                    community_papers += citing_count

                papers_total += community_papers

    # BEP sync (BIDS community only)
    if sync_type in ("beps", "all") and "bids" in communities:
        logger.info("Running BEP sync for BIDS")
        try:
            init_db("bids")
            bep_stats = sync_beps("bids")
            results["beps"] = bep_stats["total"]
        except Exception:
            logger.error("BEP sync failed during run_sync_now", exc_info=True)
            results["beps"] = 0

    results["github"] = github_total
    results["papers"] = papers_total

    return results
