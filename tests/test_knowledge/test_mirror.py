"""Tests for ephemeral database mirror system.

Tests cover:
- Mirror CRUD lifecycle (create, get, list, delete)
- ContextVar-based DB routing (get_db_path returns mirror path when set)
- active_mirror_context context manager (set/reset, exception safety)
- MirrorInfo invariants (frozen dataclass, validation, serialization)
- Mirror refresh (re-copy from production)
- TTL expiration, clamping, and cleanup
- Resource limits (max mirrors, per-user limits)
- Path traversal prevention
- Corrupt metadata resilience
- run_sync_now input validation
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import (
    get_active_mirror,
    get_db_path,
    init_db,
    reset_active_mirror,
    set_active_mirror,
)
from src.knowledge.mirror import (
    CorruptMirrorError,
    MirrorInfo,
    _get_metadata_path,
    _validate_mirror_id,
    cleanup_expired_mirrors,
    create_mirror,
    delete_mirror,
    get_mirror,
    list_mirrors,
    refresh_mirror,
)


@pytest.fixture
def data_dir(tmp_path: Path):
    """Set up a temporary data directory with a production database."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    # Create a small production database for "testcommunity"
    with (
        patch("src.cli.config.get_data_dir", return_value=tmp_path),
        patch("src.knowledge.db.get_data_dir", return_value=tmp_path),
    ):
        init_db("testcommunity")
        assert (knowledge_dir / "testcommunity.db").exists()

    yield tmp_path


@pytest.fixture(autouse=True)
def patch_data_dir(data_dir: Path):
    """Patch get_data_dir for all tests to use the temp directory."""
    with (
        patch("src.cli.config.get_data_dir", return_value=data_dir),
        patch("src.knowledge.db.get_data_dir", return_value=data_dir),
        patch("src.knowledge.mirror.get_data_dir", return_value=data_dir),
    ):
        # Ensure no mirror context leaks between tests
        token = set_active_mirror(None)
        try:
            yield data_dir
        finally:
            reset_active_mirror(token)


class TestContextVar:
    """Tests for the ContextVar-based mirror routing."""

    def test_get_db_path_default(self):
        """Without mirror context, returns production path."""
        path = get_db_path("testcommunity")
        assert "knowledge" in str(path)
        assert "mirrors" not in str(path)
        assert path.name == "testcommunity.db"

    def test_get_db_path_with_mirror(self):
        """With mirror context set, returns mirror path."""
        token = set_active_mirror("abc123")
        try:
            path = get_db_path("testcommunity")
            assert "mirrors" in str(path)
            assert "abc123" in str(path)
            assert path.name == "testcommunity.db"
        finally:
            reset_active_mirror(token)

    def test_get_db_path_resets_after_token(self):
        """After resetting the token, returns production path again."""
        token = set_active_mirror("abc123")
        path_mirror = get_db_path("testcommunity")
        assert "mirrors" in str(path_mirror)

        reset_active_mirror(token)
        path_prod = get_db_path("testcommunity")
        assert "knowledge" in str(path_prod)
        assert "mirrors" not in str(path_prod)

    def test_get_active_mirror_default_none(self):
        """Default mirror context is None."""
        assert get_active_mirror() is None

    def test_set_and_get_active_mirror(self):
        """set/get active mirror round-trip."""
        token = set_active_mirror("test123")
        try:
            assert get_active_mirror() == "test123"
        finally:
            reset_active_mirror(token)
        assert get_active_mirror() is None

    def test_invalid_mirror_id_rejected(self):
        """Mirror IDs with path traversal chars are rejected at set time."""
        with pytest.raises(ValueError, match="Invalid mirror ID"):
            set_active_mirror("../etc/passwd")


