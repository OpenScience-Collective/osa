"""Discourse forum topic sync.

Syncs topics from Discourse forums using the public JSON API.
Designed to be generic and work with any Discourse instance.

Features:
- Public API (no auth needed for read access)
- Incremental sync (only new/updated topics since last sync)
- Category and tag filtering (a shared forum such as NeuroStars is limited to
  the topics a community tags)
- Best-answer selection that works on forums with and without the "solved"
  plugin (accepted answer, then most-liked reply, then a staff reply)
- Patient rate limiting (1 request per second by default)
- Stores topics in knowledge DB for FTS search
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
import markdownify
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

if TYPE_CHECKING:
    from src.core.config.community import DiscourseCategoryConfig

from src.knowledge.db import (
    get_connection,
    get_last_sync,
    update_sync_metadata,
    upsert_discourse_topic,
)

logger = logging.getLogger(__name__)
console = Console()

# Default delay between API requests (seconds).
# Discourse allows 200 req/min per IP, but we are generous and patient.
DEFAULT_REQUEST_DELAY = 1.0


def _html_to_markdown(html: str) -> str:
    """Convert Discourse post HTML to markdown."""
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


#: Discourse post_type for an ordinary post (others are moderator notes, small
#: actions such as "closed this topic", and whispers).
_REGULAR_POST_TYPE = 1

#: Discourse action type id for a like, as counted in a post's ``actions_summary``.
_LIKE_ACTION_ID = 2

#: Fewest characters of text, besides @mentions, that a staff reply needs to stand as a
#: topic's answer. Shorter replies are pings such as "FYI @plugin_author".
_MIN_STAFF_ANSWER_CHARS = 40

#: An @mention, as markdown converted from Discourse HTML ("[@name](/u/name)") or as text.
_MENTION = re.compile(r"\[@[^\]]*\]\([^)]*\)|@[\w.-]+")


def _like_count(post: dict) -> int:
    """Number of likes on a post.

    A topic's posts (``/t/{id}.json``) carry their likes in ``actions_summary``,
    not in a ``like_count`` field; ``like_count`` is read when present.
    """
    if post.get("like_count") is not None:
        return int(post["like_count"])
    for action in post.get("actions_summary") or []:
        if action.get("id") == _LIKE_ACTION_ID:
            return int(action.get("count") or 0)
    return 0


def _get_accepted_answer(posts: list[dict]) -> str | None:
    """Pick the post that best answers a topic, as markdown.

    In order of preference:

    1. The answer the asker accepted (forums with the "solved" plugin).
    2. The most-liked reply by someone other than the asker, when one has a like.
    3. The first reply by a staff member (an admin or moderator) other than
       the asker that says more than an @mention. Forums without the "solved"
       plugin, such as Brainstorm's, are answered by their development team,
       whose replies are seldom liked.
    4. The asker's own most-liked reply, when it has a like: they found the
       solution themselves. It comes last, since a liked reply from the asker
       is more often a "thanks, that fixed it".

    Only regular posts count as replies, not moderator notes or small actions
    such as "closed this topic".
    """
    for post in posts:
        if post.get("accepted_answer"):
            return _html_to_markdown(post.get("cooked", ""))

    replies = [
        p
        for p in posts
        if p.get("post_number", 0) > 1
        and p.get("post_type", _REGULAR_POST_TYPE) == _REGULAR_POST_TYPE
    ]
    if not replies:
        return None

    asker = posts[0].get("username") if posts[0].get("post_number") == 1 else None
    others = [p for p in replies if p.get("username") != asker]
    own = [p for p in replies if p.get("username") == asker]

    # max() keeps the first of equal posts, so the earliest wins a tie
    if others:
        best = max(others, key=lambda p: (_like_count(p), bool(p.get("staff"))))
        if _like_count(best) > 0:
            return _html_to_markdown(best.get("cooked", ""))

    for reply in others:
        if reply.get("staff"):
            answer = _html_to_markdown(reply.get("cooked", ""))
            if len(_MENTION.sub("", answer).strip()) >= _MIN_STAFF_ANSWER_CHARS:
                return answer

    if own:
        best = max(own, key=_like_count)
        if _like_count(best) > 0:
            return _html_to_markdown(best.get("cooked", ""))

    return None


def _fetch_category_names(
    base_url: str, *, request_delay: float = DEFAULT_REQUEST_DELAY
) -> dict[int, str]:
    """Map each category id of a forum to its name, from ``/site.json``.

    A topic (``/t/{id}.json``) names only its ``category_id``. Returns an empty
    map when the site cannot be read, so topics are stored without a category.
    """
    data = _fetch_json(f"{base_url}/site.json", delay=request_delay)
    if not data:
        logger.warning("Could not read categories from %s/site.json", base_url)
        return {}
    return {
        c["id"]: c["name"]
        for c in data.get("categories", [])
        if c.get("id") is not None and c.get("name")
    }


def sync_discourse_topics(
    base_url: str,
    project: str,
    categories: list[DiscourseCategoryConfig] | None = None,
    tags: list[str] | None = None,
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
        categories: Optional list of category configs to limit sync to.
                    If None, syncs from /latest.json (all categories).
        tags: Optional list of tags to limit sync to (topics carrying any of
              them). Combined with categories, only the topics with one of
              the tags in one of the categories are synced.
        incremental: If True, only sync topics updated since last sync
        max_topics: Maximum number of topics to sync (for testing). None for all.
        request_delay: Seconds between API requests (default: 1.0s, patient)

    Returns:
        Number of topics synced
    """
    base_url = base_url.rstrip("/")
    console.print(f"Syncing Discourse topics from {base_url}...")
    # Recorded as the last sync, so topics that change while this sync runs (a first
    # sync of a large forum takes hours) are picked up by the next one
    started_at = datetime.now(UTC).isoformat()

    # Get last sync time for incremental sync
    last_sync = None
    if incremental:
        last_sync = get_last_sync("discourse", base_url, project)
        if last_sync:
            console.print(f"Incremental sync since {last_sync}")
        else:
            console.print("No previous sync found, doing full sync")

    # Collect topic IDs to sync
    topic_ids = _collect_topic_ids(
        base_url,
        categories=categories,
        tags=tags,
        last_sync=last_sync,
        max_topics=max_topics,
        request_delay=request_delay,
    )

    if not topic_ids:
        console.print("[yellow]No new topics to sync[/yellow]")
        update_sync_metadata("discourse", base_url, 0, project, synced_at=started_at)
        return 0

    console.print(f"Found {len(topic_ids)} topics to sync")

    category_names = _fetch_category_names(base_url, request_delay=request_delay)

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
                try:
                    topic_url = f"{base_url}/t/{topic_id}.json"
                    data = _fetch_json(topic_url, delay=request_delay)

                    if data is None:
                        failed += 1
                        progress.update(task, advance=1)
                        continue

                    # Use .get() to avoid KeyError on malformed API responses
                    resolved_id = data.get("id", topic_id)
                    slug = data.get("slug", "")

                    posts = data.get("post_stream", {}).get("posts", [])
                    first_post_html = posts[0].get("cooked", "") if posts else ""
                    first_post = _html_to_markdown(first_post_html)
                    accepted_answer = _get_accepted_answer(posts) if len(posts) > 1 else None

                    upsert_discourse_topic(
                        conn,
                        forum_url=base_url,
                        topic_id=resolved_id,
                        title=data.get("title", ""),
                        first_post=first_post,
                        accepted_answer=accepted_answer,
                        category_name=(
                            data.get("category_name") or category_names.get(data.get("category_id"))
                        ),
                        tags=data.get("tags"),
                        reply_count=data.get("reply_count", 0),
                        like_count=data.get("like_count", 0),
                        views=data.get("views", 0),
                        url=f"{base_url}/t/{slug}/{resolved_id}",
                        created_at=data.get("created_at", ""),
                        last_posted_at=data.get("last_posted_at"),
                    )
                    total_synced += 1
                    uncommitted += 1

                    # Commit every 50 topics to avoid large transactions
                    if uncommitted >= 50:
                        conn.commit()
                        uncommitted = 0
                except Exception:
                    logger.exception("Failed to process topic %d from %s", topic_id, base_url)
                    failed += 1

                progress.update(task, advance=1)

            # Final commit
            conn.commit()

    # Update sync metadata
    update_sync_metadata("discourse", base_url, total_synced, project, synced_at=started_at)

    console.print(f"[green]Synced {total_synced} topics[/green]")
    if failed:
        console.print(f"[yellow]Failed to fetch {failed} topics[/yellow]")

    return total_synced


