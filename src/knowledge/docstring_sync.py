"""Docstring sync from GitHub repositories.

Fetches source files from GitHub and extracts docstrings for indexing.
Supports MATLAB (.m) and Python (.py) files.
"""

import logging
from typing import Literal

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.api.config import get_settings
from src.knowledge.db import get_connection, update_sync_metadata, upsert_docstring
from src.knowledge.matlab_parser import parse_matlab_file
from src.knowledge.python_parser import parse_python_file

logger = logging.getLogger(__name__)
console = Console()

GITHUB_API_BASE = "https://api.github.com"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"


def sync_repo_docstrings(
    repo: str,
    language: Literal["matlab", "python"],
    project: str = "hed",
    branch: str = "main",
) -> int:
    """Sync docstrings from a GitHub repository.

    Args:
        repo: Repository in owner/name format (e.g., 'sccn/eeglab')
        language: 'matlab' or 'python'
        project: Community ID for database isolation
        branch: Git branch to sync

    Returns:
        Number of docstrings extracted

    Raises:
        httpx.HTTPStatusError: If GitHub API requests fail
    """
    console.print(f"Syncing {language} docstrings from {repo} ({branch})...")

    # Determine file extension
    extension = ".m" if language == "matlab" else ".py"

    # Get list of files from GitHub
    files = _get_repo_files(repo, branch, extension)
    console.print(f"Found {len(files)} {extension} files")

    if not files:
        console.print(f"[yellow]No {extension} files found in {repo}[/yellow]")
        return 0

    # Process files and extract docstrings
    total_docstrings = 0
    failed_files: list[tuple[str, str]] = []
    uncommitted = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Processing files...", total=len(files))

        with get_connection(project) as conn:
            for file_path in files:
                try:
                    # Fetch file content
                    content = _fetch_file_content(repo, branch, file_path)

                    # Parse docstrings
                    if language == "matlab":
                        docstrings = parse_matlab_file(content, file_path)
                    else:
                        docstrings = parse_python_file(content, file_path)

                    # Insert into database
                    for doc in docstrings:
                        upsert_docstring(
                            conn,
                            repo=repo,
                            file_path=file_path,
                            language=language,
                            symbol_name=doc.symbol_name,
                            symbol_type=doc.symbol_type,
                            docstring=doc.docstring,
                            line_number=doc.line_number,
                            branch=branch,
                        )
                        total_docstrings += 1
                        uncommitted += 1

                    # Commit every 50 docstrings to avoid large transactions
                    if uncommitted >= 50:
                        conn.commit()
                        uncommitted = 0

                except httpx.HTTPStatusError as e:
                    error_msg = f"HTTP {e.response.status_code}"
                    logger.error("HTTP error fetching %s: %s", file_path, e)
                    failed_files.append((file_path, error_msg))
                except httpx.TimeoutException:
                    logger.error("Timeout fetching %s", file_path)
                    failed_files.append((file_path, "Timeout"))
                except SyntaxError as e:
                    logger.error("Syntax error in %s: %s", file_path, e)
                    failed_files.append((file_path, f"Syntax error: {e}"))
                except UnicodeDecodeError as e:
                    logger.error("Encoding error in %s: %s", file_path, e)
                    failed_files.append((file_path, "Invalid encoding"))
                except Exception as e:
                    logger.error("Unexpected error processing %s: %s", file_path, e, exc_info=True)
                    failed_files.append((file_path, f"Error: {type(e).__name__}"))

                progress.update(task, advance=1)

            # Final commit
            conn.commit()

    # Update sync metadata
    update_sync_metadata("docstrings", f"{repo}:{language}", total_docstrings, project)

    # Report results
    console.print(f"[green]✓ Extracted {total_docstrings} docstrings[/green]")

    if failed_files:
        console.print(f"\n[yellow]Warning: Failed to process {len(failed_files)} files:[/yellow]")
        for path, error in failed_files[:10]:  # Show first 10
            console.print(f"  ✗ {path}: {error}")
        if len(failed_files) > 10:
            console.print(f"  ... and {len(failed_files) - 10} more")

    return total_docstrings


def _get_repo_files(repo: str, branch: str, extension: str) -> list[str]:
    """Get list of files with given extension from repository.

    Uses GitHub API with optional authentication for higher rate limits.

    Args:
        repo: Repository in owner/name format
        branch: Git branch
        extension: File extension (e.g., '.py' or '.m')

    Returns:
        List of file paths relative to repo root

    Raises:
        httpx.HTTPStatusError: If API request fails
        ValueError: If response format is unexpected
    """
    settings = get_settings()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Optional token for higher rate limits (60 req/hr -> 5000 req/hr)
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
        logger.debug("Using GitHub token for authentication")

    url = f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{branch}?recursive=1"

    try:
        response = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
        response.raise_for_status()
    except httpx.TimeoutException as e:
        logger.error("Timeout fetching file tree from %s", repo)
        raise TimeoutError(
            f"GitHub request timed out after 30 seconds. Repo: {repo}, branch: {branch}"
        ) from e

    try:
        tree = response.json()
    except ValueError as e:
        logger.error("Invalid JSON from GitHub API for %s: %s", repo, e)
        raise ValueError(f"GitHub returned invalid response for {repo}") from e

    if "tree" not in tree:
        logger.error("Unexpected GitHub response format for %s: missing 'tree' key", repo)
        raise ValueError(f"Unexpected response format from GitHub for {repo}")

    # Filter for files with the target extension
    files = [
        item["path"]
        for item in tree.get("tree", [])
        if item.get("type") == "blob" and item["path"].endswith(extension)
    ]

    return files


def _fetch_file_content(repo: str, branch: str, file_path: str) -> str:
    """Fetch raw file content from GitHub.

    Args:
        repo: Repository in owner/name format
        branch: Git branch
        file_path: File path relative to repo root

    Returns:
        File content as string

    Raises:
        httpx.HTTPStatusError: If request fails
        TimeoutError: If request times out after 30 seconds
    """
    url = f"{GITHUB_RAW_BASE}/{repo}/{branch}/{file_path}"

    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
    except httpx.TimeoutException as e:
        logger.error("Timeout fetching %s from %s", file_path, repo)
        raise TimeoutError(f"GitHub request timed out after 30 seconds. File: {file_path}") from e

    return response.text
