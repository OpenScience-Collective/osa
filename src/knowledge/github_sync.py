"""GitHub sync using gh CLI.

Syncs issues and PRs from configured repositories.
Each assistant can configure its own repos via sync_config.

Only stores title, first message (body), status, URL, and created date.
No replies or comments are stored.
"""

import json
import logging
import subprocess
from typing import Any

from src.knowledge.db import get_connection, get_last_sync, update_sync_metadata, upsert_github_item

logger = logging.getLogger(__name__)


def _run_gh(args: list[str], timeout: int = 120) -> list[dict[str, Any]]:
    """Run gh CLI command and return JSON result.

    Args:
        args: Arguments for gh command (without 'gh' prefix)
        timeout: Command timeout in seconds

    Returns:
        Parsed JSON response (list of items)

    Raises:
        RuntimeError: If gh command fails
    """
    cmd = ["gh"] + args
    logger.debug("Running: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr}")

    return json.loads(result.stdout) if result.stdout.strip() else []


def sync_repo_issues(repo: str, project: str = "hed", since: str | None = None) -> int:
    """Sync issues from a repository.

    Args:
        repo: Repository in owner/name format
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        since: Optional ISO date to sync from (for incremental sync)

    Returns:
        Number of items synced
    """
    fields = "number,title,body,state,createdAt,url"
    args = [
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        "500",
        "--json",
        fields,
    ]

    try:
        items = _run_gh(args)
    except subprocess.TimeoutExpired:
        logger.warning("Timeout syncing issues from %s", repo)
        return 0
    except RuntimeError as e:
        logger.warning("gh CLI error for %s: %s", repo, e)
        return 0
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON from gh for %s: %s", repo, e)
        return 0

    count = 0
    with get_connection(project) as conn:
        for item in items:
            # Skip if before since date (for incremental sync)
            if since and item.get("createdAt", "") < since:
                continue

            upsert_github_item(
                conn,
                repo=repo,
                item_type="issue",
                number=item["number"],
                title=item["title"],
                first_message=item.get("body"),
                status="open" if item.get("state") == "OPEN" else "closed",
                url=item["url"],
                created_at=item["createdAt"],
            )
            count += 1
        conn.commit()

    logger.info("Synced %d issues from %s to %s.db", count, repo, project)
    return count


def sync_repo_prs(repo: str, project: str = "hed", since: str | None = None) -> int:
    """Sync PRs from a repository.

    Args:
        repo: Repository in owner/name format
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        since: Optional ISO date to sync from (for incremental sync)

    Returns:
        Number of items synced
    """
    fields = "number,title,body,state,createdAt,url"
    args = [
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        "500",
        "--json",
        fields,
    ]

    try:
        items = _run_gh(args)
    except subprocess.TimeoutExpired:
        logger.warning("Timeout syncing PRs from %s", repo)
        return 0
    except RuntimeError as e:
        logger.warning("gh CLI error for %s: %s", repo, e)
        return 0
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON from gh for %s: %s", repo, e)
        return 0

    count = 0
    with get_connection(project) as conn:
        for item in items:
            # Skip if before since date (for incremental sync)
            if since and item.get("createdAt", "") < since:
                continue

            # Map PR state (OPEN, CLOSED, MERGED) to simple status
            state = item.get("state", "CLOSED")
            status = "open" if state == "OPEN" else "closed"

            upsert_github_item(
                conn,
                repo=repo,
                item_type="pr",
                number=item["number"],
                title=item["title"],
                first_message=item.get("body"),
                status=status,
                url=item["url"],
                created_at=item["createdAt"],
            )
            count += 1
        conn.commit()

    logger.info("Synced %d PRs from %s to %s.db", count, repo, project)
    return count


def sync_repo(repo: str, project: str = "hed", incremental: bool = True) -> int:
    """Sync both issues and PRs from a repository.

    Args:
        repo: Repository in owner/name format
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        incremental: If True, only sync items since last sync

    Returns:
        Total number of items synced
    """
    since = None
    if incremental:
        since = get_last_sync("github", repo, project)
        if since:
            logger.info("Incremental sync from %s for %s", since, repo)

    issues = sync_repo_issues(repo, project, since)
    prs = sync_repo_prs(repo, project, since)
    total = issues + prs

    update_sync_metadata("github", repo, total, project)
    return total


def sync_repos(repos: list[str], project: str = "hed", incremental: bool = True) -> dict[str, int]:
    """Sync multiple repositories for a project.

    Args:
        repos: List of repositories in owner/name format
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        incremental: If True, only sync items since last sync

    Returns:
        Dict mapping repo to items synced
    """
    results = {}
    for repo in repos:
        count = sync_repo(repo, project, incremental)
        results[repo] = count

    total = sum(results.values())
    logger.info("Total items synced for %s: %d", project, total)
    return results


# ---------------------------------------------------------------------------
# Backward compatibility exports (used by CLI sync commands)
# TODO: Update CLI to use registry-based sync and remove these
# ---------------------------------------------------------------------------

# Import HED repos from the HED assistant's sync config
from src.assistants.hed.sync import HED_REPOS  # noqa: E402


def sync_all_hed_repos(incremental: bool = True) -> dict[str, int]:
    """Sync all HED repositories.

    This is a backward-compatible wrapper that uses the new sync_repos function
    with HED-specific configuration.

    Args:
        incremental: If True, only sync items since last sync

    Returns:
        Dict mapping repo to items synced
    """
    return sync_repos(HED_REPOS, project="hed", incremental=incremental)
