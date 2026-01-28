"""LLM-based FAQ summarization from mailing list threads.

Uses a two-stage approach to balance cost and quality:
1. Score thread quality with Haiku (fast, cheap)
2. Summarize high-quality threads with Sonnet (high quality)

Cost tracking and estimation included for budget management.
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.core.services.litellm_llm import create_openrouter_llm
from src.knowledge.db import get_connection, update_summarization_status, upsert_faq_entry

logger = logging.getLogger(__name__)
console = Console()

# Cost tracking (per 1M tokens)
HAIKU_COST_PER_1M_INPUT = 0.25
HAIKU_COST_PER_1M_OUTPUT = 1.25
SONNET_COST_PER_1M_INPUT = 3.0
SONNET_COST_PER_1M_OUTPUT = 15.0


@dataclass
class FAQSummary:
    """LLM-generated FAQ summary."""

    question: str
    answer: str
    tags: list[str]
    category: str
    quality_score: float


def _build_thread_context(messages: list[dict]) -> str:
    """Format thread messages for LLM prompt.

    Args:
        messages: List of message dicts from database

    Returns:
        Formatted thread context string
    """
    lines = []
    for i, msg in enumerate(messages, 1):
        lines.append(f"--- Message {i} ---")
        lines.append(f"From: {msg['author'] or 'Unknown'}")
        lines.append(f"Date: {msg['date']}")
        lines.append(f"Subject: {msg['subject']}")
        lines.append("")
        # Truncate long messages
        body = msg["body"] or ""
        if len(body) > 2000:
            body = body[:2000] + "\n[... truncated ...]"
        lines.append(body)
        lines.append("")

    return "\n".join(lines)


def _score_thread_quality(thread_context: str, model) -> float | None:
    """Use LLM to score thread quality (0.0-1.0).

    Args:
        thread_context: Formatted thread messages
        model: LLM instance for scoring

    Returns:
        Quality score between 0.0 and 1.0, or None if scoring failed
    """
    prompt = f"""Rate the value of this mailing list thread as a FAQ entry on a scale of 0.0 to 1.0.

Consider:
- Does it have a clear, answerable technical question?
- Are the responses helpful and authoritative?
- Is it substantive (not just social chat or spam)?
- Would future users benefit from this Q&A?

Thread:
{thread_context}

Respond with ONLY a number between 0.0 and 1.0 (e.g., "0.75"):"""

    try:
        import httpx

        response = model.invoke([HumanMessage(content=prompt)])
        score_text = response.content.strip()
        # Extract first float found
        match = re.search(r"(\d+\.?\d*)", score_text)
        if match:
            score = float(match.group(1))
            return max(0.0, min(1.0, score))

        # LLM didn't return a parseable score
        logger.warning(
            "LLM returned unparseable score: %s",
            score_text[:100],
            extra={"response_preview": score_text[:100]},
        )
        return None

    except httpx.HTTPStatusError as e:
        logger.error(
            "LLM API error (HTTP %d) while scoring thread: %s",
            e.response.status_code,
            e,
            extra={"status_code": e.response.status_code},
        )
        return None
    except httpx.TimeoutException as e:
        logger.error("LLM API timeout while scoring thread: %s", e)
        return None
    except httpx.RequestError as e:
        logger.error("Network error while scoring thread: %s", e)
        return None
    except Exception as e:
        # Unexpected error - likely a bug
        logger.error(
            "Unexpected error scoring thread: %s",
            e,
            exc_info=True,
        )
        raise


def _summarize_thread(thread_context: str, model) -> FAQSummary | None:
    """Use LLM to create FAQ summary.

    Args:
        thread_context: Formatted thread messages
        model: LLM instance for summarization

    Returns:
        FAQSummary or None if summarization failed
    """
    system_prompt = """You are an expert at creating FAQ entries from mailing list threads.

Extract:
1. Core Question: The main technical question being asked
2. Best Answer: Synthesize the most helpful response(s)
3. Tags: 3-5 lowercase topic keywords (hyphenated, e.g., "data-import")
4. Category: One of: troubleshooting, how-to, bug-report, feature-request, discussion, reference

