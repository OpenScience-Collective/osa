"""Docstring sync from GitHub repositories.

Fetches source files from GitHub and extracts docstrings for indexing.
Supports MATLAB (.m) and Python (.py) files.
"""

import logging
from typing import Literal

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

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
                        )
                        total_docstrings += 1

                    # Commit every 50 files for efficiency
                    if total_docstrings % 50 == 0:
                        conn.commit()

                except Exception as e:
                    logger.warning("Failed to process %s: %s", file_path, e)

                progress.update(task, advance=1)

            # Final commit
            conn.commit()

    # Update sync metadata
    update_sync_metadata("docstrings", f"{repo}:{language}", total_docstrings, project)

    console.print(f"[green]✓ Extracted {total_docstrings} docstrings[/green]")
    return total_docstrings


def _get_repo_files(repo: str, branch: str, extension: str) -> list[str]:
    """Get list of files with given extension from repository.

    Args:
        repo: Repository in owner/name format
        branch: Git branch
        extension: File extension (e.g., '.py' or '.m')

    Returns:
        List of file paths relative to repo root

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{branch}?recursive=1"
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()

    tree = response.json()

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
    """
    url = f"{GITHUB_RAW_BASE}/{repo}/{branch}/{file_path}"
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.text
