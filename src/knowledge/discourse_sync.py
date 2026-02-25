"""Discourse forum topic sync.

Syncs topics from Discourse forums using the public JSON API.
Designed to be generic and work with any Discourse instance.

Features:
- Public API (no auth needed for read access)
- Incremental sync (only new/updated topics since last sync)
- Category filtering
- Patient rate limiting (1 request per second by default)
- Stores topics in knowledge DB for FTS search
"""

import logging
import time

import httpx
import markdownify
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.knowledge.db import get_connection, update_sync_metadata, upsert_discourse_topic

logger = logging.getLogger(__name__)
console = Console()

# Default delay between API requests (seconds).
# Discourse allows 200 req/min per IP, but we are generous and patient.
DEFAULT_REQUEST_DELAY = 1.0


def _html_to_text(html: str) -> str:
    """Convert Discourse post HTML to plain markdown text."""
    if not html:
        return ""
    md = markdownify.markdownify(html, heading_style="ATX", strip=["script", "style"])
    # Collapse excessive whitespace
    lines = [line.rstrip() for line in md.split("\n")]
    cleaned = []
    blank_count = 0
    for line in lines:
        if not line.strip():
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _fetch_json(
    url: str,
    *,
    timeout: float = 30.0,
    delay: float = DEFAULT_REQUEST_DELAY,
    max_retries: int = 3,
) -> dict | None:
    """Fetch JSON from a URL with rate limiting and retry on 429.

    Args:
        url: URL to fetch
        timeout: HTTP timeout in seconds
        delay: Delay after the request completes (rate limiting)
        max_retries: Max retries on 429 Too Many Requests

    Returns:
        Parsed JSON dict, or None on error
    """
    for attempt in range(max_retries):
        try:
            response = httpx.get(
                url,
                timeout=timeout,
                follow_redirects=True,
                headers={"Accept": "application/json"},
            )
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 10))
                logger.warning(
                    "Rate limited (429), waiting %ds (attempt %d)", retry_after, attempt + 1
                )
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            time.sleep(delay)
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("HTTP %d fetching %s: %s", e.response.status_code, url, e)
            return None
        except httpx.TimeoutException:
            logger.error("Timeout fetching %s", url)
            return None
        except httpx.RequestError as e:
            logger.error("Request error fetching %s: %s", url, e)
            return None

    logger.error("Max retries exceeded for %s", url)
    return None


def _get_accepted_answer(posts: list[dict]) -> str | None:
    """Extract the accepted answer from a list of posts.

    Discourse marks accepted answers with 'accepted_answer' field.
    Falls back to the highest-scoring reply if no accepted answer.
    """
    # Look for the accepted answer
    for post in posts:
        if post.get("accepted_answer"):
            return _html_to_text(post.get("cooked", ""))

    # Fall back to the reply with the most likes (skip OP which is post_number=1)
    replies = [p for p in posts if p.get("post_number", 0) > 1]
    if replies:
        best = max(replies, key=lambda p: p.get("like_count", 0))
        if best.get("like_count", 0) > 0:
            return _html_to_text(best.get("cooked", ""))

    return None


