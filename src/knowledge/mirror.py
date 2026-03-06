"""Ephemeral database mirror lifecycle management.

Mirrors are short-lived copies of community knowledge databases that allow
developers to read, write, and re-sync without affecting production data.
Each mirror gets its own directory under data/mirrors/{mirror_id}/ containing
copies of the relevant community SQLite databases.
"""

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from src.cli.config import get_data_dir

logger = logging.getLogger(__name__)

MIRRORS_DIR_NAME = "mirrors"
METADATA_FILE = "_metadata.json"

# Resource limits (can be overridden via settings)
DEFAULT_TTL_HOURS = 48
MAX_TTL_HOURS = 168  # 7 days
MAX_MIRRORS_TOTAL = 50
MAX_MIRRORS_PER_USER = 2


@dataclass
class MirrorInfo:
    """Metadata for an ephemeral database mirror."""

    mirror_id: str
    community_ids: list[str]
    created_at: str
    expires_at: str
    owner_id: str | None = None
    label: str | None = None
    size_bytes: int = 0

    def is_expired(self) -> bool:
        """Check if the mirror has passed its expiration time."""
        expires = datetime.fromisoformat(self.expires_at)
        return datetime.now(UTC) >= expires

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON storage."""
        return {
            "mirror_id": self.mirror_id,
            "community_ids": self.community_ids,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "owner_id": self.owner_id,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MirrorInfo":
        """Deserialize from dictionary."""
        return cls(
            mirror_id=data["mirror_id"],
            community_ids=data["community_ids"],
            created_at=data["created_at"],
            expires_at=data["expires_at"],
            owner_id=data.get("owner_id"),
            label=data.get("label"),
        )


def _get_mirrors_dir() -> Path:
    """Get the base directory for all mirrors."""
    return get_data_dir() / MIRRORS_DIR_NAME


def _get_mirror_dir(mirror_id: str) -> Path:
    """Get the directory for a specific mirror."""
    return _get_mirrors_dir() / mirror_id


def _get_metadata_path(mirror_id: str) -> Path:
    """Get the path to a mirror's metadata file."""
    return _get_mirror_dir(mirror_id) / METADATA_FILE


def _write_metadata(info: MirrorInfo) -> None:
    """Write mirror metadata to disk."""
    path = _get_metadata_path(info.mirror_id)
    path.write_text(json.dumps(info.to_dict(), indent=2))


def _read_metadata(mirror_id: str) -> MirrorInfo | None:
    """Read mirror metadata from disk."""
    path = _get_metadata_path(mirror_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    info = MirrorInfo.from_dict(data)
    info.size_bytes = _calculate_mirror_size(mirror_id)
    return info


def _calculate_mirror_size(mirror_id: str) -> int:
    """Calculate total size of database files in a mirror."""
    mirror_dir = _get_mirror_dir(mirror_id)
    if not mirror_dir.exists():
        return 0
    return sum(f.stat().st_size for f in mirror_dir.glob("*.db") if f.is_file())


def _get_production_db_path(community_id: str) -> Path:
    """Get the path to a production community database."""
    return get_data_dir() / "knowledge" / f"{community_id}.db"


def create_mirror(
    community_ids: list[str],
    ttl_hours: int = DEFAULT_TTL_HOURS,
    label: str | None = None,
    owner_id: str | None = None,
) -> MirrorInfo:
    """Create a new mirror by copying production database files.

    Args:
        community_ids: List of community IDs to include in the mirror.
        ttl_hours: Hours until the mirror expires (default 48, max 168).
        label: Optional human-readable label for the mirror.
        owner_id: Optional owner identifier (user_id) for rate limiting.

    Returns:
        MirrorInfo with the new mirror's metadata.

    Raises:
        ValueError: If no valid community databases found or limits exceeded.
    """
    ttl_hours = min(ttl_hours, MAX_TTL_HOURS)

    # Check total mirror count
    existing = list_mirrors()
    active_mirrors = [m for m in existing if not m.is_expired()]
    if len(active_mirrors) >= MAX_MIRRORS_TOTAL:
        raise ValueError(
            f"Maximum number of mirrors ({MAX_MIRRORS_TOTAL}) reached. "
            "Delete existing mirrors or wait for them to expire."
        )

    # Check per-user limit
    if owner_id:
        user_mirrors = [m for m in active_mirrors if m.owner_id == owner_id]
        if len(user_mirrors) >= MAX_MIRRORS_PER_USER:
            raise ValueError(
                f"Maximum mirrors per user ({MAX_MIRRORS_PER_USER}) reached. "
                "Delete an existing mirror first."
            )

    mirror_id = uuid4().hex[:12]
    mirror_dir = _get_mirror_dir(mirror_id)
    mirror_dir.mkdir(parents=True, exist_ok=True)

    copied_communities = []
    for community_id in community_ids:
        source_db = _get_production_db_path(community_id)
        if not source_db.exists():
            logger.warning("No database found for community '%s', skipping", community_id)
            continue
        dest_db = mirror_dir / f"{community_id}.db"
        shutil.copy2(str(source_db), str(dest_db))
        copied_communities.append(community_id)
        logger.info("Copied %s to mirror %s", community_id, mirror_id)

    if not copied_communities:
        # Clean up empty directory
        shutil.rmtree(str(mirror_dir), ignore_errors=True)
        raise ValueError(
            f"No databases found for communities: {community_ids}. "
            "Ensure the communities exist and have been synced."
        )

    now = datetime.now(UTC)
    info = MirrorInfo(
        mirror_id=mirror_id,
        community_ids=copied_communities,
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=ttl_hours)).isoformat(),
        owner_id=owner_id,
        label=label,
        size_bytes=_calculate_mirror_size(mirror_id),
    )
    _write_metadata(info)

    logger.info(
        "Created mirror %s with communities %s (expires %s)",
        mirror_id,
        copied_communities,
        info.expires_at,
    )
    return info


