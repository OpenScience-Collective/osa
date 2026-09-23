"""NEMAR's MCP contract, checked against production rather than assumed.

`tests/test_assistants/test_nemar_mcp_wiring.py` proves the assistant's config
wires up to whatever the server presents, and `tests/test_tools/test_mcp_client.py`
proves the client wrapper against a real server. Neither proves that the LIVE
server's `read_window` still hands out a `python_browser` recipe the prompt's own
snippet (`src/assistants/nemar/config.yaml`, committed as 6c13d2b) can actually run:
`eegprep_lean.read_index`, `.store`, `.group` and `read_window` need the names the
recipe uses to exist in the vendored wheel, and a server-side rename or a wheel
bump that drops one would break the browser lane while every other test here stays
green. This is the guard for that gap.

Everything that touches `mcp.nemar.org` is `@pytest.mark.network` and deselected by
default (`.github/workflows/nemar-mcp-contract.yml` runs it on a schedule). The
wheel-parsing helper and its unit test are not: they run in every CI sweep, against
the wheel actually committed beside the prompt.
"""

from __future__ import annotations

import ast
import zipfile
from pathlib import Path

import pytest

from src.core.config.community import McpServer
from src.tools.mcp_client import discover_mcp_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
WHEEL_PATH = (
    REPO_ROOT
    / "src"
    / "assistants"
    / "nemar"
    / "runtime"
    / "wheels"
    / "eegprep_lean-0.1.0.dev1-py3-none-any.whl"
)
NEMAR_MCP_URL = "https://mcp.nemar.org/mcp"


# ---------------------------------------------------------------------------
# Pure helpers. No network, no MCP server: ast over text, read from disk.
# ---------------------------------------------------------------------------


def eegprep_lean_exported_names(wheel_path: Path) -> set[str]:
    """Every name `eegprep_lean.<name>` or `from eegprep_lean import <name>` can
    resolve to, read from the wheel's own `__init__.py` with `ast` rather than
    imported and inspected: importing the wheel would need it installed, and
    execution is more trust than reading a name list needs.

    Two sources, matching how `eegprep_lean/__init__.py` actually resolves a name
    (see its own `__getattr__`): every name bound by a `from .module import ...`
    statement (eager, imported at module load), and the string keys of the
    `_EXTRA_NAMES` dict literal (lazy, resolved on first attribute access, one
    per installable extra). A name reachable only some third way is not a gap
    this reader should paper over by guessing; if one is ever added, this
    function should learn to read it too.
    """
    with zipfile.ZipFile(wheel_path) as archive:
        source = archive.read("eegprep_lean/__init__.py").decode("utf-8")
    tree = ast.parse(source)

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_EXTRA_NAMES"
            and isinstance(node.value, ast.Dict)
        ):
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    names.add(key.value)
    return names


def eegprep_lean_names_used(code: str) -> set[str]:
    """Every `eegprep_lean` name a python snippet actually reads: the names of a
    `from eegprep_lean import ...` statement, and the attribute of an
    `eegprep_lean.<name>` access. Read with `ast`, so a name mentioned only in a
    comment or a string (the live recipe's `how_to.python_browser` describes
    `read_window(index, store, ...)` in a comment, which is prose, not a second
    import) is never counted as used.
    """
    tree = ast.parse(code)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "eegprep_lean":
            for alias in node.names:
                names.add(alias.name)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "eegprep_lean"
        ):
            names.add(node.attr)
    return names


class TestEegprepLeanExportedNames:
    """Runs against the wheel actually committed beside the prompt, so this is
    not a network test: something here runs in every CI sweep."""

    def test_open_array_and_read_window_are_both_exported(self) -> None:
        """The two names the two recipe flavors need: `open_array` for the
        recipe's own `python_browser` snippet, `read_window` for the prompt's
        canonical snippet (config.yaml, 6c13d2b)."""
        names = eegprep_lean_exported_names(WHEEL_PATH)
        assert {"open_array", "read_window", "read_index", "plot_window"} <= names

    def test_a_name_the_wheel_does_not_export_is_absent(self) -> None:
        """The control: this is a real check, not one that reports everything as
        exported regardless of what is asked."""
        names = eegprep_lean_exported_names(WHEEL_PATH)
        assert "this_name_does_not_exist_in_eegprep_lean" not in names

    def test_matches_the_wheels_own_dunder_all(self) -> None:
        """`__all__` is the module's own claim about its public surface, computed
        a different way (a plain list literal, not descended into an import or a
        dict); the two should describe the same names minus `__version__`, which
        is a plain constant rather than an import or an extra."""
        with zipfile.ZipFile(WHEEL_PATH) as archive:
            source = archive.read("eegprep_lean/__init__.py").decode("utf-8")
        tree = ast.parse(source)
        dunder_all: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "__all__"
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                for element in node.value.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        dunder_all.add(element.value)

        assert dunder_all - {"__version__"} == eegprep_lean_exported_names(WHEEL_PATH)


