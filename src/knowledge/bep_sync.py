"""Sync BIDS Extension Proposals (BEPs) from bids-website and bids-specification.

Fetches BEP metadata from beps.yml on bids-standard/bids-website, then for BEPs
with open PRs, fetches the actual specification markdown from the PR branch.
"""

import json
import logging
import os
import re
from enum import StrEnum
from typing import TypedDict

import httpx
import yaml

from src.knowledge.db import get_connection, update_sync_metadata, upsert_bep_item

logger = logging.getLogger(__name__)

BEPS_YAML_URL = (
    "https://raw.githubusercontent.com/bids-standard/bids-website/main/data/beps/beps.yml"
)
SPEC_REPO = "bids-standard/bids-specification"
GITHUB_API_BASE = "https://api.github.com"


class BEPStatus(StrEnum):
    """Valid statuses for a BIDS Extension Proposal."""

    DRAFT = "draft"
    PROPOSED = "proposed"
    CLOSED = "closed"


class SyncStats(TypedDict):
    """Statistics from a BEP sync run."""

    total: int
    with_content: int
    skipped: int


def _get_github_headers() -> dict[str, str]:
    """Build headers for GitHub API requests, including auth token if available."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "OSA-BEPSync/1.0",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _extract_pr_number(pr_url: str) -> int | None:
    """Extract PR number from a GitHub PR URL.

    Args:
        pr_url: URL like https://github.com/bids-standard/bids-specification/pull/1705

    Returns:
        PR number as int, or None if URL doesn't match expected pattern.
    """
    match = re.search(r"/pull/(\d+)$", pr_url)
    return int(match.group(1)) if match else None


def _format_leads(leads: list[dict] | None) -> str | None:
    """Format BEP leads into a JSON string of names.

    Args:
        leads: List of dicts with 'given-names' and 'family-names' keys.

    Returns:
        JSON string like '["Viviana Siless", "Chris Markiewicz"]' or None.
    """
    if not leads:
        return None

    names = []
    for lead in leads:
        given = (lead.get("given-names") or "").strip()
        family = (lead.get("family-names") or "").strip()
        if given or family:
            names.append(f"{given} {family}".strip())

    return json.dumps(names) if names else None


def _fetch_beps_yaml(client: httpx.Client) -> list[dict]:
    """Fetch and parse beps.yml from bids-website repo.

    Returns:
        List of BEP entries from the YAML file.

    Raises:
        httpx.HTTPError: On network errors.
        yaml.YAMLError: On YAML parsing errors.
        ValueError: If the YAML content is not a list.
    """
    response = client.get(BEPS_YAML_URL)
    response.raise_for_status()
    data = yaml.safe_load(response.text)
    if not isinstance(data, list):
        raise ValueError(f"Expected list from beps.yml, got {type(data).__name__}")
    return data or []


def _check_pr_open(client: httpx.Client, pr_number: int) -> dict | None:
    """Check if a PR is open and return its metadata.

    Args:
        client: HTTP client with GitHub headers.
        pr_number: PR number on bids-specification.

    Returns:
        PR metadata dict if open, None if closed/merged or not found.

    Raises:
        httpx.HTTPError: On network or API errors other than 404.
    """
    url = f"{GITHUB_API_BASE}/repos/{SPEC_REPO}/pulls/{pr_number}"
    response = client.get(url)
    if response.status_code == 404:
        logger.info("PR #%d not found (may have been deleted)", pr_number)
        return None
    response.raise_for_status()
    pr_data = response.json()
    if pr_data.get("state") == "open":
        return pr_data
    return None


def _fetch_pr_markdown(
    client: httpx.Client, pr_number: int, branch: str, fork_repo: str
) -> str | None:
    """Fetch markdown spec files changed in a PR.

    Lists files changed in the PR, filters for .md files under src/,
    and fetches their content from the PR head branch. Handles pagination
    and logs per-file fetch failures; HTTP errors during file listing
    cause early termination of the listing loop.

    Args:
        client: HTTP client with GitHub headers.
        pr_number: PR number on bids-specification.
        branch: Branch name of the PR head.
        fork_repo: Full repo name of the PR head (e.g., "user/bids-specification").

    Returns:
        Concatenated markdown content, or None if no .md files found.
    """
    # List files changed in the PR (paginated)
    files_url = f"{GITHUB_API_BASE}/repos/{SPEC_REPO}/pulls/{pr_number}/files"
    md_files: list[str] = []
    page = 1

    while True:
        try:
            response = client.get(files_url, params={"per_page": 100, "page": page})
            response.raise_for_status()
            files = response.json()
            if not files:
                break

            for f in files:
                filename = f.get("filename", "")
                if filename.startswith("src/") and filename.endswith(".md"):
                    md_files.append(filename)

            page += 1
        except (httpx.HTTPError, json.JSONDecodeError):
            logger.warning("Failed to list PR #%d files (page %d)", pr_number, page, exc_info=True)
            break

    if not md_files:
        logger.info("No .md files found in PR #%d", pr_number)
        return None

    # Fetch each markdown file from the PR head branch (on the fork repo)
    contents = []
    failed_files = 0
    for filepath in md_files:
        raw_url = f"https://raw.githubusercontent.com/{fork_repo}/{branch}/{filepath}"
        try:
            response = client.get(raw_url)
            response.raise_for_status()
            contents.append(f"<!-- File: {filepath} -->\n{response.text}")
        except httpx.HTTPError:
            failed_files += 1
            logger.warning(
                "Failed to fetch %s from %s:%s", filepath, fork_repo, branch, exc_info=True
            )

    if failed_files:
        logger.warning(
            "PR #%d: fetched %d/%d files (%d failed)",
            pr_number,
            len(contents),
            len(md_files),
            failed_files,
        )

    return "\n\n---\n\n".join(contents) if contents else None


def _resolve_pr_status(
    client: httpx.Client, pr_number: int, bep_number: str
) -> tuple[BEPStatus, str | None]:
    """Check PR state and fetch spec content if open.

    Args:
        client: HTTP client with GitHub headers.
        pr_number: PR number on bids-specification.
        bep_number: BEP number (for logging).

    Returns:
        (status, content) where status is BEPStatus.PROPOSED or BEPStatus.CLOSED,
        and content is the markdown text or None.

    Raises:
        httpx.HTTPError: On network or API errors.
        json.JSONDecodeError: On malformed API responses.
    """
    pr_data = _check_pr_open(client, pr_number)
    if not pr_data:
        return BEPStatus.CLOSED, None

    branch = pr_data.get("head", {}).get("ref")
    fork_repo = pr_data.get("head", {}).get("repo", {}).get("full_name", SPEC_REPO)

    if not branch:
        logger.warning("PR #%d missing head ref, skipping content fetch", pr_number)
        return BEPStatus.PROPOSED, None

    logger.info(
        "Fetching content for BEP%s (PR #%d, branch: %s, repo: %s)",
        bep_number,
        pr_number,
        branch,
        fork_repo,
    )
    content = _fetch_pr_markdown(client, pr_number, branch, fork_repo)
    return BEPStatus.PROPOSED, content


def sync_beps(community_id: str = "bids") -> SyncStats:
    """Sync BEP metadata and spec content from GitHub.

    For each BEP in beps.yml:
    - Stores metadata (title, status, links, leads)
    - For BEPs with open PRs: fetches spec markdown from the PR branch
    - For BEPs with closed/merged PRs or no PR: stores metadata only

    Args:
        community_id: Community database to sync into (default: 'bids').

    Returns:
        SyncStats with total, with_content, skipped counts.

    Raises:
        httpx.HTTPError: If beps.yml cannot be fetched.
        yaml.YAMLError: If beps.yml cannot be parsed.
        ValueError: If beps.yml has unexpected format.
    """
    if not os.environ.get("GITHUB_TOKEN"):
        logger.warning(
            "GITHUB_TOKEN not set. BEP sync will use unauthenticated GitHub API "
            "(60 requests/hour limit). Set GITHUB_TOKEN for reliable sync."
        )

    headers = _get_github_headers()
    stats: SyncStats = {"total": 0, "with_content": 0, "skipped": 0}

    with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
        # Fetch BEP metadata (let errors propagate to caller)
        logger.info("Fetching BEP metadata from bids-website...")
        beps = _fetch_beps_yaml(client)

        logger.info("Found %d BEPs in beps.yml", len(beps))

        with get_connection(community_id) as conn:
            for bep in beps:
                bep_number = str(bep.get("number", "")).strip()
                title = bep.get("title", "").strip()

                if not bep_number or not title:
                    logger.warning("Skipping BEP with missing number or title: %s", bep)
                    stats["skipped"] += 1
                    continue

                # BEP numbers are zero-padded (e.g., "032") for consistent DB keys
                bep_number = bep_number.zfill(3)

                pr_url = bep.get("pull_request")
                html_preview = bep.get("html_preview")
                google_doc = bep.get("google_doc")
                leads = _format_leads(bep.get("leads"))

                pr_number = _extract_pr_number(pr_url) if pr_url else None
                content = None
                # Initial default; updated to 'proposed' or 'closed' after PR check
                status: BEPStatus = BEPStatus.DRAFT

                if pr_number:
                    try:
                        status, content = _resolve_pr_status(client, pr_number, bep_number)
                        if content:
                            stats["with_content"] += 1
                    except (httpx.HTTPError, json.JSONDecodeError):
                        logger.warning(
                            "Failed to check PR #%d for BEP%s, storing metadata only",
                            pr_number,
                            bep_number,
                            exc_info=True,
                        )

                upsert_bep_item(
                    conn,
                    bep_number=bep_number,
                    title=title,
                    status=status,
                    pull_request_url=pr_url,
                    pull_request_number=pr_number,
                    html_preview_url=html_preview,
                    google_doc_url=google_doc,
                    leads=leads,
                    content=content,
                )
                stats["total"] += 1

            conn.commit()

    update_sync_metadata("beps", "bids-website", stats["total"], community_id)
    logger.info(
        "BEP sync complete: %d total, %d with content, %d skipped",
        stats["total"],
        stats["with_content"],
        stats["skipped"],
    )
    return stats
