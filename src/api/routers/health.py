"""Health check endpoints for monitoring community status."""

import logging
from typing import Any

from fastapi import APIRouter

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
        logger.error(f"Failed to list assistants from registry: {e}")
        return communities_health

    for assistant_info in assistants:
        try:
            community_id = getattr(assistant_info, "id", None)
            if not community_id:
                logger.warning("Assistant info missing 'id' attribute, skipping")
                continue

            config = getattr(assistant_info, "community_config", None)

            # Skip if no config available
            if not config:
                logger.warning(f"Community {community_id} missing configuration")
                communities_health[community_id] = {
                    "status": "error",
                    "api_key": "missing",
                    "cors_origins": 0,
                    "documents": 0,
                    "sync_age_hours": None,
                }
                continue
        except Exception as e:
            logger.error(f"Failed to process assistant info: {e}")
            continue

        # Determine API key status
        import os

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
