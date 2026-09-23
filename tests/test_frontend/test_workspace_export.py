"""The workspace export zip, opened by a reader that never touches the
writer's own code (epic #429, phase #433).

frontend/test-workspace.js already proves the archive is a real zip (an
independent `unzip -t` and Python's own `zipfile` both accept it). This test
adds the one thing a Bun-only suite cannot check: that `notebook.ipynb` is a
notebook the real `nbformat` package -- not this repository's own code --
accepts as valid nbformat 4.5. A hand-built notebook dict that merely LOOKS
right (the right top-level keys, say) can still fail schema validation on a
field nbformat requires and this repository's own JS tests never check,
because they only assert the fields osa-workspace.js's own docstring
promises, not the full schema.

`build_workspace_export_fixture.js` calls the REAL `buildWorkspaceZip` from
`frontend/osa-workspace.js`, the same function `WorkspaceStore.exportZip`
calls; it is not a stand-in for that method, only a way to reach it without
a real IndexedDB, which does not exist under Bun (see that file's docstring,
and frontend/test-workspace.js's).
"""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import nbformat
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_SCRIPT = Path(__file__).resolve().parent / "build_workspace_export_fixture.js"


@pytest.fixture(scope="module")
def export_zip_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run the real export builder once, and hand every test the same file."""
    out_dir = tmp_path_factory.mktemp("osa-workspace-export")
    zip_path = out_dir / "workspace.zip"
    result = subprocess.run(
        ["bun", str(FIXTURE_SCRIPT), str(zip_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"building the export fixture failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert zip_path.exists(), "the fixture script did not write a zip"
    return zip_path


def test_export_is_a_valid_zip(export_zip_path: Path) -> None:
    """Every entry's CRC-32 matches, checked by Python's own zipfile."""
    with zipfile.ZipFile(export_zip_path) as archive:
        assert archive.testzip() is None


def test_export_contains_one_folder_per_session(export_zip_path: Path) -> None:
    with zipfile.ZipFile(export_zip_path) as archive:
        names = archive.namelist()
    sessions = {name.split("/", 1)[0] for name in names}
    assert sessions == {"session-abc123"}
    assert "session-abc123/manifest.json" in names
    assert "session-abc123/notebook.ipynb" in names
    assert "session-abc123/scripts/run-001.py" in names
    assert "session-abc123/artifacts/table.csv" in names


def test_manifest_names_every_run(export_zip_path: Path) -> None:
    with zipfile.ZipFile(export_zip_path) as archive:
        manifest = json.loads(archive.read("session-abc123/manifest.json"))

    assert [run["ordinal"] for run in manifest["runs"]] == [1, 2]
    first, second = manifest["runs"]
    assert first["call_id"] == "call-1"
    assert first["status"] == "ok"
    assert first["description"] == "Load the recording and plot channel E1."
    assert first["files"] == sorted(first["files"]), (
        "files are sorted, as osa-workspace.js documents"
    )
    assert "results/run-001/figure-1.png" in first["files"]
    assert second["files"] == sorted(set(second["files"])), "and de-duplicated"


def test_notebook_is_valid_nbformat(export_zip_path: Path) -> None:
    """The real nbformat package accepts the notebook as valid 4.5.

    This is the check that matters most here: nbformat's schema requires
    fields (a cell id on every cell, a `metadata` object on every output and
    every cell, an `execution_count` on every code cell) that a hand-rolled
    assertion on the JSON's shape could easily miss one of. `nbformat.validate`
    raises `nbformat.ValidationError` on the first thing it finds wrong, so a
    regression here fails loudly rather than passing a superficial shape check.
    """
    with zipfile.ZipFile(export_zip_path) as archive:
        notebook_json = json.loads(archive.read("session-abc123/notebook.ipynb"))

    notebook = nbformat.reads(json.dumps(notebook_json), as_version=4)
    nbformat.validate(notebook)  # raises nbformat.ValidationError on failure

    assert notebook.nbformat == 4
    assert notebook.nbformat_minor == 5
    # One markdown + one code cell per run, in run order.
    assert [cell.cell_type for cell in notebook.cells] == ["markdown", "code", "markdown", "code"]
    assert "Load the recording" in notebook.cells[0].source
    assert "np.arange" in notebook.cells[1].source


def test_notebook_carries_stdout_and_the_figure_as_real_outputs(export_zip_path: Path) -> None:
    with zipfile.ZipFile(export_zip_path) as archive:
        notebook_json = json.loads(archive.read("session-abc123/notebook.ipynb"))
    notebook = nbformat.reads(json.dumps(notebook_json), as_version=4)

    code_cell = notebook.cells[1]
    output_types = [out["output_type"] for out in code_cell.outputs]
    assert "stream" in output_types
    assert "display_data" in output_types
    display = next(out for out in code_cell.outputs if out["output_type"] == "display_data")
    assert "image/png" in display["data"]
    # Real, decodable base64 PNG bytes, not a placeholder string.
    import base64

    png_bytes = base64.b64decode(display["data"]["image/png"], validate=True)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
