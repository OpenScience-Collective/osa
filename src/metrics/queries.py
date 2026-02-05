"""Aggregation queries for the metrics database.

Provides summary statistics, usage breakdowns, and overview queries
for both per-community and cross-community metrics.
"""

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

# SQLite strftime patterns for time-bucketed queries
_PERIOD_FORMAT_MAP = {
    "daily": "%Y-%m-%d",
    "weekly": "%Y-W%W",
    "monthly": "%Y-%m",
}


def _validate_period(period: str) -> str:
    """Validate and return the strftime format for a period.

    Raises ValueError if period is not one of: daily, weekly, monthly.
    """
    if period not in _PERIOD_FORMAT_MAP:
        raise ValueError(f"Invalid period: {period}. Must be one of: daily, weekly, monthly")
    return _PERIOD_FORMAT_MAP[period]


def _count_tools(
    community_id: str, conn: sqlite3.Connection, limit: int = 5
) -> list[dict[str, Any]]:
    """Count tool usage from JSON arrays in the tools_called column.

    Returns top tools sorted by count descending.
    """
    tool_rows = conn.execute(
        """
        SELECT tools_called
        FROM request_log
        WHERE community_id = ? AND tools_called IS NOT NULL
        """,
        (community_id,),
    ).fetchall()
    tool_counts: dict[str, int] = {}
    for tr in tool_rows:
        try:
            tools = json.loads(tr["tools_called"])
            for tool in tools:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Malformed tools_called data in request_log for community %s: %r",
                community_id,
                tr["tools_called"],
            )
    top = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [{"tool": name, "count": count} for name, count in top]


