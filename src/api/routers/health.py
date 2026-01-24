"""Health check endpoints for monitoring community status."""

from typing import Any

from fastapi import APIRouter

from src.assistants import registry

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/communities")
def get_communities_health() -> dict[str, Any]:
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

    for assistant_info in registry.list_all():
        community_id = assistant_info.id
        config = assistant_info.community_config

        # Skip if no config available
        if not config:
            communities_health[community_id] = {
                "status": "error",
                "api_key": "missing",
                "cors_origins": 0,
                "documents": 0,
                "sync_age_hours": None,
            }
            continue

        # Determine API key status
        api_key_status = "configured" if config.openrouter_api_key_env_var else "using_platform"

        # Count documentation sources
        doc_count = len(config.documentation) if config.documentation else 0

        # Count CORS origins
        cors_count = len(config.cors_origins) if config.cors_origins else 0

        # Sync age is not tracked yet, set to None
        # TODO: Add sync tracking in future iteration
        sync_age_hours = None

        # Determine overall status
        # - error: critical issues (no docs)
        # - degraded: warnings (using platform key)
        # - healthy: all good
        status = "healthy"

        if doc_count == 0:
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
