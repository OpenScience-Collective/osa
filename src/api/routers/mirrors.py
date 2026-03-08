"""API endpoints for ephemeral database mirror management.

Mirrors allow developers to create short-lived copies of community knowledge
databases for development and testing. BYOK users are rate-limited to a
maximum number of concurrent mirrors per user; requests without an owner
identifier are only subject to the global mirror cap.
"""

import asyncio
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from src.api.security import RequireAuth
from src.knowledge.db import reset_active_mirror, set_active_mirror
from src.knowledge.mirror import (
    MirrorInfo,
    create_mirror,
    delete_mirror,
    get_mirror,
    list_mirrors,
    refresh_mirror,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mirrors", tags=["Mirrors"])


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class CreateMirrorRequest(BaseModel):
    """Request body for creating a new mirror."""

    community_ids: list[str] = Field(
        ..., min_length=1, description="Community IDs to include in the mirror"
    )
    ttl_hours: int = Field(
        default=48, ge=1, le=168, description="Hours until the mirror expires (1-168)"
    )
    label: str | None = Field(
        default=None, max_length=128, description="Human-readable label for the mirror"
    )

    @field_validator("community_ids")
    @classmethod
    def validate_community_ids(cls, v: list[str]) -> list[str]:
        for cid in v:
            if not cid or not cid.replace("-", "").replace("_", "").isalnum():
                raise ValueError(f"Invalid community ID: {cid!r}")
        return v


class MirrorResponse(BaseModel):
    """Mirror metadata in API responses."""

    mirror_id: str
    community_ids: list[str]
    created_at: str
    expires_at: str
    owner_id: str | None = None
    label: str | None = None
    size_bytes: int = 0
    expired: bool = False

    @classmethod
    def from_info(cls, info: MirrorInfo) -> "MirrorResponse":
        return cls(
            mirror_id=info.mirror_id,
            community_ids=info.community_ids,
            created_at=info.created_at,
            expires_at=info.expires_at,
            owner_id=info.owner_id,
            label=info.label,
            size_bytes=info.size_bytes,
            expired=info.is_expired(),
        )


class RefreshMirrorRequest(BaseModel):
    """Request body for refreshing a mirror."""

    community_ids: list[str] | None = Field(
        default=None, description="Specific communities to refresh, or null for all"
    )


SyncType = Literal["github", "papers", "docstrings", "mailman", "faq", "beps", "all"]


class MirrorSyncRequest(BaseModel):
    """Request body for syncing data into a mirror."""

    sync_type: SyncType = Field(
        default="all",
        description="Sync type: github, papers, docstrings, mailman, faq, beps, or all",
    )


class MirrorSyncResponse(BaseModel):
    """Response from a mirror sync operation."""

    success: bool
    message: str
    items_synced: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_user_id(x_user_id: str | None) -> str | None:
    """Extract user ID from header for ownership tracking."""
    return x_user_id if x_user_id else None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MirrorResponse)
async def create_mirror_endpoint(
    body: CreateMirrorRequest,
    _auth: RequireAuth,
    x_user_id: Annotated[str | None, Header()] = None,
) -> MirrorResponse:
    """Create a new ephemeral database mirror.

    Copies the specified community databases into a new mirror directory.
    BYOK users are subject to per-user mirror limits.
    """
    user_id = _get_user_id(x_user_id)

    try:
        info = create_mirror(
            community_ids=body.community_ids,
            ttl_hours=body.ttl_hours,
            label=body.label,
            owner_id=user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    logger.info(
        "Mirror created: %s (communities=%s, owner=%s)",
        info.mirror_id,
        info.community_ids,
        user_id,
    )
    return MirrorResponse.from_info(info)


@router.get("", response_model=list[MirrorResponse])
async def list_mirrors_endpoint(
    _auth: RequireAuth,
) -> list[MirrorResponse]:
    """List all active (non-expired) mirrors."""
    mirrors = list_mirrors()
    active = [m for m in mirrors if not m.is_expired()]
    return [MirrorResponse.from_info(m) for m in active]


@router.get("/{mirror_id}", response_model=MirrorResponse)
async def get_mirror_endpoint(
    mirror_id: str,
    _auth: RequireAuth,
) -> MirrorResponse:
    """Get metadata for a specific mirror."""
    info = get_mirror(mirror_id)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mirror '{mirror_id}' not found",
        )
    return MirrorResponse.from_info(info)


@router.delete("/{mirror_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mirror_endpoint(
    mirror_id: str,
    _auth: RequireAuth,
) -> None:
    """Delete a mirror and all its databases."""
    if not delete_mirror(mirror_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mirror '{mirror_id}' not found",
        )
    logger.info("Mirror deleted via API: %s", mirror_id)


@router.post("/{mirror_id}/refresh", response_model=MirrorResponse)
async def refresh_mirror_endpoint(
    mirror_id: str,
    body: RefreshMirrorRequest,
    _auth: RequireAuth,
) -> MirrorResponse:
    """Re-copy production databases into an existing mirror.

    Resets the mirror's data to match current production state.
    """
    try:
        info = refresh_mirror(mirror_id, community_ids=body.community_ids)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    logger.info("Mirror refreshed via API: %s", mirror_id)
    return MirrorResponse.from_info(info)


@router.post("/{mirror_id}/sync", response_model=MirrorSyncResponse)
async def sync_mirror_endpoint(
    mirror_id: str,
    body: MirrorSyncRequest,
    _auth: RequireAuth,
) -> MirrorSyncResponse:
    """Run sync pipeline against a mirror's databases.

    Sets the mirror context so all sync operations write to the mirror's
    databases instead of production. Supports sync types: github, papers,
    docstrings, mailman, faq, beps, or all.
    """
    info = get_mirror(mirror_id)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mirror '{mirror_id}' not found",
        )
    if info.is_expired():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Mirror '{mirror_id}' has expired",
        )

    # Set the mirror context so all DB operations go to the mirror
    token = set_active_mirror(mirror_id)
    try:
        from src.api.scheduler import run_sync_now

        results = await asyncio.to_thread(run_sync_now, body.sync_type)
        total = sum(results.values())
        return MirrorSyncResponse(
            success=True,
            message=f"Sync completed: {total} items synced into mirror {mirror_id}",
            items_synced=results,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        logger.error("Mirror sync failed for %s: %s", mirror_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Sync operation failed. Check server logs for details.",
        ) from e
    finally:
        reset_active_mirror(token)


@router.get("/{mirror_id}/download/{community_id}")
async def download_mirror_db(
    mirror_id: str,
    community_id: str,
    _auth: RequireAuth,
) -> Any:
    """Download a community database file from a mirror.

    Returns the SQLite file for local development use.
    """
    from fastapi.responses import FileResponse

    info = get_mirror(mirror_id)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mirror '{mirror_id}' not found",
        )
    if info.is_expired():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Mirror '{mirror_id}' has expired",
        )
    if not community_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid community ID format",
        )
    if community_id not in info.community_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Community '{community_id}' not found in mirror '{mirror_id}'",
        )

    from src.knowledge.mirror import _get_mirror_dir

    db_path = _get_mirror_dir(mirror_id) / f"{community_id}.db"
    if not db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Database file not found for community '{community_id}'",
        )

    return FileResponse(
        path=str(db_path),
        media_type="application/x-sqlite3",
        filename=f"{community_id}.db",
    )