def _listing_urls(
    base_url: str,
    categories: list[DiscourseCategoryConfig] | None,
    tags: list[str] | None,
) -> list[str]:
    """The topic listings to page through for the given filters.

    Discourse lists a category at ``/c/{slug}/{id}``, a tag at ``/tag/{tag}``
    and a tag within a category at ``/tags/c/{slug}/{id}/{tag}``.
    """
    if categories and tags:
        return [
            f"{base_url}/tags/c/{cat.slug}/{cat.id}/{quote(tag, safe='')}.json"
            for cat in categories
            for tag in tags
        ]
    if categories:
        return [f"{base_url}/c/{cat.slug}/{cat.id}.json" for cat in categories]
    if tags:
        return [f"{base_url}/tag/{quote(tag, safe='')}.json" for tag in tags]
    return [f"{base_url}/latest.json"]


def _collect_topic_ids(
    base_url: str,
    *,
    categories: list[DiscourseCategoryConfig] | None = None,
    tags: list[str] | None = None,
    last_sync: str | None = None,
    max_topics: int | None = None,
    request_delay: float = DEFAULT_REQUEST_DELAY,
) -> list[int]:
    """Collect topic IDs to sync from topic listings.

    Pages through /latest.json, or the category and tag listings the filters
    name, to find topics that need syncing. A topic that appears in more than
    one listing (two of its tags are configured) is collected once.

    Args:
        base_url: Discourse base URL
        categories: Optional category filters
        tags: Optional tag filters
        last_sync: ISO timestamp of last sync (for incremental)
        max_topics: Maximum topics to collect
        request_delay: Delay between requests

    Returns:
        List of topic IDs to fetch
    """
    topic_ids: dict[int, None] = {}

    for url in _listing_urls(base_url, categories, tags):
        ids = _collect_from_listing(
            url,
            last_sync=last_sync,
            max_topics=max_topics - len(topic_ids) if max_topics else None,
            request_delay=request_delay,
        )
        topic_ids.update(dict.fromkeys(ids))
        if max_topics and len(topic_ids) >= max_topics:
            break

    ids = list(topic_ids)
    return ids[:max_topics] if max_topics else ids


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
            logger.warning(
                "Listing fetch failed at page %d for %s; collected %d topics so far",
                page,
                url,
                len(topic_ids),
            )
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
