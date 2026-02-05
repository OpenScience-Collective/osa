"""Public metrics API endpoints.

Exposes non-sensitive aggregate metrics (request counts, error rates)
without authentication. No tokens, costs, or model information.

Per-community public metrics are served from the community router
at /{community_id}/metrics/public.
"""

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from src.assistants import registry
from src.metrics.db import metrics_connection
from src.metrics.queries import get_public_overview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics/public", tags=["Public Metrics"])


@router.get("/overview")
async def public_overview() -> dict[str, Any]:
    """Get public metrics overview across all communities.

    Returns total requests, error rate, active community count,
    and per-community request counts. No tokens, costs, or model info.
    All registered communities appear even if they have zero requests.
    """
    registered = [info.id for info in registry.list_available()]
    try:
        with metrics_connection() as conn:
            return get_public_overview(conn, registered_communities=registered)
    except sqlite3.Error:
        logger.exception("Failed to query metrics database for public overview")
        raise HTTPException(
            status_code=503,
            detail="Metrics database is temporarily unavailable.",
        )