def sync_discourse_topics(
    base_url: str,
    project: str,
    categories: list[dict] | None = None,
    incremental: bool = True,
    max_topics: int | None = None,
    request_delay: float = DEFAULT_REQUEST_DELAY,
) -> int:
    """Sync topics from a Discourse forum.

    Fetches topic listings and individual topic details from the Discourse
    public JSON API. Stores topics with their first post and best answer
    in the knowledge database.

    Args:
        base_url: Base URL of the Discourse instance (e.g., 'https://mne.discourse.group')
        project: Community ID for database isolation
        categories: Optional list of category dicts with 'slug' and 'id' keys.
                    If None, syncs from /latest.json (all categories).
        incremental: If True, only sync topics updated since last sync
        max_topics: Maximum number of topics to sync (for testing). None for all.
        request_delay: Seconds between API requests (default: 1.0s, patient)

    Returns:
        Number of topics synced
    """
    base_url = base_url.rstrip("/")
    console.print(f"Syncing Discourse topics from {base_url}...")

    # Get last sync time for incremental sync
    last_sync = None
    if incremental:
        from src.knowledge.db import get_last_sync

        last_sync = get_last_sync("discourse", base_url, project)
        if last_sync:
            console.print(f"Incremental sync since {last_sync}")
        else:
            console.print("No previous sync found, doing full sync")

    # Collect topic IDs to sync
    topic_ids = _collect_topic_ids(
        base_url,
        categories=categories,
        last_sync=last_sync,
        max_topics=max_topics,
        request_delay=request_delay,
    )

    if not topic_ids:
        console.print("[yellow]No new topics to sync[/yellow]")
        update_sync_metadata("discourse", base_url, 0, project)
        return 0

    console.print(f"Found {len(topic_ids)} topics to sync")

    # Fetch and store each topic
    total_synced = 0
    failed = 0
    uncommitted = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Syncing topics...", total=len(topic_ids))

        with get_connection(project) as conn:
            for topic_id in topic_ids:
                topic_url = f"{base_url}/t/{topic_id}.json"
                data = _fetch_json(topic_url, delay=request_delay)

                if data is None:
                    failed += 1
                    progress.update(task, advance=1)
                    continue

                posts = data.get("post_stream", {}).get("posts", [])
                first_post_html = posts[0].get("cooked", "") if posts else ""
                first_post = _html_to_text(first_post_html)
                accepted_answer = _get_accepted_answer(posts) if len(posts) > 1 else None

                upsert_discourse_topic(
                    conn,
                    forum_url=base_url,
                    topic_id=data["id"],
                    title=data.get("title", ""),
                    first_post=first_post,
                    accepted_answer=accepted_answer,
                    category_name=data.get("category_name"),
                    tags=data.get("tags"),
                    reply_count=data.get("reply_count", 0),
                    like_count=data.get("like_count", 0),
                    views=data.get("views", 0),
                    url=f"{base_url}/t/{data.get('slug', '')}/{data['id']}",
                    created_at=data.get("created_at", ""),
                    last_posted_at=data.get("last_posted_at"),
                )
                total_synced += 1
                uncommitted += 1

                # Commit every 50 topics to avoid large transactions
                if uncommitted >= 50:
                    conn.commit()
                    uncommitted = 0

                progress.update(task, advance=1)

            # Final commit
            conn.commit()

    # Update sync metadata
    update_sync_metadata("discourse", base_url, total_synced, project)

    console.print(f"[green]Synced {total_synced} topics[/green]")
    if failed:
        console.print(f"[yellow]Failed to fetch {failed} topics[/yellow]")

    return total_synced


def _collect_topic_ids(
    base_url: str,
    *,
    categories: list[dict] | None = None,
    last_sync: str | None = None,
    max_topics: int | None = None,
    request_delay: float = DEFAULT_REQUEST_DELAY,
) -> list[int]:
    """Collect topic IDs to sync from topic listings.

    Pages through /latest.json or category-specific listings to find
    topics that need syncing.

    Args:
        base_url: Discourse base URL
        categories: Optional category filters
        last_sync: ISO timestamp of last sync (for incremental)
        max_topics: Maximum topics to collect
        request_delay: Delay between requests

    Returns:
        List of topic IDs to fetch
    """
    topic_ids: list[int] = []

    if categories:
        # Sync specific categories
        for cat in categories:
            slug = cat.get("slug", "")
            cat_id = cat.get("id", "")
            ids = _collect_from_listing(
                f"{base_url}/c/{slug}/{cat_id}.json",
                last_sync=last_sync,
                max_topics=max_topics - len(topic_ids) if max_topics else None,
                request_delay=request_delay,
            )
            topic_ids.extend(ids)
            if max_topics and len(topic_ids) >= max_topics:
                break
    else:
        # Sync all topics via latest
        topic_ids = _collect_from_listing(
            f"{base_url}/latest.json",
            last_sync=last_sync,
            max_topics=max_topics,
            request_delay=request_delay,
        )

    return topic_ids[:max_topics] if max_topics else topic_ids


def _collect_from_listing(
    url: str,
    *,
    last_sync: str | None = None,
    max_topics: int | None = None,
    request_delay: float = DEFAULT_REQUEST_DELAY,
) -> list[int]:
    """Page through a Discourse topic listing and collect topic IDs.

    Args:
        url: Listing URL (e.g., /latest.json or /c/slug/id.json)
        last_sync: Stop collecting when we hit topics older than this
        max_topics: Maximum topics to collect
        request_delay: Delay between requests

    Returns:
        List of topic IDs
    """
    topic_ids: list[int] = []
    page = 0
    max_pages = 200  # Safety limit

    while page < max_pages:
        page_url = f"{url}?page={page}" if page > 0 else url
        data = _fetch_json(page_url, delay=request_delay)

        if data is None:
            break

        topics = data.get("topic_list", {}).get("topics", [])
        if not topics:
            break

        hit_old_topics = False
        for topic in topics:
            # Skip pinned topics (they appear on every page)
            if topic.get("pinned"):
                continue

            topic_id = topic.get("id")
            if topic_id is None:
                continue

            # For incremental sync, stop at topics older than last_sync
            if last_sync:
                last_activity = topic.get("last_posted_at") or topic.get("created_at", "")
                if last_activity and last_activity < last_sync:
                    hit_old_topics = True
                    break

            topic_ids.append(topic_id)

            if max_topics and len(topic_ids) >= max_topics:
                return topic_ids

        if hit_old_topics:
            break

        # Check if there are more pages
        more_url = data.get("topic_list", {}).get("more_topics_url")
        if not more_url:
            break

        page += 1

    return topic_ids
