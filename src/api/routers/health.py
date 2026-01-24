"""Health check endpoints for monitoring community status."""

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException

from src.api.security import RequireAuth
from src.assistants import registry

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/communities")
def get_communities_health(_auth: RequireAuth) -> dict[str, Any]:
    """Get health status for all communities.

    Returns status information for each community including:
    - status: overall health (healthy, degraded, error)
    - api_key: API key status (configured, using_platform, missing)
    - cors_origins: number of CORS origins configured
    - documents: number of documentation sources
    - sync_age_hours: hours since last sync (if applicable)

    Returns:
        Dictionary mapping community IDs to their health status.
    """
    communities_health: dict[str, Any] = {}

    try:
        assistants = registry.list_all()
    except Exception as e:
        logger.error(
            "Failed to list assistants from registry: %s",
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Service unavailable: unable to access community registry. Please try again later.",
        ) from e

    for assistant_info in assistants:
        try:
            community_id = getattr(assistant_info, "id", None)
            if not community_id:
                logger.warning("Assistant info missing 'id' attribute, skipping")
                continue

            config = getattr(assistant_info, "community_config", None)

            # Skip if no config available
            if not config:
                logger.warning("Community %s missing configuration", community_id)
                communities_health[community_id] = {
                    "status": "error",
                    "api_key": "missing",
                    "cors_origins": 0,
                    "documents": 0,
                    "sync_age_hours": None,
                }
                continue
        except (AttributeError, KeyError, TypeError) as e:
            logger.error(
                "Failed to process community health for %s: %s",
                community_id if "community_id" in locals() else "unknown",
                e,
                exc_info=True,
                extra={
                    "error_type": type(e).__name__,
                    "community_id": community_id if "community_id" in locals() else None,
                },
            )
            # Include failed community in response with error status
            fallback_id = (
                community_id
                if "community_id" in locals()
                else f"unknown_{assistants.index(assistant_info)}"
            )
            communities_health[fallback_id] = {
                "status": "error",
                "error": f"Failed to process: {type(e).__name__}",
                "api_key": "unknown",
                "cors_origins": 0,
                "documents": 0,
                "sync_age_hours": None,
            }
            continue

        # Determine API key status
        api_key_env_var = getattr(config, "openrouter_api_key_env_var", None)
        if api_key_env_var:
            # Check if env var is actually set
            api_key_status = "configured" if os.getenv(api_key_env_var) else "missing"
        else:
            api_key_status = "using_platform"

        # Count documentation sources
        documentation = getattr(config, "documentation", None)
        doc_count = len(documentation) if documentation else 0

        # Count CORS origins
        cors_origins = getattr(config, "cors_origins", None)
        cors_count = len(cors_origins) if cors_origins else 0

        # Sync age is not tracked yet, set to None
        # TODO: Add sync tracking in future iteration
        sync_age_hours = None

        # Determine overall status
        # - error: critical issues (no docs, missing configured API key)
        # - degraded: warnings (using platform key)
        # - healthy: all good
        status = "healthy"

        if doc_count == 0 or api_key_status == "missing":
            status = "error"
        elif api_key_status == "using_platform":
            status = "degraded"

        communities_health[community_id] = {
            "status": status,
            "api_key": api_key_status,
            "cors_origins": cors_count,
            "documents": doc_count,
            "sync_age_hours": sync_age_hours,
        }

    return communities_health