def get_mirror(mirror_id: str) -> MirrorInfo | None:
    """Get mirror metadata.

    Returns None if the mirror does not exist.
    Does NOT check expiration; callers should check is_expired().
    """
    return _read_metadata(mirror_id)


def list_mirrors() -> list[MirrorInfo]:
    """List all mirrors (including expired ones still on disk)."""
    mirrors_dir = _get_mirrors_dir()
    if not mirrors_dir.exists():
        return []

    result = []
    for entry in mirrors_dir.iterdir():
        if entry.is_dir() and (entry / METADATA_FILE).exists():
            info = _read_metadata(entry.name)
            if info:
                result.append(info)

    result.sort(key=lambda m: m.created_at, reverse=True)
    return result


def delete_mirror(mirror_id: str) -> bool:
    """Delete a mirror and all its databases.

    Returns True if the mirror was deleted, False if not found.
    """
    mirror_dir = _get_mirror_dir(mirror_id)
    if not mirror_dir.exists():
        return False

    shutil.rmtree(str(mirror_dir))
    logger.info("Deleted mirror %s", mirror_id)
    return True


def refresh_mirror(
    mirror_id: str,
    community_ids: list[str] | None = None,
) -> MirrorInfo:
    """Re-copy production databases into an existing mirror.

    This resets the mirror's data to match current production.

    Args:
        mirror_id: ID of the mirror to refresh.
        community_ids: Specific communities to refresh, or None for all.

    Returns:
        Updated MirrorInfo.

    Raises:
        ValueError: If mirror not found or expired.
    """
    info = get_mirror(mirror_id)
    if not info:
        raise ValueError(f"Mirror '{mirror_id}' not found")
    if info.is_expired():
        raise ValueError(f"Mirror '{mirror_id}' has expired")

    targets = community_ids or info.community_ids
    mirror_dir = _get_mirror_dir(mirror_id)

    for community_id in targets:
        source_db = _get_production_db_path(community_id)
        if not source_db.exists():
            logger.warning("No production database for '%s', skipping refresh", community_id)
            continue
        dest_db = mirror_dir / f"{community_id}.db"
        shutil.copy2(str(source_db), str(dest_db))
        logger.info("Refreshed %s in mirror %s", community_id, mirror_id)

    # Update size in metadata
    info.size_bytes = _calculate_mirror_size(mirror_id)
    return info


def cleanup_expired_mirrors() -> int:
    """Delete all expired mirrors. Returns the count of mirrors deleted."""
    deleted = 0
    for info in list_mirrors():
        if info.is_expired() and delete_mirror(info.mirror_id):
            deleted += 1
    if deleted:
        logger.info("Cleaned up %d expired mirrors", deleted)
    return deleted
