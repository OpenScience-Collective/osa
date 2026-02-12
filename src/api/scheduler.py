"""Background scheduler for automated knowledge sync.

Uses APScheduler to run per-community sync jobs based on each community's
sync configuration in their config.yaml. Sync types:
- GitHub issues/PRs sync
- Academic papers sync
- Code docstring extraction
- Mailing list archive sync
- FAQ generation from discussions (LLM-powered)
- BIDS Extension Proposals (BEP) sync
- Community budget checks (every 15 minutes, global)

Each community controls its own schedule via the `sync:` section in config.yaml.
On startup, empty databases are automatically seeded with an immediate sync.
"""

import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.api.config import get_settings
from src.assistants import registry
from src.knowledge.bep_sync import sync_beps
from src.knowledge.db import init_db, is_db_populated
from src.knowledge.github_sync import sync_repos
from src.knowledge.papers_sync import sync_all_papers, sync_citing_papers
from src.metrics.alerts import create_budget_alert_issue
from src.metrics.budget import check_budget
from src.metrics.db import metrics_connection

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: BackgroundScheduler | None = None

# Per-community, per-sync-type failure tracking
_sync_failures: dict[str, int] = {}
_sync_failures_lock = threading.Lock()
_budget_check_failures = 0
MAX_CONSECUTIVE_FAILURES = 3


def _failure_key(sync_type: str, community_id: str) -> str:
    return f"{sync_type}_{community_id}"


def _track_failure(sync_type: str, community_id: str, error: Exception) -> None:
    """Track a sync failure and log appropriately."""
    key = _failure_key(sync_type, community_id)
    with _sync_failures_lock:
        _sync_failures[key] = _sync_failures.get(key, 0) + 1
        count = _sync_failures[key]
    logger.error(
        "%s sync failed for %s (attempt %d/%d): %s",
        sync_type,
        community_id,
        count,
        MAX_CONSECUTIVE_FAILURES,
        error,
        exc_info=True,
    )
    if count >= MAX_CONSECUTIVE_FAILURES:
        logger.critical(
            "%s sync for %s has failed %d times consecutively. Manual intervention required.",
            sync_type,
            community_id,
            count,
        )


def _reset_failure(sync_type: str, community_id: str) -> None:
    """Reset failure count on success."""
    key = _failure_key(sync_type, community_id)
    with _sync_failures_lock:
        _sync_failures.pop(key, None)


# ---------------------------------------------------------------------------
# Per-community sync job functions
# ---------------------------------------------------------------------------


def _run_github_sync_for_community(community_id: str) -> None:
    """Run GitHub sync for a single community."""
    logger.info("Starting scheduled GitHub sync for %s", community_id)
    try:
        info = registry.get(community_id)
        if not info or not info.community_config or not info.community_config.github:
            logger.debug("No GitHub repos configured for %s", community_id)
            return

        repos = info.community_config.github.repos
        init_db(community_id)
        results = sync_repos(repos, project=community_id, incremental=True)
        total = sum(results.values())
        logger.info("GitHub sync complete for %s: %d items", community_id, total)
        _reset_failure("github", community_id)
    except Exception as e:
        _track_failure("github", community_id, e)


def _run_papers_sync_for_community(community_id: str) -> None:
    """Run papers sync for a single community."""
    settings = get_settings()
    logger.info("Starting scheduled papers sync for %s", community_id)
    try:
        info = registry.get(community_id)
        if not info or not info.community_config or not info.community_config.citations:
            logger.debug("No citation config for %s", community_id)
            return

        citations = info.community_config.citations
        init_db(community_id)
        total = 0

        if citations.queries:
            results = sync_all_papers(
                queries=citations.queries,
                semantic_scholar_api_key=settings.semantic_scholar_api_key,
                pubmed_api_key=settings.pubmed_api_key,
                openalex_api_key=settings.openalex_api_key,
                openalex_email=settings.openalex_email,
                project=community_id,
            )
            total += sum(results.values())

        if citations.dois:
            citing_count = sync_citing_papers(
                citations.dois,
                project=community_id,
                openalex_api_key=settings.openalex_api_key,
                openalex_email=settings.openalex_email,
            )
            total += citing_count

        logger.info("Papers sync complete for %s: %d items", community_id, total)
        _reset_failure("papers", community_id)
    except Exception as e:
        _track_failure("papers", community_id, e)


