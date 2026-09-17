"""LLM-based FAQ summarization from mailing list threads.

Uses a two-stage approach to balance cost and quality:
1. Score thread quality with evaluation agent (fast, cheap model)
2. Summarize high-quality threads with summary agent (quality model)

Models are configured per-community in config.yaml under faq_generation.
Cost tracking and estimation included for budget management (Anthropic models only).
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.core.services.anthropic_models import accepts_temperature, normalize_model
from src.knowledge.db import get_connection, update_summarization_status, upsert_faq_entry
from src.metrics.cost import estimate_cost

logger = logging.getLogger(__name__)
console = Console()

# Model ids used by the strategy comparison in estimate_summarization_cost.
# Priced through src.metrics.cost, the same table the API path bills against,
# so a rate change lands in one place.
CHEAP_MODEL = "claude-haiku-4-5"
QUALITY_MODEL = "claude-sonnet-5"

# Fraction of scored threads expected to clear the quality threshold and be
# summarized, for the "hybrid" strategy: score everything cheaply, summarize
# only the survivors on the quality model.
HYBRID_SUMMARIZED_FRACTION = 0.2

# Characters per token, Anthropic's own rule of thumb for English prose. Used
# only for cost accounting: the two LLM helpers return a score and a summary,
# not the responses, so real usage_metadata token counts are not available at
# the call site.
CHARS_PER_TOKEN = 4

# Output tokens each call produces, for the same accounting. A quality score is
# a single number; a summary is a question, an answer and tags, which is the
# ~300 tokens estimate_summarization_cost assumes per thread.
SCORE_OUTPUT_TOKENS = 10
SUMMARY_OUTPUT_TOKENS = 300


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

    except Exception as e:
        logger.error(
            "Error scoring thread: %s",
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

    except Exception as e:
        logger.error(
            "Error summarizing thread: %s",
            e,
            exc_info=True,
        )
        raise


def _estimate_call_cost(model: str, thread_context: str, output_tokens: int) -> float:
    """Estimate the cost of one LLM call over one thread.

    Priced through ``src.metrics.cost.estimate_cost`` on the model that was
    actually used, so a community running both agents on Haiku is not charged
    Sonnet rates in the run summary.

    Approximate in two known directions: the input is derived from the thread's
    character count rather than counted (the caller has a score or a summary
    back, not the response object), and the fixed prompt overhead of a few
    dozen tokens is not counted at all.

    Args:
        model: Normalized model id the call ran on.
        thread_context: The formatted thread sent as input.
        output_tokens: Expected output size for this kind of call.

    Returns:
        Estimated cost in USD.
    """
    return estimate_cost(model, len(thread_context) // CHARS_PER_TOKEN, output_tokens)


def estimate_summarization_cost(
    list_name: str,
    project: str = "eeglab",
) -> dict:
    """Estimate cost to summarize all threads.

    Compares the three strategies a community can pick between, priced from
    ``src.metrics.cost``, rather than the models any one community has
    configured. Thread sizes are estimated from message counts, so the figures
    are order-of-magnitude guidance for choosing a strategy, not a bill.

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
        total_output_tokens = thread_count * SUMMARY_OUTPUT_TOKENS

        # Cost of running every thread through one model or the other.
        haiku_cost = estimate_cost(CHEAP_MODEL, total_input_tokens, total_output_tokens)
        sonnet_cost = estimate_cost(QUALITY_MODEL, total_input_tokens, total_output_tokens)

        # Hybrid: score everything on the cheap model, summarize the survivors
        # on the quality model.
        hybrid_cost = haiku_cost + sonnet_cost * HYBRID_SUMMARIZED_FRACTION

        return {
            "thread_count": thread_count,
            "estimated_input_tokens": total_input_tokens,
            "estimated_output_tokens": total_output_tokens,
            "haiku_cost": haiku_cost,
            "sonnet_cost": sonnet_cost,
            "hybrid_cost": hybrid_cost,
            "recommended": "hybrid" if thread_count > 1000 else "haiku",
        }


def _warn_if_temperature_ignored(
    temperature: float | None, model: str, agent_role: str, project: str
) -> None:
    """Log when a community's ``temperature`` will not reach the API.

    ``claude-sonnet-5`` accepts only its default temperature, so
    ``create_anthropic_llm`` drops the field rather than sending a value the
    API would reject. A community that lowered the temperature to make scoring
    deterministic should hear that it stopped applying. ``CommunityConfig``
    warns about this at config load too; this covers the sync run, where the
    warning lands in the log the operator is already watching.

    Args:
        temperature: The configured temperature, if any.
        model: The agent's configured model id, in any form.
        agent_role: "evaluation_agent" or "summary_agent", for the message.
        project: Community ID, for the message.
    """
    if temperature is not None and not accepts_temperature(model):
        logger.warning(
            "faq_generation.%s.temperature=%s is ignored for %s: %s accepts only its "
            "default temperature. Use claude-haiku-4-5 for this agent if the "
            "temperature matters.",
            agent_role,
            temperature,
            project,
            model,
        )