Format as JSON:
{
  "question": "...",
  "answer": "...",
  "tags": ["tag1", "tag2", ...],
  "category": "..."
}"""

    user_prompt = f"""Thread:
{thread_context}"""

    try:
        import httpx

        response = model.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )

        # Parse JSON response
        content = response.content.strip()
        # Remove markdown code blocks if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            # Remove trailing code block
            if content.endswith("```"):
                content = content[:-3]

        try:
            data = json.loads(content.strip())
        except json.JSONDecodeError as e:
            logger.error(
                "LLM returned invalid JSON: %s. Response was: %s",
                e,
                content[:500],
                extra={"response_preview": content[:500], "error_position": e.pos},
            )
            return None

        # Validate required fields
        required_fields = ["question", "answer"]
        missing_fields = [f for f in required_fields if f not in data]
        if missing_fields:
            logger.error(
                "LLM response missing required fields: %s. Response was: %s",
                missing_fields,
                data,
                extra={"missing_fields": missing_fields, "response": data},
            )
            return None

        # Validate and normalize optional fields
        category = data.get("category", "discussion") or "discussion"
        tags = data.get("tags", []) or []
        answer = data["answer"]

        # Validate category is one of expected values
        valid_categories = {
            "troubleshooting",
            "how-to",
            "bug-report",
            "feature-request",
            "discussion",
            "reference",
        }
        if category not in valid_categories:
            logger.warning("LLM returned invalid category '%s', using 'discussion'", category)
            category = "discussion"

        # Limit answer length to prevent bloat
        if len(answer) > 10000:
            logger.warning("Answer too long (%d chars), truncating to 10000", len(answer))
            answer = answer[:10000] + "\n\n[Answer truncated due to length]"

        return FAQSummary(
            question=data["question"],
            answer=answer,
            tags=tags,
            category=category,
            quality_score=0.0,  # Set externally
        )

    except httpx.HTTPStatusError as e:
        logger.error(
            "LLM API error (HTTP %d) while summarizing thread: %s",
            e.response.status_code,
            e,
            extra={"status_code": e.response.status_code},
        )
        return None
    except httpx.TimeoutException as e:
        logger.error("LLM API timeout while summarizing thread: %s", e)
        return None
    except httpx.RequestError as e:
        logger.error("Network error while summarizing thread: %s", e)
        return None
    except Exception as e:
        # Unexpected error - likely a bug
        logger.error(
            "Unexpected error summarizing thread: %s",
            e,
            exc_info=True,
        )
        raise


def estimate_summarization_cost(
    list_name: str,
    project: str = "eeglab",
) -> dict:
    """Estimate cost to summarize all threads.

    Args:
        list_name: Mailing list identifier
        project: Community ID

    Returns:
        Dict with cost estimates and thread counts
    """
    with get_connection(project) as conn:
        # Count threads (group by thread_id)
        cursor = conn.execute(
            """
            SELECT thread_id, COUNT(*) as msg_count
            FROM mailing_list_messages
            WHERE list_name = ? AND thread_id IS NOT NULL
            GROUP BY thread_id
            HAVING msg_count >= 2
        """,
            (list_name,),
        )

        threads = cursor.fetchall()
        thread_count = len(threads)

        if thread_count == 0:
            return {
                "thread_count": 0,
                "estimated_input_tokens": 0,
                "estimated_output_tokens": 0,
                "haiku_cost": 0.0,
                "sonnet_cost": 0.0,
                "hybrid_cost": 0.0,
                "recommended": "none",
            }

        # Estimate tokens (heuristic: 600 tokens per message)
        avg_tokens = sum(row["msg_count"] * 600 for row in threads) // max(thread_count, 1)
        total_input_tokens = thread_count * avg_tokens
        total_output_tokens = thread_count * 300  # Summaries ~300 tokens

        # Calculate costs
        haiku_cost = (
            total_input_tokens * HAIKU_COST_PER_1M_INPUT / 1_000_000
            + total_output_tokens * HAIKU_COST_PER_1M_OUTPUT / 1_000_000
        )

        sonnet_cost = (
            total_input_tokens * SONNET_COST_PER_1M_INPUT / 1_000_000
            + total_output_tokens * SONNET_COST_PER_1M_OUTPUT / 1_000_000
        )

        # Hybrid approach (20% with Sonnet after Haiku scoring)
        hybrid_cost = haiku_cost + (sonnet_cost * 0.2)

        return {
            "thread_count": thread_count,
            "estimated_input_tokens": total_input_tokens,
            "estimated_output_tokens": total_output_tokens,
            "haiku_cost": haiku_cost,
            "sonnet_cost": sonnet_cost,
            "hybrid_cost": hybrid_cost,
            "recommended": "hybrid" if thread_count > 1000 else "haiku",
        }


def summarize_threads(
    list_name: str,
    project: str = "eeglab",
    quality_threshold: float = 0.6,
    batch_size: int = 10,
    max_threads: int | None = None,
) -> dict:
    """Summarize mailing list threads into FAQ entries.

    Args:
        list_name: Mailing list identifier
        project: Community ID
        quality_threshold: Minimum quality score to summarize (0.0-1.0)
        batch_size: Number of threads per LLM batch
        max_threads: Maximum threads to process (for testing/budgeting)

    Returns:
        Summary stats: {processed, summarized, skipped, total_cost, total_tokens}
    """
    # Create LLM instances
    haiku = create_openrouter_llm(
        model="anthropic/claude-3-5-haiku",
        temperature=0.1,
        enable_caching=True,
    )

    sonnet = create_openrouter_llm(
        model="anthropic/claude-3-5-sonnet",
        temperature=0.1,
        enable_caching=True,
    )

    with get_connection(project) as conn:
        # Get threads needing summarization
        cursor = conn.execute(
            """
            SELECT m.thread_id, COUNT(*) as msg_count,
                   COUNT(DISTINCT m.author) as participant_count,
                   MIN(m.date) as first_date
            FROM mailing_list_messages m
            LEFT JOIN faq_entries f ON m.thread_id = f.thread_id AND m.list_name = f.list_name
            WHERE m.list_name = ? AND m.thread_id IS NOT NULL
              AND f.id IS NULL
            GROUP BY m.thread_id
            HAVING msg_count >= 2
            ORDER BY msg_count DESC
            LIMIT ?
        """,
            (list_name, max_threads or 999999),
        )

        threads_to_process = cursor.fetchall()

        if not threads_to_process:
            console.print("[yellow]No threads to summarize[/yellow]")
            return {"processed": 0, "summarized": 0, "skipped": 0, "total_cost": 0.0}

        console.print(f"Found {len(threads_to_process)} threads to process")

        # Process threads
        processed = 0
        summarized = 0
        skipped = 0
        total_cost = 0.0

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            task = progress.add_task("Summarizing threads...", total=len(threads_to_process))

            for thread_info in threads_to_process:
                thread_id = thread_info["thread_id"]

                try:
                    # Fetch thread messages
                    cursor = conn.execute(
                        """
                        SELECT * FROM mailing_list_messages
                        WHERE list_name = ? AND thread_id = ?
                        ORDER BY date
                    """,
                        (list_name, thread_id),
                    )

                    messages = [dict(row) for row in cursor.fetchall()]
                    thread_context = _build_thread_context(messages)

                    # Score quality
                    quality_score = _score_thread_quality(thread_context, haiku)

                    if quality_score is None:
                        # Scoring failed - this is different from a low score
                        skipped += 1
                        update_summarization_status(
                            conn,
                            list_name=list_name,
                            thread_id=thread_id,
                            status="failed",
                            failure_reason="Failed to score thread quality (LLM error)",
                        )
                        progress.update(task, advance=1)
                        continue

                    if quality_score < quality_threshold:
                        skipped += 1
                        update_summarization_status(
                            conn,
                            list_name=list_name,
                            thread_id=thread_id,
                            status="skipped",
                            failure_reason=f"Quality score {quality_score:.2f} below threshold",
                        )
                        progress.update(task, advance=1)
                        continue

                    # Summarize with Sonnet (for high-quality threads)
                    summary = _summarize_thread(thread_context, sonnet)
                    if not summary:
                        update_summarization_status(
                            conn,
                            list_name=list_name,
                            thread_id=thread_id,
                            status="failed",
                            failure_reason="Summarization failed",
                        )
                        progress.update(task, advance=1)
                        continue

                    # Insert FAQ entry
                    summary.quality_score = quality_score
                    thread_url = messages[0]["url"].rsplit("/", 1)[0] + f"/thread.html#{thread_id}"

                    upsert_faq_entry(
                        conn,
                        list_name=list_name,
                        thread_id=thread_id,
                        thread_url=thread_url,
                        question=summary.question,
                        answer=summary.answer,
                        tags=summary.tags,
                        category=summary.category,
                        message_count=len(messages),
                        participant_count=thread_info["participant_count"],
                        first_message_date=thread_info["first_date"],
                        quality_score=quality_score,
                        summary_model="anthropic/claude-3-5-sonnet",
                    )

                    update_summarization_status(
                        conn,
                        list_name=list_name,
                        thread_id=thread_id,
                        status="summarized",
                    )

                    summarized += 1

                    # Estimate cost (rough)
                    tokens = len(thread_context) // 4  # ~4 chars per token
                    cost = (
                        tokens * (SONNET_COST_PER_1M_INPUT + SONNET_COST_PER_1M_OUTPUT) / 1_000_000
                    )
                    total_cost += cost

                    # Commit every batch
                    if summarized % batch_size == 0:
                        conn.commit()

                except sqlite3.Error as db_err:
                    # Database errors should probably abort the entire batch
                    logger.error(
                        "Database error processing thread %s: %s",
                        thread_id,
                        db_err,
                        exc_info=True,
                        extra={
                            "list_name": list_name,
                            "thread_id": thread_id,
                            "operation": "database",
                        },
                    )
                    # Re-raise database errors as they indicate serious problems
                    raise

                except Exception as e:
                    # Unexpected error - likely a programming bug
                    logger.error(
                        "Unexpected error processing thread %s: %s",
                        thread_id,
                        e,
                        exc_info=True,
                        extra={
                            "list_name": list_name,
                            "thread_id": thread_id,
                            "message_count": len(messages) if "messages" in locals() else None,
                        },
                    )
                    update_summarization_status(
                        conn,
                        list_name=list_name,
                        thread_id=thread_id,
                        status="failed",
                        failure_reason=f"Unexpected error: {type(e).__name__}",
                    )

                processed += 1
                progress.update(task, advance=1)

            # Final commit
            conn.commit()

        console.print(f"\n[green]✓ Summarized {summarized}/{processed} threads[/green]")
        console.print(f"[dim]Estimated cost: ${total_cost:.2f}[/dim]")

        return {
            "processed": processed,
            "summarized": summarized,
            "skipped": skipped,
            "total_cost": total_cost,
        }