def _run_docstrings_sync_for_community(community_id: str) -> None:
    """Run docstring extraction sync for a single community."""
    logger.info("Starting scheduled docstrings sync for %s", community_id)
    try:
        info = registry.get(community_id)
        if not info or not info.community_config or not info.community_config.docstrings:
            logger.debug("No docstrings config for %s", community_id)
            return

        from src.knowledge.docstring_sync import sync_repo_docstrings

        init_db(community_id)
        total = 0
        for repo_config in info.community_config.docstrings.repos:
            for language in repo_config.languages:
                count = sync_repo_docstrings(
                    repo_config.repo,
                    language,
                    project=community_id,
                    branch=repo_config.branch,
                )
                total += count
                logger.info(
                    "Docstrings sync: %d %s symbols from %s@%s",
                    count,
                    language,
                    repo_config.repo,
                    repo_config.branch,
                )

        logger.info("Docstrings sync complete for %s: %d total symbols", community_id, total)
        _reset_failure("docstrings", community_id)
    except Exception as e:
        _track_failure("docstrings", community_id, e)


def _run_mailman_sync_for_community(community_id: str) -> None:
    """Run mailing list archive sync for a single community."""
    logger.info("Starting scheduled mailman sync for %s", community_id)
    try:
        info = registry.get(community_id)
        if not info or not info.community_config or not info.community_config.mailman:
            logger.debug("No mailman config for %s", community_id)
            return

        from src.knowledge.mailman_sync import sync_mailing_list

        init_db(community_id)
        grand_total = 0
        for mailman_config in info.community_config.mailman:
            results = sync_mailing_list(
                list_name=mailman_config.list_name,
                base_url=str(mailman_config.base_url),
                project=community_id,
                start_year=mailman_config.start_year,
            )
            total = sum(results.values())
            grand_total += total
            logger.info(
                "Mailman sync: %d messages from %s",
                total,
                mailman_config.list_name,
            )

        logger.info("Mailman sync complete for %s: %d total messages", community_id, grand_total)
        _reset_failure("mailman", community_id)
    except Exception as e:
        _track_failure("mailman", community_id, e)


def _run_faq_sync_for_community(community_id: str) -> None:
    """Run FAQ generation sync for a single community."""
    logger.info("Starting scheduled FAQ sync for %s", community_id)
    try:
        info = registry.get(community_id)
        if not info or not info.community_config:
            logger.debug("No community config for %s", community_id)
            return

        config = info.community_config
        if not config.mailman or not config.faq_generation:
            logger.debug("No FAQ generation config for %s", community_id)
            return

        from src.knowledge.faq_summarizer import summarize_threads

        init_db(community_id)
        list_names = [m.list_name for m in config.mailman]

        for list_name in list_names:
            result = summarize_threads(
                list_name=list_name,
                project=community_id,
                quality_threshold=config.faq_generation.quality_threshold,
            )
            logger.info(
                "FAQ sync for %s/%s: %d created, %d skipped",
                community_id,
                list_name,
                result.get("created", 0),
                result.get("skipped", 0),
            )

        _reset_failure("faq", community_id)
    except Exception as e:
        _track_failure("faq", community_id, e)


def _run_beps_sync_for_community(community_id: str) -> None:
    """Run BEP sync for a single community (typically BIDS only)."""
    logger.info("Starting scheduled BEP sync for %s", community_id)
    try:
        init_db(community_id)
        stats = sync_beps(community_id)
        logger.info(
            "BEP sync complete for %s: %d total, %d with content",
            community_id,
            stats["total"],
            stats["with_content"],
        )
        _reset_failure("beps", community_id)
    except Exception as e:
        _track_failure("beps", community_id, e)


# ---------------------------------------------------------------------------
# Budget check (global, not per-community scheduled)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Sync type to job function mapping
# ---------------------------------------------------------------------------

# Maps sync type name to (job_function, data_config_check)
# data_config_check returns truthy if the community has the necessary data config
_SYNC_TYPE_MAP: dict[str, tuple[Callable[[str], None], Callable[[Any], Any]]] = {
    "github": (
        _run_github_sync_for_community,
        lambda cfg: cfg.github and cfg.github.repos,
    ),
    "papers": (
        _run_papers_sync_for_community,
        lambda cfg: cfg.citations and (cfg.citations.queries or cfg.citations.dois),
    ),
    "docstrings": (
        _run_docstrings_sync_for_community,
        lambda cfg: cfg.docstrings and cfg.docstrings.repos,
    ),
    "mailman": (
        _run_mailman_sync_for_community,
        lambda cfg: bool(cfg.mailman),
    ),
    "faq": (
        _run_faq_sync_for_community,
        lambda cfg: bool(cfg.mailman) and cfg.faq_generation is not None,
    ),
    "beps": (
        _run_beps_sync_for_community,
        lambda _cfg: True,  # BEP sync doesn't need special data config
    ),
}


# ---------------------------------------------------------------------------
# Startup seed: populate empty databases
# ---------------------------------------------------------------------------


