"""Global metrics API endpoints.

Provides cross-community metrics overview and token breakdowns.
All endpoints require admin authentication.
"""

import logging
from typing import Any

from fastapi import APIRouter, Query

from src.api.security import RequireAdminAuth
from src.metrics.db import get_metrics_connection
from src.metrics.queries import get_overview, get_token_breakdown

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("/overview")
async def metrics_overview(_auth: RequireAdminAuth) -> dict[str, Any]:
    """Get cross-community metrics overview.

    Returns total requests, tokens, average duration, error rate,
    and per-community breakdown.
    """
    conn = get_metrics_connection()
    try:
        return get_overview(conn)
    finally:
        conn.close()


@router.get("/tokens")
async def token_breakdown(
    _auth: RequireAdminAuth,
    community_id: str | None = Query(default=None, description="Filter by community"),
) -> dict[str, Any]:
    """Get token usage breakdown by model and key source.

    Optionally filter by community_id.
    """
    conn = get_metrics_connection()
    try:
        return get_token_breakdown(conn, community_id=community_id)
    finally:
        conn.close()
