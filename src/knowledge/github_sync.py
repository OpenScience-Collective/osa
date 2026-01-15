"""GitHub sync using REST API.

Syncs issues and PRs from configured repositories.
Each assistant can configure its own repos via sync_config.

Only stores title, first message (body), status, URL, and created date.
No replies or comments are stored.
"""

import logging
from typing import Any

import httpx

from src.api.config import get_settings
from src.knowledge.db import get_connection, get_last_sync, update_sync_metadata, upsert_github_item

logger = logging.getLogger(__name__)


def _github_request(
    endpoint: str, params: dict[str, Any] | None = None, timeout: int = 30
) -> list[dict[str, Any]]:
    """Make GitHub REST API request.

    Args:
        endpoint: API endpoint (e.g., '/repos/owner/repo/issues')
        params: Query parameters
        timeout: Request timeout in seconds

    Returns:
        List of items from API response

    Raises:
        httpx.HTTPStatusError: If request fails

    Note:
        Works without authentication for public repos (60 req/hour).
        Optional GITHUB_TOKEN env var enables higher rate limits (5000 req/hour).
    """
    settings = get_settings()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Optional token for higher rate limits
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
        logger.debug("Using GitHub token for authentication")

    url = f"https://api.github.com{endpoint}"
    logger.debug("GET %s with params %s", url, params)

    all_items = []
    page = 1
    per_page = 100

    # Handle pagination
    while True:
        page_params = {**(params or {}), "page": page, "per_page": per_page}

        response = httpx.get(url, headers=headers, params=page_params, timeout=timeout)
        response.raise_for_status()

        items = response.json()
        if not items:
            break

        all_items.extend(items)

        # Check if there are more pages
        if len(items) < per_page:
            break

        page += 1

    logger.debug("Fetched %d items from %s", len(all_items), endpoint)
    return all_items


def sync_repo_issues(repo: str, project: str = "hed", since: str | None = None) -> int:
    """Sync issues from a repository using GitHub REST API.

    Args:
        repo: Repository in owner/name format
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        since: Optional ISO date to sync from (for incremental sync)

    Returns:
        Number of items synced
    """
    try:
        items = _github_request(
            f"/repos/{repo}/issues",
            params={"state": "all", "filter": "all"},
        )
    except httpx.HTTPError as e:
        logger.warning("GitHub API error for %s: %s", repo, e)
        return 0

    count = 0
    with get_connection(project) as conn:
        for item in items:
            # Skip pull requests (they appear in issues endpoint too)
            if "pull_request" in item:
                continue

            # Skip if before since date (for incremental sync)
            if since and item.get("created_at", "") < since:
                continue

            upsert_github_item(
                conn,
                repo=repo,
                item_type="issue",
                number=item["number"],
                title=item["title"],
                first_message=item.get("body"),
                status="open" if item.get("state") == "open" else "closed",
                url=item["html_url"],
                created_at=item["created_at"],
            )
            count += 1
        conn.commit()

    logger.info("Synced %d issues from %s to %s.db", count, repo, project)
    return count


def sync_repo_prs(repo: str, project: str = "hed", since: str | None = None) -> int:
    """Sync PRs from a repository using GitHub REST API.

    Args:
        repo: Repository in owner/name format
        project: Assistant/project name for database isolation. Defaults to 'hed'.
        since: Optional ISO date to sync from (for incremental sync)

    Returns:
        Number of items synced
    """
    try:
        items = _github_request(
            f"/repos/{repo}/pulls",
            params={"state": "all"},
        )
    except httpx.HTTPError as e:
        logger.warning("GitHub API error for %s: %s", repo, e)
        return 0

    count = 0
    with get_connection(project) as conn:
        for item in items:
            # Skip if before since date (for incremental sync)
            if since and item.get("created_at", "") < since:
                continue

            # Map PR state (open, closed) to simple status
            # Note: GitHub API doesn't distinguish merged in state field
            status = "open" if item.get("state") == "open" else "closed"

            upsert_github_item(
                conn,
                repo=repo,
                item_type="pr",
                number=item["number"],
                title=item["title"],
                first_message=item.get("body"),
                status=status,
                url=item["html_url"],
                created_at=item["created_at"],
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