def _warn_if_provider_ignored(provider: str | None, agent_role: str, project: str) -> None:
    """Log when a community's ``provider`` routing hint has no effect.

    ``provider`` selects among OpenRouter's upstream hosts (DeepInfra, Cerebras
    and so on). The Claude Platform on AWS has no routing layer, so the field
    is silently inert here. A community that set it deliberately, to get FP8
    quantization for cost reasons for instance, should hear that it stopped
    applying rather than discover it in a bill.

    Args:
        provider: The configured provider hint, if any.
        agent_role: "evaluation_agent" or "summary_agent", for the message.
        project: Community ID, for the message.
    """
    if provider:
        logger.warning(
            "faq_generation.%s.provider=%r is ignored for %s: FAQ generation runs on "
            "the Claude Platform on AWS, which has no provider routing. Remove the "
            "field from config.yaml.",
            agent_role,
            provider,
            project,
        )


def summarize_threads(
    list_name: str,
    project: str = "eeglab",
    quality_threshold: float | None = None,
    batch_size: int = 10,
    max_threads: int | None = None,
) -> dict:
    """Summarize mailing list threads into FAQ entries.

    Uses community-specific FAQ generation config for model selection
    and quality thresholds. Falls back to defaults if not configured.

    Args:
        list_name: Mailing list identifier
        project: Community ID
        quality_threshold: Minimum quality score to summarize (0.0-1.0)
                          If None, uses community config (default: 0.7)
        batch_size: Number of threads per LLM batch
        max_threads: Maximum threads to process (for testing/budgeting)

    Returns:
        Summary stats: {processed, summarized, skipped, total_cost, total_tokens}
    """
    # Load community config for FAQ generation settings
    from src.assistants import registry
    from src.core.services.anthropic_llm import create_anthropic_llm

    config = registry.get_community_config(project)
    faq_config = config.faq_generation if config else None

    # Use config-based models or fall back to defaults
    if faq_config:
        for role, agent in (
            ("evaluation_agent", faq_config.evaluation_agent),
            ("summary_agent", faq_config.summary_agent),
        ):
            _warn_if_provider_ignored(agent.provider, role, project)
            _warn_if_temperature_ignored(agent.temperature, agent.model, role, project)

        # Evaluation agent (for scoring quality)
        eval_agent = create_anthropic_llm(
            model=faq_config.evaluation_agent.model,
            temperature=faq_config.evaluation_agent.temperature,
            thinking=None,
            enable_caching=faq_config.evaluation_agent.enable_caching,
        )

        # Summary agent (for creating FAQs)
        summary_agent = create_anthropic_llm(
            model=faq_config.summary_agent.model,
            temperature=faq_config.summary_agent.temperature,
            thinking=None,
            enable_caching=faq_config.summary_agent.enable_caching,
        )

        # Track model names for the database and for cost accounting.
        # Normalized, so a config still carrying a legacy OpenRouter-style id
        # records the id that was actually billed, which is also the id
        # src/metrics/cost.py prices.
        summary_model_name = normalize_model(faq_config.summary_agent.model)
        eval_model_name = normalize_model(faq_config.evaluation_agent.model)

        # Use config threshold if not overridden
        if quality_threshold is None:
            quality_threshold = faq_config.quality_threshold
    else:
        # Fallback to hardcoded defaults (backward compatibility)
        # Note: Uses same model for both agents (Haiku 4.5) with different temperatures.
        # This is a simplified approach for communities without FAQ config.
        # The temperature difference (0.0 for scoring, 0.1 for summarization) provides
        # deterministic evaluation while allowing slight creativity in FAQ phrasing.
        logger.warning(
            "No faq_generation config found for %s, using defaults",
            project,
        )
        summary_model_name = CHEAP_MODEL
        eval_model_name = CHEAP_MODEL
        eval_agent = create_anthropic_llm(
            model=summary_model_name,
            temperature=0.0,  # Deterministic scoring
            thinking=None,
            enable_caching=True,
        )
        summary_agent = create_anthropic_llm(
            model=summary_model_name,
            temperature=0.1,  # Slightly creative for natural phrasing
            thinking=None,
            enable_caching=True,
        )
        if quality_threshold is None:
            quality_threshold = 0.7

    with get_connection(project) as conn:
        # Get threads needing summarization
        # TODO: Use faq_config.sources settings for min_messages, min_participants, enabled
        # Currently hardcoded to msg_count >= 2 for backward compatibility
        # See FAQSourceConfig in src/core/config/community.py
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
                    quality_score = _score_thread_quality(thread_context, eval_agent)

                    # The scoring call billed whether or not its answer parsed
                    # (an API failure raises instead of returning None), and it
                    # runs on every thread, so it is most of a run's cost.
                    total_cost += _estimate_call_cost(
                        eval_model_name, thread_context, SCORE_OUTPUT_TOKENS
                    )

                    if quality_score is None:
                        # Scoring failed due to LLM error (not a low score, which would be < threshold)
                        # These threads are marked 'failed' and won't be retried automatically
                        # This prevents problematic threads from blocking batch processing
                        # Manual retry: Delete failed entry from DB or run sync with --retry-failed flag
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

                    # Summarize with summary agent (for high-quality threads)
                    summary = _summarize_thread(thread_context, summary_agent)

                    # Same reasoning as the scoring call: unparsable output
                    # still cost what it cost.
                    total_cost += _estimate_call_cost(
                        summary_model_name, thread_context, SUMMARY_OUTPUT_TOKENS
                    )

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
                        summary_model=summary_model_name,
                    )

                    update_summarization_status(
                        conn,
                        list_name=list_name,
                        thread_id=thread_id,
                        status="summarized",
                    )

                    summarized += 1

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