class TestMirrorLifecycle:
    """Tests for creating, listing, and deleting mirrors."""

    def test_create_mirror(self):
        """Create a mirror and verify it copies the database."""
        info = create_mirror(community_ids=["testcommunity"], ttl_hours=24)
        assert info.mirror_id
        assert "testcommunity" in info.community_ids
        assert info.size_bytes > 0
        assert not info.is_expired()

        assert info.created_at
        assert info.expires_at

    def test_create_mirror_with_label(self):
        """Create a mirror with a label."""
        info = create_mirror(
            community_ids=["testcommunity"],
            label="test-prompt-v2",
        )
        assert info.label == "test-prompt-v2"

    def test_create_mirror_with_owner(self):
        """Create a mirror with an owner ID."""
        info = create_mirror(
            community_ids=["testcommunity"],
            owner_id="user123",
        )
        assert info.owner_id == "user123"

    def test_create_mirror_nonexistent_community(self):
        """Creating a mirror for a nonexistent community raises ValueError."""
        with pytest.raises(ValueError, match="No databases found"):
            create_mirror(community_ids=["nonexistent"])

    def test_get_mirror(self):
        """Get mirror by ID returns correct metadata."""
        info = create_mirror(community_ids=["testcommunity"])
        retrieved = get_mirror(info.mirror_id)
        assert retrieved is not None
        assert retrieved.mirror_id == info.mirror_id
        assert retrieved.community_ids == info.community_ids

    def test_get_mirror_nonexistent(self):
        """Getting a nonexistent mirror returns None."""
        assert get_mirror("nonexistent") is None

    def test_list_mirrors(self):
        """List mirrors returns all created mirrors."""
        info1 = create_mirror(community_ids=["testcommunity"], label="first")
        info2 = create_mirror(community_ids=["testcommunity"], label="second")

        mirrors = list_mirrors()
        ids = [m.mirror_id for m in mirrors]
        assert info1.mirror_id in ids
        assert info2.mirror_id in ids

    def test_delete_mirror(self):
        """Delete a mirror removes it from disk."""
        info = create_mirror(community_ids=["testcommunity"])
        assert get_mirror(info.mirror_id) is not None

        result = delete_mirror(info.mirror_id)
        assert result is True
        assert get_mirror(info.mirror_id) is None

    def test_delete_nonexistent_mirror(self):
        """Deleting a nonexistent mirror returns False."""
        assert delete_mirror("nonexistent") is False


