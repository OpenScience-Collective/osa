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
    """Make GitHub REST API request with pagination.

    Args:
        endpoint: API endpoint (e.g., '/repos/owner/repo/issues')
        params: Query parameters
        timeout: Request timeout in seconds

    Returns:
        List of items from API response

    Raises:
        httpx.HTTPStatusError: If HTTP request fails (4xx/5xx status)
        httpx.TimeoutException: If request times out
        httpx.NetworkError: If network connectivity fails
        ValueError: If response is not valid JSON

    Note:
        Works without authentication for public repos (60 req/hour).
        Optional GITHUB_TOKEN env var enables higher rate limits (5000 req/hour).
    """
    import json

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

        try:
            response = httpx.get(url, headers=headers, params=page_params, timeout=timeout)
            response.raise_for_status()

            # Log rate limit info
            if "X-RateLimit-Remaining" in response.headers:
                remaining = response.headers.get("X-RateLimit-Remaining")
                limit = response.headers.get("X-RateLimit-Limit")
                logger.debug("GitHub rate limit: %s/%s remaining", remaining, limit)

                if int(remaining) < 10:
                    logger.warning(
                        "GitHub rate limit low: %s/%s remaining. Consider adding GITHUB_TOKEN.",
                        remaining,
                        limit,
                    )

            # Parse JSON with error handling
            try:
                items = response.json()
            except json.JSONDecodeError as e:
                logger.error(
                    "GitHub API returned invalid JSON for %s (status %d): %s",
                    url,
                    response.status_code,
                    str(e),
                )
                raise ValueError(f"Invalid JSON response from GitHub API: {e}") from e

        except httpx.HTTPStatusError as e:
            # Provide detailed error context
            status = e.response.status_code
            if status == 403:
                logger.error(
                    "GitHub API rate limit or forbidden (HTTP %d) on page %d of %s. Response: %s",
                    status,
                    page,
                    endpoint,
                    e.response.text[:200],
                )
            elif status == 404:
                logger.error("Repository or endpoint not found (HTTP %d): %s", status, endpoint)
            elif status == 401:
                logger.error(
                    "GitHub API authentication failed (HTTP %d). Check GITHUB_TOKEN.", status
                )
            else:
                logger.error(
                    "GitHub API HTTP %d error on page %d of %s: %s",
                    status,
                    page,
                    endpoint,
                    e.response.text[:200],
                )
            raise

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
    import sqlite3

    # Validate repo format
    if "/" not in repo or repo.count("/") > 1:
        logger.error("Invalid repo format: %s. Expected 'owner/name'.", repo)
        return 0

    try:
        items = _github_request(
            f"/repos/{repo}/issues",
            params={"state": "all", "filter": "all"},
        )
    except httpx.TimeoutException as e:
        logger.error(
            "GitHub API timeout for %s: %s. Check network connectivity or increase timeout.",
            repo,
            e,
        )
        return 0
    except httpx.NetworkError as e:
        logger.error("Network error syncing %s: %s. Check internet connectivity.", repo, e)
        return 0
    except (httpx.HTTPStatusError, ValueError):
        # HTTPStatusError and JSON parsing errors already logged in _github_request
        return 0
    except httpx.RequestError as e:
        logger.error("GitHub API request failed for %s: %s", repo, e)
        return 0

    count = 0
    skipped = 0

    try:
        with get_connection(project) as conn:
            for item in items:
                try:
                    # Skip pull requests (they appear in issues endpoint too)
                    if "pull_request" in item:
                        continue

                    # Skip if before since date (for incremental sync)
                    if since and item.get("created_at", "") < since:
                        skipped += 1
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
                except KeyError as e:
                    logger.warning("Skipping issue due to missing field %s in %s", e, repo)
                    continue

            conn.commit()
    except sqlite3.OperationalError as e:
        logger.error("Database locked or I/O error for %s: %s", repo, e)
        return 0
    except sqlite3.Error as e:
        logger.error("Database error syncing %s: %s", repo, e)
        return 0

    if since and skipped > 0:
        logger.info(
            "Synced %d issues from %s to %s.db (skipped %d older than %s)",
            count,
            repo,
            project,
            skipped,
            since,
        )
    else:
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
    import sqlite3

    # Validate repo format
    if "/" not in repo or repo.count("/") > 1:
        logger.error("Invalid repo format: %s. Expected 'owner/name'.", repo)
        return 0

    try:
        items = _github_request(
            f"/repos/{repo}/pulls",
            params={"state": "all"},
        )
    except httpx.TimeoutException as e:
        logger.error(
            "GitHub API timeout for %s: %s. Check network connectivity or increase timeout.",
            repo,
            e,
        )
        return 0
    except httpx.NetworkError as e:
        logger.error("Network error syncing %s: %s. Check internet connectivity.", repo, e)
        return 0
    except (httpx.HTTPStatusError, ValueError):
        # HTTPStatusError and JSON parsing errors already logged in _github_request
        return 0
    except httpx.RequestError as e:
        logger.error("GitHub API request failed for %s: %s", repo, e)
        return 0

    count = 0
    skipped = 0

    try:
        with get_connection(project) as conn:
            for item in items:
                try:
                    # Skip if before since date (for incremental sync)
                    if since and item.get("created_at", "") < since:
                        skipped += 1
                        continue

                    # Map PR state (open, closed) to simple status
                    # Note: Merged status available via 'merged' field if needed in future
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
                except KeyError as e:
                    logger.warning("Skipping PR due to missing field %s in %s", e, repo)
                    continue

            conn.commit()
    except sqlite3.OperationalError as e:
        logger.error("Database locked or I/O error for %s: %s", repo, e)
        return 0
    except sqlite3.Error as e:
        logger.error("Database error syncing %s: %s", repo, e)
        return 0

    if since and skipped > 0:
        logger.info(
            "Synced %d PRs from %s to %s.db (skipped %d older than %s)",
            count,
            repo,
            project,
            skipped,
            since,
        )
    else:
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
    failed = []

    for repo in repos:
        try:
            count = sync_repo(repo, project, incremental)
            results[repo] = count
            if count == 0:
                logger.warning(
                    "No items synced from %s (could be no new items or sync error)", repo
                )
        except Exception as e:
            logger.error("Failed to sync %s: %s", repo, e)
            results[repo] = 0
            failed.append(repo)

    total = sum(results.values())
    if failed:
        logger.error(
            "Total items synced for %s: %d (%d repos failed: %s)",
            project,
            total,
            len(failed),
            failed,
        )
    else:
        logger.info(
            "Total items synced for %s: %d (all %d repos succeeded)", project, total, len(repos)
        )

    return results
