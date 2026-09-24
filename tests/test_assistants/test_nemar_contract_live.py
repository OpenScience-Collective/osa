"""NEMAR's MCP contract, checked against production rather than assumed.

`tests/test_assistants/test_nemar_mcp_wiring.py` proves the assistant's config
wires up to whatever the server presents, and `tests/test_tools/test_mcp_client.py`
proves the client wrapper against a real server. Neither proves that the LIVE
server still hands out what NEMAR's prompt relies on: every recording's `path` and
group `name`, `read_window`'s sample range, and a `python_browser` recipe whose
`eegprep_lean` names the vendored wheel exports. A server-side rename, or a wheel
bump that drops a name, would break the browser lane while every other test here
stays green. This is the guard for that gap.

A name check cannot see a new keyword argument or a changed signature. Running
the live recipe itself against the vendored wheel is the stronger check.

Everything that touches `mcp.nemar.org` is `@pytest.mark.network` and deselected by
default (`.github/workflows/nemar-mcp-contract.yml` runs it on a schedule). The
wheel-parsing helper and its unit test are not: they run in every CI sweep, against
the wheel actually committed beside the prompt.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest
from packaging.version import Version

from src.core.config.community import CommunityConfig, McpServer
from src.core.config.runtime_lock import WHEELS_DIR_NAME, load_runtime_lock
from src.tools.mcp_client import discover_mcp_tools

NEMAR_DIR = Path(__file__).resolve().parents[2] / "src" / "assistants" / "nemar"
NEMAR_MCP_URL = "https://mcp.nemar.org/mcp"
RUNNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_python_browser_recipe.py"


def _vendored_eegprep_lean_wheel() -> Path:
    """The eegprep-lean wheel NEMAR's lock overlay ships, found through the overlay
    the way the server finds it, so a re-vendor needs no edit here."""
    config = CommunityConfig.from_yaml(NEMAR_DIR / "config.yaml")
    assert config.runtime is not None and config.runtime.python is not None
    lockfile = config.runtime.python.lockfile
    assert lockfile is not None, "NEMAR's runtime names no lock overlay"
    overlay = load_runtime_lock(NEMAR_DIR, lockfile)
    entry = overlay.packages["eegprep-lean"]
    return (NEMAR_DIR / lockfile).parent / WHEELS_DIR_NAME / entry.file_name


WHEEL_PATH = _vendored_eegprep_lean_wheel()


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


def _eegprep_lean_bindings(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """How a snippet refers to eegprep_lean: the names bound to the module itself
    (`import eegprep_lean`, `import eegprep_lean as epl`), and each name imported
    from it, local name to real name (`from eegprep_lean import read_index as ri`)."""
    modules: set[str] = set()
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "eegprep_lean":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "eegprep_lean":
            for alias in node.names:
                imported[alias.asname or alias.name] = alias.name
    return modules, imported


def eegprep_lean_names_used(code: str) -> set[str]:
    """Every `eegprep_lean` name a python snippet actually reads: the names of a
    `from eegprep_lean import ...` statement, and the attribute of an access on
    the module under any alias. Read with `ast`, so a name mentioned only in a
    comment or a string is never counted as used.
    """
    tree = ast.parse(code)
    modules, imported = _eegprep_lean_bindings(tree)
    names = set(imported.values())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in modules
        ):
            names.add(node.attr)
    return names


def eegprep_lean_calls(code: str) -> list[tuple[str, frozenset[str]]]:
    """Each call a snippet makes to an eegprep_lean function, as its real name and
    the keyword arguments passed. A call through a returned object
    (`arr.getitem(...)`) is not eegprep_lean's own name and is not listed."""
    tree = ast.parse(code)
    modules, imported = _eegprep_lean_bindings(tree)
    calls: list[tuple[str, frozenset[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        ):
            name = func.attr
        elif isinstance(func, ast.Name) and func.id in imported:
            name = imported[func.id]
        else:
            continue
        keywords = frozenset(k.arg for k in node.keywords if k.arg is not None)
        calls.append((name, keywords))
    return calls


def eegprep_lean_keywords(wheel_path: Path) -> dict[str, frozenset[str] | None]:
    """For each function eegprep_lean exports, the parameter names a caller may
    pass by keyword, read from the wheel's source; None when it takes `**kwargs`.
    Found where `__init__.py` says each name lives: a `from .module import` or an
    `_EXTRA_NAMES` entry. A class or constant has no entry here."""
    with zipfile.ZipFile(wheel_path) as archive:
        init = ast.parse(archive.read("eegprep_lean/__init__.py").decode("utf-8"))
        homes: dict[str, str] = {}
        for node in ast.walk(init):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                for alias in node.names:
                    homes[alias.asname or alias.name] = f"eegprep_lean/{node.module}.py"
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "_EXTRA_NAMES"
                and isinstance(node.value, ast.Dict)
            ):
                for key, value in zip(node.value.keys, node.value.values, strict=True):
                    if isinstance(key, ast.Constant) and isinstance(value, ast.Tuple):
                        module = value.elts[0]
                        if isinstance(module, ast.Constant) and isinstance(module.value, str):
                            homes[str(key.value)] = module.value.replace(".", "/") + ".py"
        modules = {
            path: ast.parse(archive.read(path).decode("utf-8")) for path in set(homes.values())
        }

    keywords: dict[str, frozenset[str] | None] = {}
    for name, path in homes.items():
        for node in modules[path].body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                arguments = node.args
                if arguments.kwarg is not None:
                    keywords[name] = None
                else:
                    keywords[name] = frozenset(
                        a.arg for a in [*arguments.args, *arguments.kwonlyargs]
                    )
    return keywords


def recipe_problems(code: str, wheel_path: Path) -> list[str]:
    """What in `code` the wheel cannot run: a name it does not export, or a keyword
    argument its function does not take. Empty when every eegprep_lean call fits."""
    problems = [
        f"eegprep_lean.{name} is not exported"
        for name in sorted(eegprep_lean_names_used(code) - eegprep_lean_exported_names(wheel_path))
    ]
    accepted = eegprep_lean_keywords(wheel_path)
    for name, passed in eegprep_lean_calls(code):
        allowed = accepted.get(name)
        if allowed is None:
            continue
        for keyword in sorted(passed - allowed):
            problems.append(f"eegprep_lean.{name} takes no keyword {keyword!r}")
    return problems


class TestTheDevelopPreludeReachesReadIndex:
    """NEMAR's develop prelude points eegprep-lean at staging by assigning
    `eegprep_lean.index.INDEX_URL_TEMPLATE` (#480). That only works while `read_index`
    reads that module global at each call; a re-vendored wheel that renamed it, or
    captured it in a default argument, would leave the develop chat reading production
    with nothing failing until a staging reader tried. Offline: ast over the wheel."""

    def _index_module(self) -> ast.Module:
        with zipfile.ZipFile(WHEEL_PATH) as wheel:
            return ast.parse(wheel.read("eegprep_lean/index.py").decode("utf-8"))

    def test_the_template_is_a_module_global_holding_the_production_host(self) -> None:
        assigned = {
            target.id: node.value
            for node in self._index_module().body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        template = assigned.get("INDEX_URL_TEMPLATE")
        assert isinstance(template, ast.Constant)
        assert template.value == "https://zarr.nemar.org/{dataset_id}/zarr/index.json"

    def test_read_index_reads_it_at_call_time(self) -> None:
        [read_index] = [
            node
            for node in self._index_module().body
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
            and node.name == "read_index"
        ]
        loads = {
            n.id
            for n in ast.walk(read_index)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        assert "INDEX_URL_TEMPLATE" in loads
        # Not bound at definition time, where assigning the global later changes nothing.
        defaults = read_index.args.defaults + [d for d in read_index.args.kw_defaults if d]
        assert not any(isinstance(d, ast.Name) and d.id == "INDEX_URL_TEMPLATE" for d in defaults)

    def test_the_develop_prelude_assigns_that_global_to_the_staging_host(self) -> None:
        config = CommunityConfig.from_yaml(NEMAR_DIR / "config.yaml")
        assert config.runtime is not None
        develop = config.runtime.for_deployment("develop").python
        assert develop is not None and develop.prelude is not None
        [assignment] = [
            node
            for node in ast.parse(develop.prelude).body
            if isinstance(node, ast.Assign)
            and ast.unparse(node.targets[0]) == "eegprep_lean.index.INDEX_URL_TEMPLATE"
        ]
        assert isinstance(assignment.value, ast.Constant)
        assert assignment.value.value == "https://zarr-test.nemar.org/{dataset_id}/zarr/index.json"


class TestEegprepLeanExportedNames:
    """Runs against the wheel actually committed beside the prompt, so this is
    not a network test: something here runs in every CI sweep."""

    def test_open_array_and_read_window_are_both_exported(self) -> None:
        """The names both reads need: `open_array` for the raw read, and
        `read_index`, `read_window` and `plot_window` for the physical read the
        prompt's snippet makes."""
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

    def test_an_aliased_module_is_counted(self) -> None:
        code = "import eegprep_lean as epl\nw = await epl.read_window(i, s)\n"
        assert eegprep_lean_names_used(code) == {"read_window"}


def _prompt_snippet() -> str:
    """NEMAR's canonical snippet, as the prompt teaches it."""
    prompt = CommunityConfig.from_yaml(NEMAR_DIR / "config.yaml").system_prompt
    heading = prompt.index("## Running code in the reader's browser")
    fence = re.search(r"```python\n(.*?)\n```", prompt[heading:], re.DOTALL)
    assert fence is not None, "the prompt's browser section has no python snippet"
    return fence.group(1)


class TestRecipeProblems:
    """Names alone cannot see a keyword the wheel does not take, which is how a
    recipe written for a newer eegprep-lean fails on an older one."""

    def test_the_prompts_own_snippet_fits_the_vendored_wheel(self) -> None:
        assert recipe_problems(_prompt_snippet(), WHEEL_PATH) == []

    def test_a_keyword_the_function_does_not_take_is_named(self) -> None:
        code = (
            'import eegprep_lean\nindex = await eegprep_lean.read_index("x", no_such_keyword=1)\n'
        )

        assert recipe_problems(code, WHEEL_PATH) == [
            "eegprep_lean.read_index takes no keyword 'no_such_keyword'"
        ]

    def test_a_keyword_is_checked_through_an_imported_alias(self) -> None:
        code = 'from eegprep_lean import read_index as ri\nindex = await ri("x", bogus=1)\n'

        assert recipe_problems(code, WHEEL_PATH) == [
            "eegprep_lean.read_index takes no keyword 'bogus'"
        ]

    def test_a_name_the_wheel_does_not_export_is_named(self) -> None:
        code = "import eegprep_lean\neegprep_lean.read_everything()\n"

        assert recipe_problems(code, WHEEL_PATH) == ["eegprep_lean.read_everything is not exported"]

    def test_the_check_agrees_with_the_wheels_version_about_index_url(self) -> None:
        """Two independent sources for one fact: eegprep-lean added `index_url` in
        0.1.0.dev2, and nemar-cli's recipe needs it. The check must read the vendored
        wheel's signature the way its version says it should."""
        overlay = load_runtime_lock(NEMAR_DIR, "runtime/nemar-pyodide-lock.json")
        version = Version(overlay.packages["eegprep-lean"].version)
        code = (
            "import eegprep_lean\n"
            'index = await eegprep_lean.read_index("x", index_url="https://example.org/index.json")\n'
        )

        refused = recipe_problems(code, WHEEL_PATH) != []

        assert refused == (version < Version("0.1.0.dev2"))


# ---------------------------------------------------------------------------
# Against the real NEMAR server. Deselected by default; see
# .github/workflows/nemar-mcp-contract.yml.
# ---------------------------------------------------------------------------


def _answer(result: object, tool: str, key: str) -> dict:
    """A tool's structured answer, holding `key`. A tool that answers in text (a
    refusal, a timeout, an unreachable server) or with an error object fails here,
    showing what it said, not with a TypeError or KeyError later."""
    assert isinstance(result, dict), f"{tool} answered in text, not structured data: {result!r}"
    assert key in result, f"{tool} answered without {key!r}: {result!r}"
    return result


async def _live_read_window(duration_s: int) -> tuple[dict, dict]:
    """nm000103's first recording, and `nemar_read_window`'s answer for its first
    `duration_s` seconds of its first group, from production."""
    tools = discover_mcp_tools(McpServer(name="nemar", url=NEMAR_MCP_URL))
    list_recordings = next(t for t in tools if t.name == "nemar_list_recordings")
    read_window = next(t for t in tools if t.name == "nemar_read_window")

    listed = _answer(
        await list_recordings.ainvoke({"dataset_id": "nm000103", "limit": 1}),
        "nemar_list_recordings",
        "recordings",
    )
    recording = listed["recordings"][0]
    result = _answer(
        await read_window.ainvoke(
            {
                "dataset_id": "nm000103",
                "recording": recording["path"],
                "group": recording["groups"][0]["name"],
                "start_s": 0,
                "duration_s": duration_s,
            }
        ),
        "nemar_read_window",
        "recipe",
    )
    return recording, result


@pytest.mark.network
class TestListRecordingsContract:
    async def test_every_recording_has_a_path_and_named_groups(self) -> None:
        tools = discover_mcp_tools(McpServer(name="nemar", url=NEMAR_MCP_URL))
        list_recordings = next(t for t in tools if t.name == "nemar_list_recordings")

        result = _answer(
            await list_recordings.ainvoke({"dataset_id": "nm000103", "limit": 5}),
            "nemar_list_recordings",
            "recordings",
        )

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
        _, result = await _live_read_window(duration_s=10)

        assert result["mode"] == "recipe"
        recipe = result["recipe"]
        how_to = recipe["how_to"]
        assert isinstance(how_to["python_browser"], str) and how_to["python_browser"]

        # The sample-range fields the prompt's snippet needs START_SAMPLE and
        # N_SAMPLES from (config.yaml's "Get the values the code needs" step).
        sample_slice = recipe["sample_slice"]
        assert isinstance(sample_slice["start"], int) and sample_slice["start"] >= 0
        assert isinstance(sample_slice["end"], int) and sample_slice["end"] > sample_slice["start"]

    async def test_the_live_recipe_fits_the_vendored_wheel(self) -> None:
        """The guard that can go red: if `mcp.nemar.org` starts handing out a
        `python_browser` recipe that calls a name the vendored wheel does not
        export, or passes a keyword its function does not take, this fails before
        a reader's browser does.
        """
        _, result = await _live_read_window(duration_s=10)

        snippet = result["recipe"]["how_to"]["python_browser"]
        assert eegprep_lean_names_used(snippet), (
            "the live recipe named no eegprep_lean names at all; that is itself a break"
        )

        problems = recipe_problems(snippet, WHEEL_PATH)
        assert not problems, (
            f"the live python_browser recipe cannot run on the vendored wheel "
            f"({WHEEL_PATH.name}): {problems}\n{snippet}"
        )


@pytest.mark.network
class TestLiveRecipeOnCPython:
    """The daily live check (#432): run production's actual `python_browser` recipe,
    unmodified, in a fresh CPython interpreter with nothing installed but the
    vendored wheel -- the same bytes this repository serves same-origin to a
    reader's browser. The tests above check the recipe's names and keywords fit the
    wheel's signatures; this one runs it, over the real network, against real data.

    `eegprep_lean.transport.UrllibTransport` (the CPython, non-Pyodide path this
    subprocess actually takes) sends ``User-Agent: eegprep-lean``, never the bare
    ``Python-urllib/x.y`` default -- see that module's own ``USER_AGENT`` constant
    and its comment, which already names the NEMAR hosts refusing the default. So
    this test needs no user-agent workaround; if that ever changes upstream, this
    is exactly the test that would start failing with a 403 from zarr.nemar.org.
    """

    async def test_the_live_recipe_runs_on_the_vendored_wheel(self, tmp_path: Path) -> None:
        _, result = await _live_read_window(duration_s=2)

        recipe = result["recipe"]
        snippet = recipe["how_to"]["python_browser"]
        sample_slice = recipe["sample_slice"]
        start_sample, end_sample = sample_slice["start"], sample_slice["end"]
        width = end_sample - start_sample

        recipe_file = tmp_path / "recipe.py"
        recipe_file.write_text(snippet, encoding="utf-8")

        completed = subprocess.run(
            [
                "uv",
                "run",
                "--isolated",
                "--no-project",
                "--with",
                f"eegprep-lean[zarr] @ file://{WHEEL_PATH.resolve()}",
                "python",
                str(RUNNER_PATH),
                str(recipe_file),
                str(start_sample),
                str(end_sample),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )

        assert completed.returncode == 0, (
            f"the live recipe failed under the vendored wheel on CPython "
            f"({WHEEL_PATH.name}):\n{snippet}\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        bound = [read for read in (report.get("window"), report.get("digital")) if read]
        assert bound, (
            f"the recipe ran but bound neither `window` nor `digital`: {report}\n{snippet}"
        )
        for read in bound:
            assert read["shape"][-1] == width, (
                f"expected {width} samples (sample_slice {start_sample}-{end_sample}), got {read}"
            )
            assert read["dtype"], f"a read with no dtype: {read}"
        physical = report.get("window") or {}
        if "unit" in physical:
            # A read_window recipe: the physical read names its unit, and the raw
            # read it keeps covers the same channels and samples.
            assert isinstance(physical["unit"], str) and physical["unit"], physical
            if report.get("digital"):
                assert report["digital"]["shape"] == physical["shape"], report
