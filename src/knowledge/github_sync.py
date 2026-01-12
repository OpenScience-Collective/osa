"""GitHub sync using gh CLI.

Syncs issues and PRs from HED repositories:
- hed-standard/hed-specification
- hed-standard/hed-javascript
- hed-standard/hed-schemas

Only stores title, first message (body), status, URL, and created date.
No replies or comments are stored.
"""

import json
import logging
import subprocess
from typing import Any

from src.knowledge.db import get_connection, get_last_sync, update_sync_metadata, upsert_github_item

logger = logging.getLogger(__name__)

# HED-specific repos (per user requirements)
HED_REPOS = [
    "hed-standard/hed-specification",
    "hed-standard/hed-javascript",
    "hed-standard/hed-schemas",
]


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


def sync_repo_issues(repo: str, since: str | None = None) -> int:
    """Sync issues from a repository.

    Args:
        repo: Repository in owner/name format
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
    with get_connection() as conn:
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

    logger.info("Synced %d issues from %s", count, repo)
    return count


def sync_repo_prs(repo: str, since: str | None = None) -> int:
    """Sync PRs from a repository.

    Args:
        repo: Repository in owner/name format
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
    with get_connection() as conn:
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

    logger.info("Synced %d PRs from %s", count, repo)
    return count


def sync_repo(repo: str, incremental: bool = True) -> int:
    """Sync both issues and PRs from a repository.

    Args:
        repo: Repository in owner/name format
        incremental: If True, only sync items since last sync

    Returns:
        Total number of items synced
    """
    since = None
    if incremental:
        since = get_last_sync("github", repo)
        if since:
            logger.info("Incremental sync from %s for %s", since, repo)

    issues = sync_repo_issues(repo, since)
    prs = sync_repo_prs(repo, since)
    total = issues + prs

    update_sync_metadata("github", repo, total)
    return total


def sync_all_hed_repos(incremental: bool = True) -> dict[str, int]:
    """Sync all HED repositories.

    Args:
        incremental: If True, only sync items since last sync

    Returns:
        Dict mapping repo to items synced
    """
    results = {}
    for repo in HED_REPOS:
        count = sync_repo(repo, incremental)
        results[repo] = count

    total = sum(results.values())
    logger.info("Total items synced from HED repos: %d", total)
    return results
