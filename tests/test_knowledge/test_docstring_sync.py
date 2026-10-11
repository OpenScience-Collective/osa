"""Tests for sync_repo_docstrings, end to end against GitHub served at the HTTP boundary.

respx answers GitHub's tree API and raw file host, as the other HTTP-level tests in this
repo do; the parser, the sync and the database are the real ones. These pin when the sync
may delete what is already indexed: never for a file it could not read, and never for a
missing file when GitHub's listing was truncated.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import respx

from src.knowledge.db import get_connection, init_db, upsert_docstring
from src.knowledge.docstring_sync import (
    GITHUB_API_BASE,
    GITHUB_RAW_BASE,
    sync_repo_docstrings,
)

REPO = "org/toolbox"
BRANCH = "master"

DOCUMENTED = """function y = documented(x)
% DOCUMENTED doubles x
y = 2 * x;
"""

LICENSE_ONLY = """function y = licensed(x)

% @=============================================================================
% This function is part of the Brainstorm software:
% =============================================================================@

y = x;
"""


@pytest.fixture
def db_path(tmp_path: Path):
    path = tmp_path / "knowledge" / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=path):
        init_db("test")
        yield path


def _seed(path: Path, rows: list[tuple[str, str]]) -> None:
    with patch("src.knowledge.db.get_db_path", return_value=path), get_connection("test") as conn:
        for file_path, symbol in rows:
            upsert_docstring(
                conn,
                repo=REPO,
                file_path=file_path,
                language="matlab",
                symbol_name=symbol,
                symbol_type="function",
                docstring=f"{symbol} old help",
                branch=BRANCH,
            )
        conn.commit()


def _indexed(path: Path) -> set[tuple[str, str]]:
    with patch("src.knowledge.db.get_db_path", return_value=path), get_connection("test") as conn:
        rows = conn.execute("SELECT file_path, symbol_name FROM docstrings").fetchall()
    return {(r["file_path"], r["symbol_name"]) for r in rows}


def _serve(router, files: dict[str, str | int], truncated: bool = False) -> None:
    """Serve a tree listing the given files; a file maps to its text or an HTTP status."""
    tree = [{"path": name, "type": "blob"} for name in files]
    router.get(f"{GITHUB_API_BASE}/repos/{REPO}/git/trees/{BRANCH}").respond(
        json={"tree": tree, "truncated": truncated}
    )
    for name, body in files.items():
        route = router.get(f"{GITHUB_RAW_BASE}/{REPO}/{BRANCH}/{name}")
        if isinstance(body, int):
            route.respond(body)
        else:
            route.respond(text=body)


def _sync(path: Path) -> int:
    with patch("src.knowledge.db.get_db_path", return_value=path):
        return sync_repo_docstrings(REPO, "matlab", project="test", branch=BRANCH)


def test_complete_listing_deletes_what_is_gone(db_path: Path):
    """A removed file, a renamed function and a license-only "help" all leave the index."""
    _seed(db_path, [("documented.m", "old_name"), ("licensed.m", "licensed"), ("gone.m", "gone")])

    with respx.mock() as router:
        _serve(router, {"documented.m": DOCUMENTED, "licensed.m": LICENSE_ONLY})
        assert _sync(db_path) == 1

    assert _indexed(db_path) == {("documented.m", "documented")}


def test_truncated_listing_keeps_files_it_did_not_list(db_path: Path):
    """When GitHub truncates the tree, a file missing from it may still exist: kept."""
    _seed(db_path, [("gone.m", "gone")])

    with respx.mock() as router:
        _serve(router, {"documented.m": DOCUMENTED}, truncated=True)
        _sync(db_path)

    assert _indexed(db_path) == {("documented.m", "documented"), ("gone.m", "gone")}


def test_file_that_could_not_be_read_keeps_its_docstrings(db_path: Path):
    """A file whose download failed was not parsed, so nothing of it is deleted."""
    _seed(db_path, [("flaky.m", "flaky")])

    with respx.mock() as router:
        _serve(router, {"documented.m": DOCUMENTED, "flaky.m": 502})
        _sync(db_path)

    assert _indexed(db_path) == {("documented.m", "documented"), ("flaky.m", "flaky")}