class TestMirrorRefresh:
    """Tests for refreshing mirror data from production."""

    def test_refresh_mirror(self):
        """Refresh re-copies production databases."""
        info = create_mirror(community_ids=["testcommunity"])
        refreshed = refresh_mirror(info.mirror_id)
        assert refreshed.mirror_id == info.mirror_id
        assert refreshed.size_bytes > 0

    def test_refresh_expired_mirror(self):
        """Refreshing an expired mirror raises ValueError."""
        info = create_mirror(community_ids=["testcommunity"], ttl_hours=1)

        # Manually expire the mirror
        meta_path = _get_metadata_path(info.mirror_id)
        meta = json.loads(meta_path.read_text())
        meta["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        meta_path.write_text(json.dumps(meta))

        with pytest.raises(ValueError, match="has expired"):
            refresh_mirror(info.mirror_id)

    def test_refresh_nonexistent_mirror(self):
        """Refreshing a nonexistent mirror raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            refresh_mirror("nonexistent")


class TestTTLAndCleanup:
    """Tests for mirror expiration and cleanup."""

    def test_mirror_not_expired(self):
        """Newly created mirror is not expired."""
        info = create_mirror(community_ids=["testcommunity"], ttl_hours=24)
        assert not info.is_expired()

    def test_mirror_expired(self):
        """Mirror with past expiration is expired."""
        info = MirrorInfo(
            mirror_id="test",
            community_ids=("testcommunity",),
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        assert info.is_expired()

    def test_cleanup_removes_expired(self):
        """cleanup_expired_mirrors removes expired mirrors."""
        info = create_mirror(community_ids=["testcommunity"], ttl_hours=1)

        # Manually expire the mirror
        meta_path = _get_metadata_path(info.mirror_id)
        meta = json.loads(meta_path.read_text())
        meta["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        meta_path.write_text(json.dumps(meta))

        deleted = cleanup_expired_mirrors()
        assert deleted == 1
        assert get_mirror(info.mirror_id) is None

    def test_cleanup_preserves_active(self):
        """cleanup_expired_mirrors preserves non-expired mirrors."""
        info = create_mirror(community_ids=["testcommunity"], ttl_hours=48)
        deleted = cleanup_expired_mirrors()
        assert deleted == 0
        assert get_mirror(info.mirror_id) is not None


class TestResourceLimits:
    """Tests for mirror resource limits."""

    def test_per_user_limit(self):
        """BYOK users are limited to MAX_MIRRORS_PER_USER mirrors."""
        from src.knowledge.mirror import MAX_MIRRORS_PER_USER

        # Create max mirrors for user
        for i in range(MAX_MIRRORS_PER_USER):
            create_mirror(
                community_ids=["testcommunity"],
                owner_id="user1",
                label=f"mirror-{i}",
            )

        # Next one should fail
        with pytest.raises(ValueError, match="Maximum mirrors per user"):
            create_mirror(
                community_ids=["testcommunity"],
                owner_id="user1",
            )

    def test_different_users_independent(self):
        """Different users have independent mirror limits."""
        from src.knowledge.mirror import MAX_MIRRORS_PER_USER

        for _i in range(MAX_MIRRORS_PER_USER):
            create_mirror(community_ids=["testcommunity"], owner_id="user1")

        # Different user should still be able to create mirrors
        info = create_mirror(community_ids=["testcommunity"], owner_id="user2")
        assert info.owner_id == "user2"

    def test_no_owner_no_per_user_limit(self):
        """Mirrors without owner_id (admin) are not subject to per-user limits."""
        from src.knowledge.mirror import MAX_MIRRORS_PER_USER

        for _i in range(MAX_MIRRORS_PER_USER + 1):
            info = create_mirror(community_ids=["testcommunity"])
            assert info.owner_id is None


class TestPathTraversal:
    """Tests for path traversal prevention in mirror IDs."""

    def test_empty_mirror_id_rejected(self):
        """Empty string mirror ID is rejected."""
        with pytest.raises(ValueError, match="Invalid mirror ID length"):
            _validate_mirror_id("")

    def test_dots_only_rejected(self):
        """Mirror ID of just dots is rejected."""
        with pytest.raises(ValueError, match="Invalid mirror ID"):
            _validate_mirror_id("..")

    def test_single_dot_rejected(self):
        """Mirror ID of a single dot is rejected."""
        with pytest.raises(ValueError, match="Invalid mirror ID"):
            _validate_mirror_id(".")

    def test_backslash_traversal_rejected(self):
        """Mirror ID with backslash path traversal is rejected."""
        with pytest.raises(ValueError, match="Invalid mirror ID"):
            _validate_mirror_id("..\\etc\\passwd")

    def test_slash_traversal_rejected(self):
        """Mirror ID with forward slash is rejected."""
        with pytest.raises(ValueError, match="Invalid mirror ID"):
            _validate_mirror_id("../etc/passwd")

    def test_too_long_mirror_id_rejected(self):
        """Mirror ID exceeding 64 chars is rejected."""
        with pytest.raises(ValueError, match="Invalid mirror ID length"):
            _validate_mirror_id("a" * 65)

    def test_valid_mirror_id_accepted(self):
        """Valid alphanumeric mirror ID passes validation."""
        _validate_mirror_id("abc123def456")
        _validate_mirror_id("mirror-1_test")

    def test_delete_mirror_validates_id(self):
        """delete_mirror rejects path traversal mirror IDs."""
        with pytest.raises(ValueError, match="Invalid mirror ID"):
            delete_mirror("../../etc")

    def test_get_mirror_validates_id(self):
        """get_mirror rejects path traversal mirror IDs."""
        with pytest.raises(ValueError, match="Invalid mirror ID"):
            get_mirror("../../etc")


class TestCorruptMetadata:
    """Tests for resilience against corrupt metadata files."""

    def test_corrupt_json_raises_error(self, data_dir: Path):
        """Corrupt JSON metadata raises CorruptMirrorError."""
        mirror_dir = data_dir / "mirrors" / "corrupt123"
        mirror_dir.mkdir(parents=True)
        (mirror_dir / "_metadata.json").write_text("not valid json{{{")

        with pytest.raises(CorruptMirrorError, match="corrupt metadata"):
            get_mirror("corrupt123")

    def test_missing_keys_raises_error(self, data_dir: Path):
        """Metadata with missing required keys raises CorruptMirrorError."""
        mirror_dir = data_dir / "mirrors" / "missingkeys"
        mirror_dir.mkdir(parents=True)
        (mirror_dir / "_metadata.json").write_text('{"mirror_id": "missingkeys"}')

        with pytest.raises(CorruptMirrorError, match="corrupt metadata"):
            get_mirror("missingkeys")

    def test_corrupt_metadata_does_not_break_list(self, data_dir: Path):
        """One corrupt metadata file does not break list_mirrors."""
        # Create a valid mirror
        info = create_mirror(community_ids=["testcommunity"], label="valid")

        # Create a corrupt mirror directory
        corrupt_dir = data_dir / "mirrors" / "corrupt456"
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / "_metadata.json").write_text("garbage data")

        mirrors = list_mirrors()
        ids = [m.mirror_id for m in mirrors]
        assert info.mirror_id in ids

    def test_corrupt_metadata_does_not_break_cleanup(self, data_dir: Path):
        """Corrupt metadata does not crash cleanup_expired_mirrors."""
        corrupt_dir = data_dir / "mirrors" / "corrupt789"
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / "_metadata.json").write_text("not json")

        # Should not raise
        deleted = cleanup_expired_mirrors()
        assert deleted == 0


class TestCreateMirrorCleanup:
    """Tests for create_mirror error handling and cleanup."""

    def test_create_mirror_partial_communities(self):
        """Creating with mix of valid and invalid communities only copies valid ones."""
        info = create_mirror(community_ids=["testcommunity", "nonexistent"])
        assert info.community_ids == ("testcommunity",)
        assert "nonexistent" not in info.community_ids


class TestActiveMirrorContext:
    """Tests for the active_mirror_context context manager."""

    def test_context_manager_sets_and_resets(self):
        """Context manager sets mirror ID and resets it after the block."""
        from src.knowledge.db import active_mirror_context

        assert get_active_mirror() is None
        with active_mirror_context("abc123"):
            assert get_active_mirror() == "abc123"
        assert get_active_mirror() is None

    def test_context_manager_resets_on_exception(self):
        """Context manager resets mirror ID even if an exception occurs."""
        from src.knowledge.db import active_mirror_context

        assert get_active_mirror() is None
        with pytest.raises(RuntimeError), active_mirror_context("abc123"):
            assert get_active_mirror() == "abc123"
            raise RuntimeError("test error")
        assert get_active_mirror() is None

    def test_context_manager_validates_mirror_id(self):
        """Context manager rejects invalid mirror IDs."""
        from src.knowledge.db import active_mirror_context

        with pytest.raises(ValueError), active_mirror_context("../invalid"):
            pass


class TestMirrorInfoInvariants:
    """Tests for MirrorInfo construction validation."""

    def test_empty_community_ids_rejected(self):
        """Constructing MirrorInfo with empty community_ids raises ValueError."""
        with pytest.raises(ValueError, match="community_ids must not be empty"):
            MirrorInfo(
                mirror_id="valid",
                community_ids=(),
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

    def test_invalid_mirror_id_at_construction(self):
        """Constructing MirrorInfo with path-traversal mirror_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid mirror ID"):
            MirrorInfo(
                mirror_id="../etc",
                community_ids=("test",),
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

    def test_frozen_dataclass_is_immutable(self):
        """MirrorInfo fields cannot be modified after construction."""
        info = MirrorInfo(
            mirror_id="test",
            community_ids=("testcommunity",),
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        with pytest.raises(AttributeError):
            info.mirror_id = "changed"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            info.size_bytes = 999  # type: ignore[misc]

    def test_serialization_roundtrip(self):
        """MirrorInfo to_dict/from_dict preserves all fields."""
        now = datetime.now(UTC)
        original = MirrorInfo(
            mirror_id="test123",
            community_ids=("hed", "bids"),
            created_at=now,
            expires_at=now + timedelta(hours=24),
            owner_id="user1",
            label="my mirror",
        )
        data = original.to_dict()
        restored = MirrorInfo.from_dict(data)

        assert restored.mirror_id == original.mirror_id
        assert restored.community_ids == original.community_ids
        assert restored.created_at == original.created_at
        assert restored.expires_at == original.expires_at
        assert restored.owner_id == original.owner_id
        assert restored.label == original.label
        # size_bytes is excluded from serialization
        assert "size_bytes" not in data
        assert restored.size_bytes == 0


class TestTTLClamping:
    """Tests for TTL clamping in create_mirror."""

    def test_ttl_clamped_to_max(self):
        """create_mirror clamps TTL to MAX_TTL_HOURS."""
        from src.knowledge.mirror import MAX_TTL_HOURS

        info = create_mirror(community_ids=["testcommunity"], ttl_hours=999)
        actual_ttl = (info.expires_at - info.created_at).total_seconds() / 3600
        assert actual_ttl <= MAX_TTL_HOURS
        assert actual_ttl == pytest.approx(MAX_TTL_HOURS, abs=0.01)


class TestRunSyncNowValidation:
    """Tests for run_sync_now input validation."""

    def test_invalid_sync_type_raises_valueerror(self):
        """run_sync_now raises ValueError for unknown sync types."""
        from src.api.scheduler import run_sync_now

        with pytest.raises(ValueError, match="Unknown sync_type"):
            run_sync_now("invalid_type")
