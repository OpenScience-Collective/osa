"""Health check endpoints for monitoring community status."""

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException

from src.api.security import RequireAuth
from src.assistants import registry
from src.core.config.community import CommunityConfig

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


def compute_community_health(config: CommunityConfig) -> dict[str, Any]:
    """Compute health status for a single community config.

    Returns:
        Dict with status, api_key, cors_origins, documents, sync_age_hours, warnings.
    """
    warnings: list[str] = []

    # API key status
    api_key_env_var = config.openrouter_api_key_env_var
    if api_key_env_var:
        if os.getenv(api_key_env_var):
            api_key_status = "configured"
        else:
            api_key_status = "missing"
            warnings.append(
                f"Environment variable '{api_key_env_var}' not set; "
                "using shared OSA platform key. This is for demonstration only "
                "and is not sustainable. Requires a dedicated API key and active maintainer."
            )
    else:
        api_key_status = "using_platform"

    # Documentation sources
    doc_count = len(config.documentation) if config.documentation else 0
    if doc_count == 0:
        warnings.append("No documentation sources configured")

    # CORS origins
    cors_count = len(config.cors_origins) if config.cors_origins else 0

    # Determine overall status
    status = "healthy"
    if doc_count == 0 or api_key_status == "missing":
        status = "error"
    elif api_key_status == "using_platform":
        status = "degraded"

    return {
        "status": status,
        "api_key": api_key_status,
        "cors_origins": cors_count,
        "documents": doc_count,
        "sync_age_hours": None,  # TODO: Add sync tracking in future iteration
        "warnings": warnings,
    }


@router.get("/communities")
def get_communities_health(_auth: RequireAuth) -> dict[str, Any]:
    """Get health status for all communities.

    Returns status information for each community including:
    - status: overall health (healthy, degraded, error)
    - api_key: API key status (configured, using_platform, missing)
    - cors_origins: number of CORS origins configured
    - documents: number of documentation sources
    - sync_age_hours: hours since last sync (if applicable)
    - warnings: list of configuration issues

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
                    "warnings": ["Community configuration not found"],
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
                "warnings": [f"Failed to process: {type(e).__name__}"],
            }
            continue

        communities_health[community_id] = compute_community_health(config)

    return communities_health
