"""The conversation notebook builder, checked by readers that never touch its code (E4.1, #667).

frontend/test-notebook-builder.js covers the builder's own behavior (ordering, import
hoisting, left-out runs, ids). This test adds what only Python's tools can say:

- the notebook is valid nbformat 4.5 according to the real `nbformat` package;
- every code cell compiles as Python (top-level `await` allowed, magics stripped), so
  the builder never emits a cell that fails before it runs;
- the percent-format script, read by the real `jupytext` library and by its
  `jupytext --to notebook` command, gives back cells whose types and sources equal the
  builder's, and a source line that looks like a cell marker does not split a cell.

`build_notebook_fixture.js` calls the REAL `buildConversationNotebook` from
`frontend/osa-notebook-builder.js` over the recorded conversations in
`frontend/test-support/notebook-conversations.js`; nothing here stands in for it.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import jupytext
import nbformat
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_SCRIPT = Path(__file__).resolve().parent / "build_notebook_fixture.js"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """Run the real builder once over every conversation, and hand every test the result."""
    out = tmp_path_factory.mktemp("osa-notebook-builder") / "built.json"
    result = subprocess.run(
        ["bun", str(FIXTURE_SCRIPT), str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"building the notebook fixtures failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return json.loads(out.read_text(encoding="utf-8"))


def conversation_names() -> list[str]:
    """The conversations the fixture script builds, read from the same registry."""
    source = (REPO_ROOT / "frontend/test-support/notebook-conversations.js").read_text(encoding="utf-8")
    block = source.split("export const NOTEBOOK_CONVERSATIONS = {", 1)[1]
    return re.findall(r"^  (\w+): \(\) =>", block, re.MULTILINE)


NAMES = conversation_names()


def cells_of(notebook: dict[str, Any]) -> list[tuple[str, str]]:
    return [(cell["cell_type"], cell["source"]) for cell in notebook["cells"]]


def test_the_registry_is_not_empty() -> None:
    assert {"erp", "tricky", "empty"} <= set(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_notebook_is_valid_nbformat_4_5(built: dict[str, dict[str, Any]], name: str) -> None:
    notebook = nbformat.from_dict(built[name]["notebook"])
    assert notebook.nbformat == 4
    assert notebook.nbformat_minor == 5
    nbformat.validate(notebook)


@pytest.mark.parametrize("name", NAMES)
def test_every_code_cell_compiles(built: dict[str, dict[str, Any]], name: str) -> None:
    """Magics stripped, top-level await allowed: what a notebook kernel would accept."""
    for cell in built[name]["notebook"]["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = "\n".join(line for line in cell["source"].split("\n") if not line.startswith("%"))
        compile(source, cell["id"], "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


@pytest.mark.parametrize("name", NAMES)
def test_jupytext_library_restores_the_cells(built: dict[str, dict[str, Any]], name: str) -> None:
    restored = jupytext.reads(built[name]["script"], fmt="py:percent")
    assert [(cell.cell_type, cell.source) for cell in restored.cells] == cells_of(built[name]["notebook"])
    assert restored.metadata["kernelspec"] == built[name]["notebook"]["metadata"]["kernelspec"]


@pytest.mark.parametrize("name", NAMES)
def test_jupytext_command_restores_the_cells(
    built: dict[str, dict[str, Any]], name: str, tmp_path: Path
) -> None:
    script_path = tmp_path / f"{name}.py"
    script_path.write_text(built[name]["script"], encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "jupytext", "--to", "notebook", str(script_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"jupytext failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    restored = nbformat.read(tmp_path / f"{name}.ipynb", as_version=4)
    assert [(cell.cell_type, cell.source) for cell in restored.cells] == cells_of(built[name]["notebook"])


def test_magics_come_back_as_magics(built: dict[str, dict[str, Any]]) -> None:
    """`# %pip` in the script is `%pip` in the restored notebook, so Run all installs the package."""
    script = built["erp"]["script"]
    assert "# %pip install eegprep-lean scipy" in script
    restored = jupytext.reads(script, fmt="py:percent")
    setup = next(cell for cell in restored.cells if cell.cell_type == "code")
    assert setup.source.startswith("%pip install eegprep-lean scipy\n%matplotlib inline\n")


def test_a_line_that_looks_like_a_cell_marker_does_not_split_a_cell(
    built: dict[str, dict[str, Any]],
) -> None:
    notebook, script = built["tricky"]["notebook"], built["tricky"]["script"]
    # Title, setup, then a heading and a code cell for each of the two runs.
    assert [kind for kind, _ in cells_of(notebook)] == ["markdown", "code", "markdown", "code", "markdown", "code"]
    restored = jupytext.reads(script, fmt="py:percent")
    assert len(restored.cells) == len(notebook["cells"])
    tricky = restored.cells[3].source
    assert "# # %% a comment that begins like a cell marker" in tricky
    assert "    # # %% an indented one" in tricky
    assert "# # %% [markdown]" in tricky
    # Magic-looking lines inside a string stay untouched; a marker-like one is neutralized there too,
    # since Jupytext does not track strings when it looks for cell markers.
    assert '%not_a_magic = in a string\n# # %% not a marker, in a string\n!also not a shell command\n"""' in tricky
    # No line of the script other than a real cell marker looks like one to Jupytext.
    markers = [line for line in script.splitlines() if re.match(r"\s*#\s*%%", line)]
    assert all(re.fullmatch(r"# %%( \[markdown\])?", line) for line in markers)
    assert len(markers) == len(notebook["cells"])


def test_the_script_is_for_cell_runners(built: dict[str, dict[str, Any]]) -> None:
    script = built["erp"]["script"]
    assert script.endswith("\n")
    assert "does not run under plain python" in script
    # Top-level await is a SyntaxError for plain python, which is why the header says so.
    with pytest.raises(SyntaxError):
        compile(script, "script.py", "exec")


def test_zero_successful_runs_is_still_a_notebook(built: dict[str, dict[str, Any]]) -> None:
    for name, kinds in (("empty", ["markdown", "code"]), ("onlyFailures", ["markdown", "code", "markdown"])):
        assert built[name]["stats"]["steps"] == 0
        assert [kind for kind, _ in cells_of(built[name]["notebook"])] == kinds
        nbformat.validate(nbformat.from_dict(built[name]["notebook"]))
