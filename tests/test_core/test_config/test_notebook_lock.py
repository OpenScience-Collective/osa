"""Tests for a community's notebook starter, and the notebook site's merged lock.

Against real files on disk (tmp_path), the same style as test_runtime_lock.py: no
mocks, real JSON, real sha256 checks. The real merge against NEMAR's own shipped
overlay and the real Pyodide 0.29.5 lock is a separate, network-marked test in
test_notebook_site_build.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.core.config.notebook_lock import (
    NOTEBOOK_TOKEN,
    NotebookConfigError,
    NotebookLockError,
    merge_site_lock,
    starter_path_problem,
    validate_notebook_starter,
)
from src.core.config.runtime_lock import RuntimeLockOverlay


def _notebook(cells: list[dict]) -> dict:
    return {
        "cells": cells,
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _cell(cell_type: str, source: str | list[str], cell_id: str = "c1") -> dict:
    return {"cell_type": cell_type, "id": cell_id, "metadata": {}, "source": source}


def _write_starter(tmp_path: Path, notebook: dict, path: str = "notebook/starter.ipynb") -> str:
    full = tmp_path / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(notebook))
    return path


class TestStarterPathShape:
    @pytest.mark.parametrize(
        "path",
        [
            "notebook/starter.ipynb",
            "starter.ipynb",
            "a/b/c/starter.ipynb",
        ],
    )
    def test_a_plain_relative_ipynb_path_is_fine(self, path: str) -> None:
        assert starter_path_problem(path) is None

    def test_an_absolute_path_is_refused(self) -> None:
        assert starter_path_problem("/etc/passwd") is not None

    def test_a_parent_traversal_is_refused(self) -> None:
        assert starter_path_problem("../secret.ipynb") is not None

    def test_a_backslash_is_refused(self) -> None:
        assert starter_path_problem("notebook\\starter.ipynb") is not None

    def test_a_non_ipynb_suffix_is_refused(self) -> None:
        assert starter_path_problem("notebook/starter.json") is not None


class TestValidateNotebookStarter:
    def test_a_real_starter_with_the_token_validates(self, tmp_path: Path) -> None:
        notebook = _notebook(
            [
                _cell("markdown", f"# {NOTEBOOK_TOKEN} in your browser"),
                _cell("code", "1 + 1", cell_id="c2"),
            ]
        )
        path = _write_starter(tmp_path, notebook)

        parsed = validate_notebook_starter(tmp_path, path)

        assert parsed["nbformat"] == 4

    def test_the_token_may_be_split_across_a_multiline_source_list(self, tmp_path: Path) -> None:
        notebook = _notebook([_cell("markdown", ["# reads ", NOTEBOOK_TOKEN, " here"])])
        path = _write_starter(tmp_path, notebook)

        validate_notebook_starter(tmp_path, path)  # does not raise

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(NotebookConfigError, match="cannot be read"):
            validate_notebook_starter(tmp_path, "notebook/does-not-exist.ipynb")

    def test_not_json_is_refused(self, tmp_path: Path) -> None:
        full = tmp_path / "notebook" / "starter.ipynb"
        full.parent.mkdir(parents=True)
        full.write_text("not json at all {")

        with pytest.raises(NotebookConfigError, match="not valid JSON"):
            validate_notebook_starter(tmp_path, "notebook/starter.ipynb")

    def test_the_wrong_nbformat_is_refused(self, tmp_path: Path) -> None:
        notebook = _notebook([_cell("markdown", NOTEBOOK_TOKEN)])
        notebook["nbformat"] = 3
        path = _write_starter(tmp_path, notebook)

        with pytest.raises(NotebookConfigError, match="nbformat"):
            validate_notebook_starter(tmp_path, path)

    def test_no_cells_is_refused(self, tmp_path: Path) -> None:
        notebook = _notebook([])
        path = _write_starter(tmp_path, notebook)

        with pytest.raises(NotebookConfigError, match="no cells"):
            validate_notebook_starter(tmp_path, path)

    def test_a_missing_token_is_refused(self, tmp_path: Path) -> None:
        notebook = _notebook([_cell("markdown", "# nothing to fill in here")])
        path = _write_starter(tmp_path, notebook)

        with pytest.raises(NotebookConfigError, match="never uses the token"):
            validate_notebook_starter(tmp_path, path)

    def test_an_unrecognized_cell_type_is_refused(self, tmp_path: Path) -> None:
        notebook = _notebook([_cell("raw", NOTEBOOK_TOKEN)])
        path = _write_starter(tmp_path, notebook)

        with pytest.raises(NotebookConfigError, match="cell_type"):
            validate_notebook_starter(tmp_path, path)


def _entry(name: str, file_name: str, sha256: str, **overrides) -> dict:
    entry = {
        "name": name,
        "version": "1.0",
        "file_name": file_name,
        "package_type": "package",
        "install_dir": "site",
        "sha256": sha256,
        "imports": [name],
        "depends": [],
    }
    entry.update(overrides)
    return entry


def _sha(data: bytes = b"whatever") -> str:
    return hashlib.sha256(data).hexdigest()


class TestMergeSiteLock:
    def test_site_url_must_be_absolute_http(self) -> None:
        with pytest.raises(NotebookLockError, match="absolute http"):
            merge_site_lock({"packages": {}}, {}, "not-a-url")

    def test_stock_must_have_packages(self) -> None:
        with pytest.raises(NotebookLockError, match="no packages"):
            merge_site_lock({}, {}, "https://notebook.osc.earth")

    def test_an_overlay_entry_may_only_add_never_replace_the_distribution(self) -> None:
        """The same rule frontend/osa-worker-core.js's mergeLock enforces for one
        community's own runtime, checked here for the site-wide merge."""
        stock = {"packages": {"numpy": {"name": "numpy"}}}
        overlay = RuntimeLockOverlay.model_validate(
            {"packages": {"numpy": _entry("numpy", "numpy-1.0-py3-none-any.whl", _sha())}}
        )

        with pytest.raises(NotebookLockError, match="would replace the Pyodide"):
            merge_site_lock(stock, {"nemar": overlay}, "https://notebook.osc.earth")

    def test_a_new_package_is_added_with_an_absolute_community_scoped_url(self) -> None:
        stock = {"info": {"python": "3.13.2"}, "packages": {"numpy": {"name": "numpy"}}}
        digest = _sha()
        overlay = RuntimeLockOverlay.model_validate(
            {
                "packages": {
                    "eegprep-lean": _entry(
                        "eegprep-lean", "eegprep_lean-0.1.0-py3-none-any.whl", digest
                    )
                }
            }
        )

        merged = merge_site_lock(stock, {"nemar": overlay}, "https://notebook.osc.earth/")

        assert merged["info"] == {"python": "3.13.2"}
        assert "numpy" in merged["packages"]  # untouched
        entry = merged["packages"]["eegprep-lean"]
        assert entry["file_name"] == (
            "https://notebook.osc.earth/wheels/nemar/eegprep_lean-0.1.0-py3-none-any.whl"
        )
        assert entry["sha256"] == digest

    def test_two_communities_sharing_an_identical_package_merge_without_conflict(self) -> None:
        digest = _sha()
        entry = _entry("zarr", "zarr-3.4.0-py3-none-any.whl", digest)
        overlay_a = RuntimeLockOverlay.model_validate({"packages": {"zarr": entry}})
        overlay_b = RuntimeLockOverlay.model_validate({"packages": {"zarr": entry}})
        stock = {"packages": {}}

        merged = merge_site_lock(
            stock, {"nemar": overlay_a, "hed": overlay_b}, "https://notebook.osc.earth"
        )

        assert len(merged["packages"]) == 1

    def test_two_communities_disagreeing_about_a_shared_package_fails(self) -> None:
        entry_a = _entry("zarr", "zarr-3.4.0-py3-none-any.whl", _sha(b"a"))
        entry_b = _entry("zarr", "zarr-3.4.0-py3-none-any.whl", _sha(b"b"))
        overlay_a = RuntimeLockOverlay.model_validate({"packages": {"zarr": entry_a}})
        overlay_b = RuntimeLockOverlay.model_validate({"packages": {"zarr": entry_b}})
        stock = {"packages": {}}

        with pytest.raises(NotebookLockError, match="byte-identical"):
            merge_site_lock(
                stock, {"nemar": overlay_a, "hed": overlay_b}, "https://notebook.osc.earth"
            )
