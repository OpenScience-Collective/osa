"""Global metrics API endpoints.

Provides cross-community metrics overview and token breakdowns.
Supports both global admin keys (see all) and per-community keys (filtered view).
"""

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.api.security import RequireScopedAuth
from src.metrics.db import get_metrics_connection
from src.metrics.queries import (
    get_community_summary,
    get_overview,
    get_quality_summary,
    get_token_breakdown,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("/overview")
async def metrics_overview(auth: RequireScopedAuth) -> dict[str, Any]:
    """Get cross-community metrics overview.

    Global admin keys see all communities. Per-community keys see only
    their community's data wrapped in the same response format.
    """
    conn = get_metrics_connection()
    try:
        if auth.role == "admin":
            return get_overview(conn)
        # Community-scoped: return summary for just their community
        return get_community_summary(auth.community_id, conn)
    except sqlite3.Error:
        logger.exception("Failed to query metrics database for overview")
        raise HTTPException(
            status_code=503,
            detail="Metrics database is temporarily unavailable.",
        )
    finally:
        conn.close()


@router.get("/tokens")
async def token_breakdown(
    auth: RequireScopedAuth,
    community_id: str | None = Query(default=None, description="Filter by community"),
) -> dict[str, Any]:
    """Get token usage breakdown by model and key source.

    Global admin keys can filter by any community. Per-community keys
    are automatically scoped to their community (community_id parameter ignored).
    """
    # Community-scoped keys always filter to their own community
    effective_community = community_id
    if auth.role == "community":
        effective_community = auth.community_id

    conn = get_metrics_connection()
    try:
        return get_token_breakdown(conn, community_id=effective_community)
    except sqlite3.Error:
        logger.exception("Failed to query metrics database for token breakdown")
        raise HTTPException(
            status_code=503,
            detail="Metrics database is temporarily unavailable.",
        )
    finally:
        conn.close()


@router.get("/quality")
async def quality_overview(auth: RequireScopedAuth) -> dict[str, Any]:
    """Get quality metrics overview.

    Global admin keys see quality for all communities.
    Per-community keys see quality summary for their community only.
    """
    conn = get_metrics_connection()
    try:
        if auth.role == "community":
            return get_quality_summary(auth.community_id, conn)
        # Admin: aggregate quality across all communities
        overview = get_overview(conn)
        communities_data = overview.get("communities", [])
        summaries = []
        for c in communities_data:
            cid = c["community_id"]
            summaries.append(get_quality_summary(cid, conn))
        return {"communities": summaries}
    except sqlite3.Error:
        logger.exception("Failed to query quality metrics")
        raise HTTPException(
            status_code=503,
            detail="Metrics database is temporarily unavailable.",
        )
    finally:
        conn.close()
