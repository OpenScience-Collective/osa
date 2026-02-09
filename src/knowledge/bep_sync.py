"""Sync BIDS Extension Proposals (BEPs) from bids-website and bids-specification.

Fetches BEP metadata from beps.yml on bids-standard/bids-website, then for BEPs
with open PRs, fetches the actual specification markdown from the PR branch.
"""

import json
import logging
import os
import re

import httpx
import yaml

from src.knowledge.db import get_connection, update_sync_metadata, upsert_bep_item

logger = logging.getLogger(__name__)

BEPS_YAML_URL = (
    "https://raw.githubusercontent.com/bids-standard/bids-website/main/data/beps/beps.yml"
)
SPEC_REPO = "bids-standard/bids-specification"
GITHUB_API_BASE = "https://api.github.com"


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
    """
    response = client.get(BEPS_YAML_URL)
    response.raise_for_status()
    return yaml.safe_load(response.text) or []


def _check_pr_open(client: httpx.Client, pr_number: int) -> dict | None:
    """Check if a PR is open and return its metadata.

    Args:
        client: HTTP client with GitHub headers.
        pr_number: PR number on bids-specification.

    Returns:
        PR metadata dict if open, None if closed/merged or on error.
    """
    url = f"{GITHUB_API_BASE}/repos/{SPEC_REPO}/pulls/{pr_number}"
    try:
        response = client.get(url)
        response.raise_for_status()
        pr_data = response.json()
        if pr_data.get("state") == "open":
            return pr_data
        return None
    except httpx.HTTPError:
        logger.warning("Failed to check PR #%d status", pr_number, exc_info=True)
        return None


def _fetch_pr_markdown(client: httpx.Client, pr_number: int, branch: str) -> str | None:
    """Fetch markdown spec files changed in a PR.

    Lists files changed in the PR, filters for .md files under src/,
    and fetches their content from the PR branch.

    Args:
        client: HTTP client with GitHub headers.
        pr_number: PR number on bids-specification.
        branch: Branch name of the PR head.

    Returns:
        Concatenated markdown content, or None if no .md files found.
    """
    # List files changed in the PR (paginated)
    files_url = f"{GITHUB_API_BASE}/repos/{SPEC_REPO}/pulls/{pr_number}/files"
    md_files = []
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
                # Only include .md files under src/ (spec content, not CI/docs metadata)
                if filename.startswith("src/") and filename.endswith(".md"):
                    md_files.append(filename)

            page += 1
        except httpx.HTTPError:
            logger.warning("Failed to list PR #%d files (page %d)", pr_number, page, exc_info=True)
            break

    if not md_files:
        logger.info("No .md files found in PR #%d", pr_number)
        return None

    # Fetch each markdown file from the PR branch
    contents = []
    for filepath in md_files:
        raw_url = f"https://raw.githubusercontent.com/{SPEC_REPO}/{branch}/{filepath}"
        try:
            response = client.get(raw_url)
            response.raise_for_status()
            contents.append(f"<!-- File: {filepath} -->\n{response.text}")
        except httpx.HTTPError:
            logger.warning("Failed to fetch %s from branch %s", filepath, branch)

    return "\n\n---\n\n".join(contents) if contents else None


def sync_beps(community_id: str = "bids") -> dict[str, int]:
    """Sync BEP metadata and spec content from GitHub.

    For each BEP in beps.yml:
    - Stores metadata (title, status, links, leads)
    - For BEPs with open PRs: fetches spec markdown from the PR branch
    - For BEPs with closed/merged PRs or no PR: stores metadata only

    Args:
        community_id: Community database to sync into (default: 'bids').

    Returns:
        Dict with sync statistics: total, with_content, skipped.
    """
    headers = _get_github_headers()
    stats = {"total": 0, "with_content": 0, "skipped": 0}

    with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
        # Fetch BEP metadata
        logger.info("Fetching BEP metadata from bids-website...")
        try:
            beps = _fetch_beps_yaml(client)
        except httpx.HTTPError:
            logger.error("Failed to fetch beps.yml", exc_info=True)
            return stats

        logger.info("Found %d BEPs in beps.yml", len(beps))

        with get_connection(community_id) as conn:
            for bep in beps:
                bep_number = str(bep.get("number", "")).strip()
                title = bep.get("title", "").strip()

                if not bep_number or not title:
                    logger.warning("Skipping BEP with missing number or title: %s", bep)
                    stats["skipped"] += 1
                    continue

                # Pad to 3 digits for consistency
                bep_number = bep_number.zfill(3)

                pr_url = bep.get("pull_request")
                html_preview = bep.get("html_preview")
                google_doc = bep.get("google_doc")
                leads = _format_leads(bep.get("leads"))

                pr_number = _extract_pr_number(pr_url) if pr_url else None
                content = None
                status = "draft"  # default: Google Doc only

                if pr_number:
                    # Check if PR is open
                    pr_data = _check_pr_open(client, pr_number)
                    if pr_data:
                        status = "proposed"
                        branch = pr_data["head"]["ref"]
                        logger.info(
                            "Fetching content for BEP%s (PR #%d, branch: %s)",
                            bep_number,
                            pr_number,
                            branch,
                        )
                        content = _fetch_pr_markdown(client, pr_number, branch)
                        if content:
                            stats["with_content"] += 1
                    else:
                        # PR exists but is closed/merged
                        status = "closed"

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
