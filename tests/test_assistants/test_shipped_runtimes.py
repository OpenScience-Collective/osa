"""Every shipped community that names a Pyodide lock overlay ships one that works.

The server fails closed on an overlay that does not verify: the community quietly
offers no client tools. That is only a deployment gone wrong, and never a state a
reviewed config reaches, because this test runs for every community folder. NEMAR's
own entries are tested in test_nemar_runtime.py; the overlay against Pyodide's own lock
is frontend/test-data-lane.js, which has it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import build_runtime_lock
from src.core.config.community import CommunityConfig
from src.core.config.runtime_lock import load_runtime_lock

ASSISTANTS_DIR = Path(__file__).resolve().parents[2] / "src" / "assistants"


def _communities_with_an_overlay() -> list[tuple[str, str]]:
    found = []
    for path in sorted(ASSISTANTS_DIR.glob("*/config.yaml")):
        config = CommunityConfig.from_yaml(path)
        python = config.runtime.python if config.runtime is not None else None
        if python is not None and python.lockfile is not None:
            found.append((config.id, python.lockfile))
    return found


WITH_OVERLAY = _communities_with_an_overlay()


def test_the_search_finds_the_overlays_that_exist() -> None:
    """Otherwise an empty parametrization would pass every check below by skipping it."""
    assert ("nemar", "runtime/nemar-pyodide-lock.json") in WITH_OVERLAY


@pytest.mark.parametrize(("community_id", "lockfile"), WITH_OVERLAY)
def test_every_wheel_matches_its_entry(community_id: str, lockfile: str) -> None:
    """The check the server makes before it will offer the community's tools."""
    load_runtime_lock(ASSISTANTS_DIR / community_id, lockfile)


@pytest.mark.parametrize(("community_id", "lockfile"), WITH_OVERLAY)
def test_the_overlay_is_what_its_wheels_produce(community_id: str, lockfile: str) -> None:
    """A hand edit, a wheel swapped without regenerating, or a depends.toml change not
    carried through all fail here."""
    path = ASSISTANTS_DIR / community_id / lockfile
    assert build_runtime_lock.main(["--check", str(path)]) == 0
