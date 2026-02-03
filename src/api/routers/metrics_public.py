"""Public metrics API endpoints.

Exposes non-sensitive community activity data (request counts, error rates,
top tools) without authentication. No tokens, costs, or model information.
"""

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.metrics.db import get_metrics_connection
from src.metrics.queries import (
    get_public_community_summary,
    get_public_overview,
    get_public_usage_stats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics/public", tags=["Public Metrics"])


@router.get("/overview")
async def public_overview() -> dict[str, Any]:
    """Get public metrics overview across all communities.

    Returns total requests, error rate, active community count,
    and per-community request counts. No tokens, costs, or model info.
    """
    conn = get_metrics_connection()
    try:
        return get_public_overview(conn)
    except sqlite3.Error:
        logger.exception("Failed to query metrics database for public overview")
        raise HTTPException(
            status_code=503,
            detail="Metrics database is temporarily unavailable.",
        )
    finally:
        conn.close()


@router.get("/{community_id}")
async def public_community_summary(community_id: str) -> dict[str, Any]:
    """Get public summary for a specific community.

    Returns request counts, error rate, and top tools.
    No tokens, costs, or model info.
    """
    conn = get_metrics_connection()
    try:
        return get_public_community_summary(community_id, conn)
    except sqlite3.Error:
        logger.exception("Failed to query metrics database for community %s", community_id)
        raise HTTPException(
            status_code=503,
            detail="Metrics database is temporarily unavailable.",
        )
    finally:
        conn.close()


@router.get("/{community_id}/usage")
async def public_community_usage(
    community_id: str,
    period: str = Query(
        default="daily",
        pattern="^(daily|weekly|monthly)$",
        description="Time bucket period",
    ),
) -> dict[str, Any]:
    """Get public time-bucketed usage for a community.

    Returns request counts and errors per time bucket.
    No tokens or costs.
    """
    conn = get_metrics_connection()
    try:
        return get_public_usage_stats(community_id, period, conn)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except sqlite3.Error:
        logger.exception("Failed to query metrics database for community %s usage", community_id)
        raise HTTPException(
            status_code=503,
            detail="Metrics database is temporarily unavailable.",
        )
    finally:
        conn.close()
