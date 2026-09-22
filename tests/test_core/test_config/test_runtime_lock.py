"""Tests for a community's Pyodide lock overlay, against real files on disk."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.core.config.runtime_lock import (
    RuntimeLockError,
    canonical_name,
    load_runtime_lock,
    lockfile_path_problem,
    runtime_wheel_path,
)

WHEEL = "tinypkg-1.0-py3-none-any.whl"
BYTES = b"PK\x03\x04 stands for a wheel"


def _entry(**overrides) -> dict:
    entry = {
        "name": "tinypkg",
        "version": "1.0",
        "file_name": WHEEL,
        "package_type": "package",
        "install_dir": "site",
        "sha256": hashlib.sha256(BYTES).hexdigest(),
        "imports": ["tinypkg"],
        "depends": [],
    }
    entry.update(overrides)
    return entry


def _commit(community: Path, packages: dict, wheels: dict[str, bytes] | None = None) -> str:
    """Write an overlay and its wheels as a community would commit them."""
    (community / "runtime" / "wheels").mkdir(parents=True)
    (community / "runtime" / "lock.json").write_text(json.dumps({"packages": packages}))
    for name, data in (wheels if wheels is not None else {WHEEL: BYTES}).items():
        (community / "runtime" / "wheels" / name).write_bytes(data)
    return "runtime/lock.json"


class TestLoading:
    def test_a_committed_overlay_loads_and_lists_its_wheels(self, tmp_path: Path) -> None:
        lockfile = _commit(tmp_path, {"tinypkg": _entry()})

        overlay = load_runtime_lock(tmp_path, lockfile)

        assert overlay.packages["tinypkg"].sha256 == hashlib.sha256(BYTES).hexdigest()
        assert overlay.file_names() == frozenset({WHEEL})

    def test_a_wheel_whose_bytes_changed_is_refused(self, tmp_path: Path) -> None:
        """Served wheels are cached by name for a year, so changed bytes under an old
        name would reach some readers and not others. The fix is a new version."""
        lockfile = _commit(tmp_path, {"tinypkg": _entry()}, {WHEEL: b"different bytes"})

        with pytest.raises(RuntimeLockError, match="needs a new version"):
            load_runtime_lock(tmp_path, lockfile)

    def test_a_listed_wheel_that_is_missing_is_refused(self, tmp_path: Path) -> None:
        lockfile = _commit(tmp_path, {"tinypkg": _entry()}, {})

        with pytest.raises(RuntimeLockError, match="cannot be read"):
            load_runtime_lock(tmp_path, lockfile)

    def test_a_missing_overlay_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeLockError, match="cannot be read"):
            load_runtime_lock(tmp_path, "runtime/absent.json")

    def test_an_overlay_that_is_not_json_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "lock.json").write_text("{not json")

        with pytest.raises(RuntimeLockError, match="not a valid overlay"):
            load_runtime_lock(tmp_path, "lock.json")

    def test_the_path_is_checked_before_the_disk_is_touched(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeLockError, match=r"'\.\.'"):
            load_runtime_lock(tmp_path, "../lock.json")


class TestTheEntries:
    """Each in Pyodide's own lock shape, because the widget merges them verbatim."""

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"file_name": "../tinypkg-1.0-py3-none-any.whl"}, "bare py3-none-any"),
            (
                {"file_name": "tinypkg-1.0-cp313-cp313-pyemscripten_2025_0_wasm32.whl"},
                "bare py3-none-any",
            ),
            ({"sha256": "ABC"}, "64 lowercase hexadecimal"),
            ({"package_type": "shared_library"}, "package_type"),
            ({"install_dir": "dynlib"}, "install_dir"),
            ({"unvendored_tests": False}, "Extra inputs"),
        ],
    )
    def test_an_entry_outside_the_shape_is_refused(
        self, tmp_path: Path, overrides: dict, message: str
    ) -> None:
        lockfile = _commit(tmp_path, {"tinypkg": _entry(**overrides)})

        with pytest.raises(RuntimeLockError, match=message):
            load_runtime_lock(tmp_path, lockfile)

    def test_a_key_must_be_the_name_pyodide_looks_up(self, tmp_path: Path) -> None:
        """Pyodide keys its lock by canonical name, so any other key is never found."""
        lockfile = _commit(tmp_path, {"TinyPkg": _entry()})

        with pytest.raises(RuntimeLockError, match="canonical form"):
            load_runtime_lock(tmp_path, lockfile)

    def test_two_entries_cannot_share_a_wheel(self, tmp_path: Path) -> None:
        lockfile = _commit(tmp_path, {"tinypkg": _entry(), "other": _entry(name="other")})

        with pytest.raises(RuntimeLockError, match="same wheel file"):
            load_runtime_lock(tmp_path, lockfile)

    def test_an_empty_overlay_is_refused(self, tmp_path: Path) -> None:
        lockfile = _commit(tmp_path, {}, {})

        with pytest.raises(RuntimeLockError, match="adds nothing"):
            load_runtime_lock(tmp_path, lockfile)

    def test_canonical_names_follow_pep_503(self) -> None:
        assert canonical_name("eegprep_lean") == "eegprep-lean"
        assert canonical_name("Google.CRC32C") == "google-crc32c"


class TestServing:
    def test_a_listed_wheel_resolves_to_its_file(self, tmp_path: Path) -> None:
        lockfile = _commit(tmp_path, {"tinypkg": _entry()})

        path = runtime_wheel_path(tmp_path, lockfile, WHEEL)

        assert path is not None
        assert path.read_bytes() == BYTES

    def test_anything_unlisted_resolves_to_nothing(self, tmp_path: Path) -> None:
        """Even a file that exists beside the listed one: listing is the permission."""
        lockfile = _commit(
            tmp_path, {"tinypkg": _entry()}, {WHEEL: BYTES, "extra-1.0-py3-none-any.whl": b"x"}
        )

        assert runtime_wheel_path(tmp_path, lockfile, "extra-1.0-py3-none-any.whl") is None
        assert runtime_wheel_path(tmp_path, lockfile, "lock.json") is None


@pytest.mark.parametrize(
    ("lockfile", "problem"),
    [
        ("runtime/lock.json", None),
        ("lock.json", None),
        ("/abs/lock.json", "relative"),
        ("../lock.json", "'..'"),
        ("a/./lock.json", "'..'"),
        ("a\\lock.json", "forward slashes"),
        ("runtime/lock.txt", ".json"),
    ],
)
def test_lockfile_path_problems(lockfile: str, problem: str | None) -> None:
    found = lockfile_path_problem(lockfile)
    if problem is None:
        assert found is None
    else:
        assert found is not None and problem in found