def _check_and_seed_databases() -> None:
    """Check for empty databases and trigger immediate sync if needed.

    Runs in a background thread to avoid blocking app startup.
    Only seeds sync types that the community has configured in both
    its data config and sync schedule. FAQ is excluded from startup
    seeding because it requires LLM calls (expensive, slow) and depends
    on mailman data being populated first.
    """
    try:
        _do_seed_databases()
    except Exception:
        logger.error("Startup database seeding crashed unexpectedly", exc_info=True)


def _do_seed_databases() -> None:
    """Inner implementation for database seeding (separated for error boundary)."""
    logger.info("Checking for empty knowledge databases that need seeding")
    seeded_any = False

    for info in registry.list_all():
        if not info.community_config or not info.community_config.sync:
            continue

        community_id = info.id
        config = info.community_config
        sync_config = config.sync
        populated = is_db_populated(community_id)

        # FAQ excluded: requires LLM calls and depends on mailman data
        for sync_type in ("github", "papers", "docstrings", "mailman", "beps"):
            schedule = getattr(sync_config, sync_type, None)
            if not schedule:
                continue

            job_func, data_check = _SYNC_TYPE_MAP[sync_type]
            if not data_check(config):
                continue

            if not populated.get(sync_type, False):
                logger.info(
                    "Empty %s database for %s, triggering seed sync",
                    sync_type,
                    community_id,
                )
                try:
                    init_db(community_id)
                    job_func(community_id)
                    seeded_any = True
                except Exception:
                    logger.error(
                        "Seed sync failed for %s/%s",
                        community_id,
                        sync_type,
                        exc_info=True,
                    )

    if seeded_any:
        logger.info("Startup database seeding complete")
    else:
        logger.info("All community databases already populated, no seeding needed")


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------


def start_scheduler() -> BackgroundScheduler | None:
    """Start the background scheduler with per-community sync jobs.

    Reads each community's sync config from their YAML configuration
    and registers APScheduler jobs accordingly. Also triggers immediate
    sync for any empty databases.

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

    # Create scheduler
    _scheduler = BackgroundScheduler()
    jobs_registered = 0

    # Register per-community sync jobs
    for info in registry.list_all():
        if not info.community_config or not info.community_config.sync:
            continue

        community_id = info.id
        config = info.community_config
        sync_config = config.sync

        for sync_type, (job_func, data_check) in _SYNC_TYPE_MAP.items():
            schedule = getattr(sync_config, sync_type, None)
            if not schedule:
                continue

            if not data_check(config):
                logger.warning(
                    "Community %s has sync schedule for %s but no data config, skipping",
                    community_id,
                    sync_type,
                )
                continue

            job_id = f"{sync_type}_{community_id}"
            try:
                trigger = CronTrigger.from_crontab(schedule.cron)
                _scheduler.add_job(
                    job_func,
                    trigger=trigger,
                    args=[community_id],
                    id=job_id,
                    name=f"{sync_type} sync for {community_id}",
                    replace_existing=True,
                )
                jobs_registered += 1
                logger.info(
                    "Scheduled %s sync for %s: %s",
                    sync_type,
                    community_id,
                    schedule.cron,
                )
            except ValueError as e:
                logger.error(
                    "Invalid cron expression for %s/%s: %s",
                    community_id,
                    sync_type,
                    e,
                )

    # Budget check (global, every 15 minutes)
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
    logger.info("Background scheduler started with %d sync jobs", jobs_registered)

    # Seed empty databases in background thread (non-blocking)
    seed_thread = threading.Thread(
        target=_check_and_seed_databases,
        name="db-seed",
        daemon=True,
    )
    seed_thread.start()

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
    """Run sync immediately for all communities (for manual triggers).

    Args:
        sync_type: "github", "papers", "docstrings", "mailman", "faq", "beps", or "all"

    Returns:
        Dict mapping sync type to number of communities successfully synced.
    """
    settings = get_settings()
    results: dict[str, int] = {}

    # Set GITHUB_TOKEN for gh CLI if provided
    if settings.github_token:
        os.environ["GITHUB_TOKEN"] = settings.github_token

    if sync_type != "all" and sync_type not in _SYNC_TYPE_MAP:
        logger.warning("Unknown sync_type requested: %s", sync_type)
        return results

    sync_types_to_run = list(_SYNC_TYPE_MAP.keys()) if sync_type == "all" else [sync_type]

    for info in registry.list_all():
        if not info.community_config:
            continue

        community_id = info.id
        config = info.community_config

        for st in sync_types_to_run:
            job_func, data_check = _SYNC_TYPE_MAP[st]
            if not data_check(config):
                continue

            try:
                init_db(community_id)
                job_func(community_id)
                results[st] = results.get(st, 0) + 1
            except Exception:
                logger.error(
                    "Manual %s sync failed for %s",
                    st,
                    community_id,
                    exc_info=True,
                )

    return results