class TestEegprepLeanNamesUsed:
    def test_a_from_import_is_counted(self) -> None:
        assert eegprep_lean_names_used("from eegprep_lean import open_array\n") == {"open_array"}

    def test_multiple_imported_names_are_all_counted(self) -> None:
        code = "from eegprep_lean import read_index, read_window\n"
        assert eegprep_lean_names_used(code) == {"read_index", "read_window"}

    def test_an_attribute_access_is_counted(self) -> None:
        code = "import eegprep_lean\nx = eegprep_lean.plot_window(w)\n"
        assert eegprep_lean_names_used(code) == {"plot_window"}

    def test_a_name_mentioned_only_in_a_comment_is_not_counted(self) -> None:
        """The exact shape of the live recipe's how_to.python_browser: a real
        import of one name, and a second name named only in a trailing comment
        describing an alternative. Only the real import counts."""
        code = (
            "from eegprep_lean import open_array\n"
            'arr = await open_array("url")\n'
            "# read_window(index, store, ...) does the same read in physical units\n"
        )
        assert eegprep_lean_names_used(code) == {"open_array"}

    def test_a_name_mentioned_in_a_string_is_not_counted(self) -> None:
        code = 'from eegprep_lean import open_array\nlabel = "call eegprep_lean.read_window next"\n'
        assert eegprep_lean_names_used(code) == {"open_array"}

    def test_top_level_await_parses(self) -> None:
        """The recipe's snippets are meant to run in a runtime whose top level is
        already async (Pyodide's exec harness); this must parse them as they are
        written, not require them rewritten into a function first."""
        code = "from eegprep_lean import open_array\narr = await open_array('url')\n"
        assert eegprep_lean_names_used(code) == {"open_array"}


# ---------------------------------------------------------------------------
# Against the real NEMAR server. Deselected by default; see
# .github/workflows/nemar-mcp-contract.yml.
# ---------------------------------------------------------------------------


@pytest.mark.network
class TestListRecordingsContract:
    async def test_every_recording_has_a_path_and_named_groups(self) -> None:
        tools = discover_mcp_tools(McpServer(name="nemar", url=NEMAR_MCP_URL))
        list_recordings = next(t for t in tools if t.name == "nemar_list_recordings")

        result = await list_recordings.ainvoke({"dataset_id": "nm000103", "limit": 5})

        recordings = result["recordings"]
        assert len(recordings) > 0
        for recording in recordings:
            assert isinstance(recording["path"], str) and recording["path"]
            assert len(recording["groups"]) > 0
            for group in recording["groups"]:
                assert isinstance(group["name"], str) and group["name"]


@pytest.mark.network
class TestReadWindowContract:
    async def test_the_recipe_carries_how_to_python_browser_and_the_sample_range(
        self,
    ) -> None:
        """Pins what `nemar_read_window`'s response actually contains today, so a
        server-side rename fails this test rather than only the model's next
        attempt to read the sample range out of it."""
        tools = discover_mcp_tools(McpServer(name="nemar", url=NEMAR_MCP_URL))
        list_recordings = next(t for t in tools if t.name == "nemar_list_recordings")
        read_window = next(t for t in tools if t.name == "nemar_read_window")

        recordings = await list_recordings.ainvoke({"dataset_id": "nm000103", "limit": 1})
        recording = recordings["recordings"][0]
        group_name = recording["groups"][0]["name"]

        result = await read_window.ainvoke(
            {
                "dataset_id": "nm000103",
                "recording": recording["path"],
                "group": group_name,
                "start_s": 0,
                "duration_s": 10,
            }
        )

        assert result["mode"] == "recipe"
        recipe = result["recipe"]
        how_to = recipe["how_to"]
        assert isinstance(how_to["python_browser"], str) and how_to["python_browser"]

        # The sample-range fields the prompt's snippet needs START_SAMPLE and
        # N_SAMPLES from (config.yaml's "Get the values the code needs" step).
        sample_slice = recipe["sample_slice"]
        assert isinstance(sample_slice["start"], int) and sample_slice["start"] >= 0
        assert isinstance(sample_slice["end"], int) and sample_slice["end"] > sample_slice["start"]

    async def test_every_eegprep_lean_name_the_live_recipe_uses_is_exported(self) -> None:
        """The guard that can go red: if `mcp.nemar.org` starts handing out a
        `python_browser` recipe that calls something `eegprep_lean` does not
        export -- a rename on the server side, or a wheel bump here that drops a
        name -- this fails without needing a person to notice the browser lane
        silently broke.
        """
        tools = discover_mcp_tools(McpServer(name="nemar", url=NEMAR_MCP_URL))
        list_recordings = next(t for t in tools if t.name == "nemar_list_recordings")
        read_window = next(t for t in tools if t.name == "nemar_read_window")

        recordings = await list_recordings.ainvoke({"dataset_id": "nm000103", "limit": 1})
        recording = recordings["recordings"][0]
        group_name = recording["groups"][0]["name"]

        result = await read_window.ainvoke(
            {
                "dataset_id": "nm000103",
                "recording": recording["path"],
                "group": group_name,
                "start_s": 0,
                "duration_s": 10,
            }
        )

        snippet = result["recipe"]["how_to"]["python_browser"]
        used = eegprep_lean_names_used(snippet)
        assert used, "the live recipe named no eegprep_lean names at all; that is itself a break"

        exported = eegprep_lean_exported_names(WHEEL_PATH)
        missing = used - exported
        assert not missing, (
            f"the live python_browser recipe uses {missing}, which the vendored wheel "
            f"({WHEEL_PATH.name}) does not export: {snippet!r}"
        )
