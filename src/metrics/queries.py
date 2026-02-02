"""Aggregation queries for the metrics database.

Provides summary statistics, usage breakdowns, and overview queries
for both per-community and cross-community metrics.
"""

import json
import sqlite3
from typing import Any


def get_community_summary(community_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
    """Get summary statistics for a single community.

    Args:
        community_id: The community identifier.
        conn: SQLite connection (with row_factory=sqlite3.Row).

    Returns:
        Dict with total_requests, total_tokens, avg_duration_ms,
        error_rate, top_models, top_tools.
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

    # Top tools (from JSON array in tools_called column)
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
            pass
    top_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:5]

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
        "top_tools": [{"tool": t[0], "count": t[1]} for t in top_tools],
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
    # SQLite strftime patterns for bucketing
    format_map = {
        "daily": "%Y-%m-%d",
        "weekly": "%Y-W%W",
        "monthly": "%Y-%m",
    }
    if period not in format_map:
        raise ValueError(f"Invalid period: {period}. Must be one of: daily, weekly, monthly")

    fmt = format_map[period]

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