def get_community_summary(community_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
    """Get summary statistics for a single community.

    Args:
        community_id: The community identifier.
        conn: SQLite connection (with row_factory=sqlite3.Row).

    Returns:
        Dict with total_requests, total_input_tokens, total_output_tokens,
        total_tokens, avg_duration_ms, total_estimated_cost, error_rate,
        top_models, top_tools.
    """
    row = conn.execute(
        """
        SELECT
            COUNT(*) as total_requests,
            COALESCE(SUM(input_tokens), 0) as total_input_tokens,
            COALESCE(SUM(output_tokens), 0) as total_output_tokens,
            COALESCE(SUM(total_tokens), 0) as total_tokens,
            COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
            COALESCE(SUM(estimated_cost), 0) as total_estimated_cost,
            COUNT(CASE WHEN status_code >= 400 THEN 1 END) as error_count
        FROM request_log
        WHERE community_id = ?
        """,
        (community_id,),
    ).fetchone()

    total = row["total_requests"]
    error_rate = row["error_count"] / total if total > 0 else 0.0

    # Top models
    model_rows = conn.execute(
        """
        SELECT model, COUNT(*) as count
        FROM request_log
        WHERE community_id = ? AND model IS NOT NULL
        GROUP BY model
        ORDER BY count DESC
        LIMIT 5
        """,
        (community_id,),
    ).fetchall()

    return {
        "community_id": community_id,
        "total_requests": total,
        "total_input_tokens": row["total_input_tokens"],
        "total_output_tokens": row["total_output_tokens"],
        "total_tokens": row["total_tokens"],
        "avg_duration_ms": round(row["avg_duration_ms"], 1),
        "total_estimated_cost": round(row["total_estimated_cost"], 4),
        "error_rate": round(error_rate, 4),
        "top_models": [{"model": r["model"], "count": r["count"]} for r in model_rows],
        "top_tools": _count_tools(community_id, conn),
    }


def get_usage_stats(
    community_id: str,
    period: str,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Get time-bucketed usage statistics for a community.

    Args:
        community_id: The community identifier.
        period: One of "daily", "weekly", "monthly".
        conn: SQLite connection.

    Returns:
        Dict with period, community_id, and buckets list.
    """
    fmt = _validate_period(period)

    # Safe to use f-string: fmt is from _PERIOD_FORMAT_MAP whitelist, not user input
    rows = conn.execute(
        f"""
        SELECT
            strftime('{fmt}', timestamp) as bucket,
            COUNT(*) as requests,
            COALESCE(SUM(total_tokens), 0) as tokens,
            COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
            COALESCE(SUM(estimated_cost), 0) as estimated_cost,
            COUNT(CASE WHEN status_code >= 400 THEN 1 END) as errors
        FROM request_log
        WHERE community_id = ?
        GROUP BY bucket
        ORDER BY bucket
        """,
        (community_id,),
    ).fetchall()

    return {
        "community_id": community_id,
        "period": period,
        "buckets": [
            {
                "bucket": r["bucket"],
                "requests": r["requests"],
                "tokens": r["tokens"],
                "avg_duration_ms": round(r["avg_duration_ms"], 1),
                "estimated_cost": round(r["estimated_cost"], 4),
                "errors": r["errors"],
            }
            for r in rows
        ],
    }


def get_overview(conn: sqlite3.Connection) -> dict[str, Any]:
    """Get cross-community metrics overview.

    Args:
        conn: SQLite connection.

    Returns:
        Dict with total stats and per-community breakdown.
    """
    # Global totals
    totals = conn.execute(
        """
        SELECT
            COUNT(*) as total_requests,
            COALESCE(SUM(total_tokens), 0) as total_tokens,
            COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
            COALESCE(SUM(estimated_cost), 0) as total_estimated_cost,
            COUNT(CASE WHEN status_code >= 400 THEN 1 END) as total_errors
        FROM request_log
        """
    ).fetchone()

    total_req = totals["total_requests"]

    # Per-community breakdown
    community_rows = conn.execute(
        """
        SELECT
            community_id,
            COUNT(*) as requests,
            COALESCE(SUM(total_tokens), 0) as tokens,
            COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
            COALESCE(SUM(estimated_cost), 0) as estimated_cost
        FROM request_log
        WHERE community_id IS NOT NULL
        GROUP BY community_id
        ORDER BY requests DESC
        """
    ).fetchall()

    return {
        "total_requests": total_req,
        "total_tokens": totals["total_tokens"],
        "avg_duration_ms": round(totals["avg_duration_ms"], 1),
        "total_estimated_cost": round(totals["total_estimated_cost"], 4),
        "error_rate": round(totals["total_errors"] / total_req, 4) if total_req > 0 else 0.0,
        "communities": [
            {
                "community_id": r["community_id"],
                "requests": r["requests"],
                "tokens": r["tokens"],
                "avg_duration_ms": round(r["avg_duration_ms"], 1),
                "estimated_cost": round(r["estimated_cost"], 4),
            }
            for r in community_rows
        ],
    }


def get_token_breakdown(
    conn: sqlite3.Connection,
    community_id: str | None = None,
) -> dict[str, Any]:
    """Get token usage breakdown by model and key_source.

    Args:
        conn: SQLite connection.
        community_id: Optional filter by community.

    Returns:
        Dict with by_model and by_key_source breakdowns.
    """
    where = ""
    params: tuple = ()
    if community_id:
        where = "WHERE community_id = ?"
        params = (community_id,)

    by_model = conn.execute(
        f"""
        SELECT
            model,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(total_tokens), 0) as total_tokens,
            COALESCE(SUM(estimated_cost), 0) as estimated_cost
        FROM request_log
        {where}
        {"AND" if where else "WHERE"} model IS NOT NULL
        GROUP BY model
        ORDER BY total_tokens DESC
        """,
        params,
    ).fetchall()

    by_key_source = conn.execute(
        f"""
        SELECT
            key_source,
            COUNT(*) as requests,
            COALESCE(SUM(total_tokens), 0) as total_tokens,
            COALESCE(SUM(estimated_cost), 0) as estimated_cost
        FROM request_log
        {where}
        {"AND" if where else "WHERE"} key_source IS NOT NULL
        GROUP BY key_source
        ORDER BY requests DESC
        """,
        params,
    ).fetchall()

    return {
        "community_id": community_id,
        "by_model": [
            {
                "model": r["model"],
                "requests": r["requests"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "total_tokens": r["total_tokens"],
                "estimated_cost": round(r["estimated_cost"], 4),
            }
            for r in by_model
        ],
        "by_key_source": [
            {
                "key_source": r["key_source"],
                "requests": r["requests"],
                "total_tokens": r["total_tokens"],
                "estimated_cost": round(r["estimated_cost"], 4),
            }
            for r in by_key_source
        ],
    }


# ---------------------------------------------------------------------------
# Public query functions (no tokens, costs, or model info)
# ---------------------------------------------------------------------------


def get_public_overview(
    conn: sqlite3.Connection,
    registered_communities: list[str] | None = None,
) -> dict[str, Any]:
    """Get public metrics overview with only non-sensitive data.

    Returns request counts and error rates; no tokens, costs, or model info.
    Only counts community-scoped requests (not health checks, sync, etc.).

    Args:
        conn: SQLite connection.
        registered_communities: If provided, ensures all these communities
            appear in the response even if they have zero logged requests.

    Returns:
        Dict with total_requests, error_rate, communities_active,
        and per-community request counts.
    """
    # Only count community-scoped requests, not infrastructure endpoints
    totals = conn.execute(
        """
        SELECT
            COUNT(*) as total_requests,
            COUNT(CASE WHEN status_code >= 400 THEN 1 END) as total_errors
        FROM request_log
        WHERE community_id IS NOT NULL
        """
    ).fetchone()

    total_req = totals["total_requests"]

    community_rows = conn.execute(
        """
        SELECT
            community_id,
            COUNT(*) as requests,
            COUNT(CASE WHEN status_code >= 400 THEN 1 END) as errors
        FROM request_log
        WHERE community_id IS NOT NULL
        GROUP BY community_id
        ORDER BY requests DESC
        """
    ).fetchall()

    # Build community data from DB results
    community_data: dict[str, dict[str, Any]] = {}
    for r in community_rows:
        cid = r["community_id"]
        community_data[cid] = {
            "community_id": cid,
            "requests": r["requests"],
            "error_rate": round(r["errors"] / r["requests"], 4) if r["requests"] > 0 else 0.0,
        }

    # Include registered communities with zero requests
    if registered_communities:
        for cid in registered_communities:
            if cid not in community_data:
                community_data[cid] = {
                    "community_id": cid,
                    "requests": 0,
                    "error_rate": 0.0,
                }

    # Sort by requests descending, then alphabetically
    communities_list = sorted(
        community_data.values(),
        key=lambda c: (-c["requests"], c["community_id"]),
    )

    return {
        "total_requests": total_req,
        "error_rate": round(totals["total_errors"] / total_req, 4) if total_req > 0 else 0.0,
        "communities_active": sum(1 for c in communities_list if c["requests"] > 0),
        "communities": communities_list,
    }


def get_public_community_summary(community_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
    """Get public summary for a single community.

    Returns request counts and top tools; no tokens, costs, or model info.

    Args:
        community_id: The community identifier.
        conn: SQLite connection.

    Returns:
        Dict with community_id, total_requests, error_rate, top_tools.
    """
    row = conn.execute(
        """
        SELECT
            COUNT(*) as total_requests,
            COUNT(CASE WHEN status_code >= 400 THEN 1 END) as error_count
        FROM request_log
        WHERE community_id = ?
        """,
        (community_id,),
    ).fetchone()

    total = row["total_requests"]
    error_rate = row["error_count"] / total if total > 0 else 0.0

    return {
        "community_id": community_id,
        "total_requests": total,
        "error_rate": round(error_rate, 4),
        "top_tools": _count_tools(community_id, conn),
    }


def get_public_usage_stats(
    community_id: str,
    period: str,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Get public time-bucketed usage statistics.

    Returns request counts and errors per bucket; no tokens or costs.

    Args:
        community_id: The community identifier.
        period: One of "daily", "weekly", "monthly".
        conn: SQLite connection.

    Returns:
        Dict with period, community_id, and buckets list.
    """
    fmt = _validate_period(period)

    # Safe to use f-string: fmt is from _PERIOD_FORMAT_MAP whitelist, not user input
    rows = conn.execute(
        f"""
        SELECT
            strftime('{fmt}', timestamp) as bucket,
            COUNT(*) as requests,
            COUNT(CASE WHEN status_code >= 400 THEN 1 END) as errors
        FROM request_log
        WHERE community_id = ?
        GROUP BY bucket
        ORDER BY bucket
        """,
        (community_id,),
    ).fetchall()

    return {
        "community_id": community_id,
        "period": period,
        "buckets": [
            {
                "bucket": r["bucket"],
                "requests": r["requests"],
                "errors": r["errors"],
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Quality metrics queries
# ---------------------------------------------------------------------------


def get_quality_metrics(
    community_id: str,
    conn: sqlite3.Connection,
    period: str = "daily",
) -> dict[str, Any]:
    """Get quality metrics for a community, bucketed by time period.

    Returns error rates, average tool call counts, and latency percentiles
    (p50, p95) per time bucket.

    Args:
        community_id: The community identifier.
        conn: SQLite connection.
        period: One of "daily", "weekly", "monthly".

    Returns:
        Dict with period, community_id, and buckets with quality data.
    """
    fmt = _validate_period(period)

    rows = conn.execute(
        f"""
        SELECT
            strftime('{fmt}', timestamp) as bucket,
            COUNT(*) as requests,
            COUNT(CASE WHEN status_code >= 400 THEN 1 END) as errors,
            COALESCE(AVG(tool_call_count), 0) as avg_tool_calls,
            COUNT(CASE WHEN error_message IS NOT NULL THEN 1 END) as agent_errors,
            COUNT(CASE WHEN langfuse_trace_id IS NOT NULL THEN 1 END) as traced_requests
        FROM request_log
        WHERE community_id = ?
        GROUP BY bucket
        ORDER BY bucket
        """,
        (community_id,),
    ).fetchall()

    # Compute latency percentiles per bucket
    buckets = []
    for r in rows:
        bucket_name = r["bucket"]
        total = r["requests"]
        error_rate = r["errors"] / total if total > 0 else 0.0

        # Latency percentiles for this bucket
        p50, p95 = _fetch_latency_percentiles(
            conn,
            community_id,
            extra_where=f"AND strftime('{fmt}', timestamp) = ?",
            extra_params=(bucket_name,),
        )

        buckets.append(
            {
                "bucket": bucket_name,
                "requests": total,
                "error_rate": round(error_rate, 4),
                "avg_tool_calls": round(r["avg_tool_calls"], 2),
                "agent_errors": r["agent_errors"],
                "traced_requests": r["traced_requests"],
                "p50_duration_ms": round(p50, 1) if p50 is not None else None,
                "p95_duration_ms": round(p95, 1) if p95 is not None else None,
            }
        )

    return {
        "community_id": community_id,
        "period": period,
        "buckets": buckets,
    }


def get_quality_summary(community_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
    """Get overall quality overview for a community.

    Args:
        community_id: The community identifier.
        conn: SQLite connection.

    Returns:
        Dict with overall quality stats: error_rate, avg_tool_calls,
        agent_errors, p50/p95 latency, traced percentage.
    """
    row = conn.execute(
        """
        SELECT
            COUNT(*) as total_requests,
            COUNT(CASE WHEN status_code >= 400 THEN 1 END) as error_count,
            COALESCE(AVG(tool_call_count), 0) as avg_tool_calls,
            COUNT(CASE WHEN error_message IS NOT NULL THEN 1 END) as agent_errors,
            COUNT(CASE WHEN langfuse_trace_id IS NOT NULL THEN 1 END) as traced
        FROM request_log
        WHERE community_id = ?
        """,
        (community_id,),
    ).fetchone()

    total = row["total_requests"]
    error_rate = row["error_count"] / total if total > 0 else 0.0
    traced_pct = row["traced"] / total if total > 0 else 0.0

    # Latency percentiles
    p50, p95 = _fetch_latency_percentiles(conn, community_id)

    return {
        "community_id": community_id,
        "total_requests": total,
        "error_rate": round(error_rate, 4),
        "avg_tool_calls": round(row["avg_tool_calls"], 2),
        "agent_errors": row["agent_errors"],
        "traced_pct": round(traced_pct, 4),
        "p50_duration_ms": round(p50, 1) if p50 is not None else None,
        "p95_duration_ms": round(p95, 1) if p95 is not None else None,
    }


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Compute percentile from a sorted list of values.

    Uses floor-rank method: index = floor(pct * len), clamped to valid range.

    Args:
        sorted_values: Pre-sorted list of numeric values.
        pct: Percentile as fraction (e.g., 0.5 for p50, 0.95 for p95).

    Returns:
        The percentile value, or None if list is empty.
    """
    if not sorted_values:
        return None
    idx = int(pct * len(sorted_values))
    idx = min(idx, len(sorted_values) - 1)
    return sorted_values[idx]


def _fetch_latency_percentiles(
    conn: sqlite3.Connection,
    community_id: str,
    extra_where: str = "",
    extra_params: tuple[str, ...] = (),
) -> tuple[float | None, float | None]:
    """Fetch p50 and p95 latency for a community with optional filters.

    Args:
        conn: SQLite connection with row_factory set.
        community_id: The community identifier.
        extra_where: Additional SQL WHERE clause (e.g., "AND strftime(...) = ?").
        extra_params: Parameters for the extra WHERE clause.

    Returns:
        Tuple of (p50, p95) duration in ms, or None if no data.
    """
    durations = conn.execute(
        f"""
        SELECT duration_ms
        FROM request_log
        WHERE community_id = ? AND duration_ms IS NOT NULL {extra_where}
        ORDER BY duration_ms
        """,
        (community_id, *extra_params),
    ).fetchall()
    values = [d["duration_ms"] for d in durations]
    return _percentile(values, 0.5), _percentile(values, 0.95)
