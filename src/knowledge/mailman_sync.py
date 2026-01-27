"""Mailman pipermail archive scraper.

Scrapes Mailman pipermail HTML archives to extract thread messages.
Designed to be generic and work with any Mailman mailing list.

Features:
- HTML parsing with caching and rate limiting
- Thread structure preservation
- Progress tracking with Rich
- Graceful error handling
"""

import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from markdownify import markdownify
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.knowledge.db import get_connection, upsert_mailing_list_message

logger = logging.getLogger(__name__)
console = Console()

# Rate limiting (be respectful to servers)
MAILMAN_DELAY = 1.0  # seconds between requests

# Caching
CACHE_DIR = Path.home() / ".cache" / "osa" / "mailman"
CACHE_TTL = 7 * 24 * 3600  # 7 days


@dataclass
class MessageInfo:
    """Parsed message metadata."""

    message_id: str
    subject: str
    author: str | None
    author_email: str | None
    date: str
    body: str | None
    in_reply_to: str | None
    url: str


def _fetch_page(url: str, cache_key: str | None = None) -> str | None:
    """Fetch HTML page with caching and rate limiting.

    Args:
        url: URL to fetch
        cache_key: Optional cache key for local storage

    Returns:
        HTML content or None if error
    """
    # Check cache
    if cache_key:
        cache_file = CACHE_DIR / f"{cache_key}.html"
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < CACHE_TTL:
                logger.debug("Cache hit for %s", cache_key)
                return cache_file.read_text()

    # Fetch from network
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": "OSA-MailmanSync/1.0 (+https://github.com/hed-standard/osa)"},
            timeout=30.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        content = response.text

        # Cache result
        if cache_key:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(content)
            logger.debug("Cached %s", cache_key)

        # Rate limit
        time.sleep(MAILMAN_DELAY)

        return content

    except httpx.HTTPStatusError as e:
        logger.error("HTTP %d fetching %s", e.response.status_code, url)
        return None
    except httpx.TimeoutException:
        logger.error("Timeout fetching %s", url)
        return None
    except httpx.RequestError as e:
        logger.error("Request error fetching %s: %s", url, e)
        return None


def _parse_year_index(html: str) -> list[int]:
    """Extract available years from index page.

    Args:
        html: HTML content of index page

    Returns:
        Sorted list of available years
    """
    # Look for links like: <a href="2024/">2024</a>
    pattern = r'<a href="(\d{4})/">(\d{4})</a>'
    matches = re.findall(pattern, html)
    years = sorted({int(year) for _, year in matches})
    return years


def _normalize_subject(subject: str) -> str:
    """Normalize email subject for thread matching.

    Strips prefixes like Re:, Fwd:, [list-name], etc. to find thread root subject.

    Args:
        subject: Original email subject line

    Returns:
        Normalized subject for thread matching
    """
    # Remove list name prefixes like [EEGLAB] or [hed-dev]
    subject = re.sub(r"^\[[\w-]+\]\s*", "", subject, flags=re.IGNORECASE)

    # Remove reply/forward prefixes (Re:, RE:, Fwd:, FW:, etc.)
    # Handle multiple levels: Re: Re: Re:
    while True:
        old_subject = subject
        subject = re.sub(r"^(Re|Fwd?|AW|WG):\s*", "", subject, flags=re.IGNORECASE).strip()
        if subject == old_subject:
            break

    # Normalize whitespace
    subject = " ".join(subject.split())

    return subject.lower()


def _parse_thread_index(html: str, base_url: str, year: int) -> list[tuple[str, str, str]]:
    """Parse thread.html to extract message URLs and subjects.

    Args:
        html: HTML content of thread index
        base_url: Base URL to pipermail
        year: Year being processed

    Returns:
        List of (message_url, message_id, subject) tuples
    """
    # Pipermail thread.html uses <LI> with indentation for threading
    # Pattern: <LI><A HREF="017633.html">Subject</A>
    pattern = r'<LI><A HREF="(\d+\.html)">([^<]+)</A>'
    matches = re.findall(pattern, html)

    results = []
    for msg_file, subject in matches:
        message_id = msg_file.replace(".html", "")
        message_url = f"{base_url}{year}/{msg_file}"
        results.append((message_url, message_id, subject.strip()))

    return results


def _parse_message_page(html: str, url: str) -> MessageInfo | None:
    """Parse individual message HTML page.

    Args:
        html: HTML content of message page
        url: URL to message

    Returns:
        MessageInfo or None if parsing failed
    """
    try:
        # Extract subject (from <title> or <H1>)
        subject_match = re.search(r"<TITLE>([^<]+)</TITLE>", html, re.IGNORECASE)
        subject = subject_match.group(1).strip() if subject_match else "No subject"

        # Extract author and email
        # Pattern: <B>Author Name</B> <a href="mailto:email@domain.com">
        author_match = re.search(r"<B>([^<]+)</B>", html)
        author = author_match.group(1).strip() if author_match else None

        email_match = re.search(r'href="mailto:([^"]+)"', html)
        author_email = email_match.group(1).strip() if email_match else None

        # Extract date
        # Pattern varies, but typically after author: Mon Jan 27 12:34:56 PST 2026
        date_match = re.search(r"<I>([^<]+)</I>", html)
        date_str = date_match.group(1).strip() if date_match else ""

        # Extract body (content between <PRE> tags, or main text)
        body_match = re.search(r"<PRE>(.*?)</PRE>", html, re.DOTALL | re.IGNORECASE)
        if body_match:
            body_html = body_match.group(1)
            # Convert to markdown
            body = markdownify(body_html, heading_style="ATX", strip=["script", "style"])
        else:
            body = None

        # Extract In-Reply-To (from headers if present)
        # This might require parsing the full email headers section
        in_reply_to = None  # TODO: Parse from headers if available

        # Message ID from URL
        message_id = url.split("/")[-1].replace(".html", "")

        return MessageInfo(
            message_id=message_id,
            subject=subject,
            author=author,
            author_email=author_email,
            date=date_str,
            body=body,
            in_reply_to=in_reply_to,
            url=url,
        )

    except (AttributeError, ValueError, UnicodeDecodeError) as e:
        # Expected errors from malformed HTML or encoding issues
        logger.warning(
            "Failed to parse message page %s: %s. This may indicate unexpected HTML structure.",
            url,
            e,
            extra={"url": url, "error_type": type(e).__name__},
        )
        return None
    except Exception as e:
        # Unexpected error - likely a programming bug
        logger.error(
            "Unexpected error parsing message page %s: %s. This is likely a bug.",
            url,
            e,
            exc_info=True,
            extra={"url": url, "error_type": type(e).__name__},
        )
        raise


def sync_mailing_list_year(
    list_name: str,
    base_url: str,
    year: int,
    project: str = "eeglab",
) -> int:
    """Sync messages from a single year.

    Args:
        list_name: Mailing list identifier (e.g., 'eeglablist')
        base_url: Base URL to pipermail (with trailing slash)
        year: Year to sync
        project: Community ID for database isolation

    Returns:
        Number of messages synced
    """
    console.print(f"Syncing {list_name} year {year}...")

    # Fetch thread index
    thread_url = f"{base_url}{year}/thread.html"
    thread_html = _fetch_page(thread_url, cache_key=f"{list_name}_{year}_thread")
    if not thread_html:
        logger.error("Failed to fetch thread index for year %d", year)
        return 0

    # Parse message list
    messages = _parse_thread_index(thread_html, base_url, year)
    console.print(f"Found {len(messages)} messages in {year}")

    if not messages:
        return 0

    # Build thread mapping based on normalized subjects
    # Key: normalized_subject -> first message_id in that thread
    thread_mapping: dict[str, str] = {}
    for _message_url, message_id, subject in messages:
        normalized = _normalize_subject(subject)
        if normalized not in thread_mapping:
            # First message with this subject becomes the thread root
            thread_mapping[normalized] = message_id

    logger.debug(
        "Found %d unique thread roots from %d messages",
        len(thread_mapping),
        len(messages),
    )

    # Fetch and parse each message
    count = 0
    failed = 0

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task(f"Processing {year}...", total=len(messages))

        with get_connection(project) as conn:
            for message_url, message_id, subject in messages:
                try:
                    # Fetch message page
                    cache_key = f"{list_name}_{message_id}"
                    msg_html = _fetch_page(message_url, cache_key=cache_key)
                    if not msg_html:
                        failed += 1
                        progress.update(task, advance=1)
                        continue

                    # Parse message
                    msg_info = _parse_message_page(msg_html, message_url)
                    if not msg_info:
                        failed += 1
                        progress.update(task, advance=1)
                        continue

                    # Determine thread_id from normalized subject
                    normalized_subject = _normalize_subject(subject)
                    thread_id = thread_mapping.get(normalized_subject, message_id)

                    # Upsert to database
                    try:
                        upsert_mailing_list_message(
                            conn,
                            list_name=list_name,
                            message_id=msg_info.message_id,
                            thread_id=thread_id,
                            subject=msg_info.subject,
                            author=msg_info.author,
                            author_email=msg_info.author_email,
                            date=msg_info.date,
                            body=msg_info.body,
                            in_reply_to=msg_info.in_reply_to,
                            url=msg_info.url,
                            year=year,
                        )
                        count += 1

                        # Commit every 50 messages
                        if count % 50 == 0:
                            try:
                                conn.commit()
                                logger.debug("Committed batch of 50 messages")
                            except sqlite3.Error as commit_err:
                                logger.error(
                                    "Failed to commit batch at message %d: %s. Last 50 messages may be lost.",
                                    count,
                                    commit_err,
                                    exc_info=True,
                                    extra={"batch_size": 50, "total_processed": count},
                                )
                                # Re-raise database errors as they indicate serious problems
                                raise

                    except sqlite3.IntegrityError as db_err:
                        # Constraint violation - likely duplicate message
                        logger.warning(
                            "Database constraint violation for message %s: %s. Skipping.",
                            message_id,
                            db_err,
                            extra={"message_id": message_id, "url": message_url},
                        )
                        failed += 1
                    except sqlite3.OperationalError as db_err:
                        # Database locked, disk full, or other operational issue
                        logger.error(
                            "Database operational error for message %s: %s. This may require intervention.",
                            message_id,
                            db_err,
                            exc_info=True,
                            extra={"message_id": message_id, "url": message_url},
                        )
                        # Re-raise to abort sync - these are serious problems
                        raise

                except Exception as e:
                    # Unexpected error - likely a programming bug
                    logger.error(
                        "Unexpected error processing message %s: %s",
                        message_url,
                        e,
                        exc_info=True,
                        extra={"message_id": message_id, "url": message_url},
                    )
                    failed += 1
                    # Continue processing other messages despite unexpected errors

                progress.update(task, advance=1)

            # Final commit
            conn.commit()

    console.print(f"[green]✓ Synced {count} messages from {year}[/green]")
    if failed > 0:
        console.print(f"[yellow]⚠ Failed to process {failed} messages[/yellow]")

    return count


def sync_mailing_list(
    list_name: str,
    base_url: str,
    project: str = "eeglab",
    start_year: int | None = None,
    end_year: int | None = None,
) -> dict[int, int]:
    """Sync mailing list messages from pipermail archives.

    Args:
        list_name: Mailing list identifier (e.g., 'eeglablist')
        base_url: Base URL to pipermail (e.g., 'https://sccn.ucsd.edu/pipermail/eeglablist/')
        project: Community ID for database isolation
        start_year: Earliest year to sync (default: all available)
        end_year: Latest year to sync (default: all available)

    Returns:
        Dict mapping year -> message count
    """
    console.print(f"[bold]Syncing {list_name} from {base_url}[/bold]")

    # Ensure base_url ends with /
    if not base_url.endswith("/"):
        base_url += "/"

    # Fetch year index
    index_html = _fetch_page(base_url, cache_key=f"{list_name}_index")
    if not index_html:
        console.print("[red]Error: Failed to fetch mailing list index[/red]")
        return {}

    # Parse available years
    years = _parse_year_index(index_html)
    if not years:
        console.print("[yellow]Warning: No years found in index[/yellow]")
        return {}

    # Filter years
    if start_year:
        years = [y for y in years if y >= start_year]
    if end_year:
        years = [y for y in years if y <= end_year]

    if not years:
        console.print("[yellow]Warning: No years in specified range[/yellow]")
        return {}

    console.print(f"Found {len(years)} years to sync: {min(years)}-{max(years)}")

    # Sync each year
    results = {}
    for year in years:
        count = sync_mailing_list_year(list_name, base_url, year, project)
        results[year] = count

    total = sum(results.values())
    console.print(f"\n[green]✓ Total: {total} messages synced from {len(years)} years[/green]")

    return results
