"""NEMAR runs code in the reader's browser, with the runtime its shipped files describe.

Reads the real `src/assistants/nemar/` folder: the config the app loads, the lock
overlay beside it, and the wheels it pins. What the runtime does with them in a real
Pyodide is `frontend/test-data-lane.js`; this is the server's side of the same files.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tomllib
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import build_runtime_lock
from src.core.config.community import CommunityConfig
from src.core.config.runtime_lock import load_runtime_lock

ROOT = Path(__file__).resolve().parents[2]
NEMAR_DIR = ROOT / "src" / "assistants" / "nemar"


@pytest.fixture(scope="module")
def nemar() -> CommunityConfig:
    return CommunityConfig.from_yaml(NEMAR_DIR / "config.yaml")


class TestTheShippedConfig:
    def test_it_offers_code_execution_behind_the_gate(self, nemar: CommunityConfig) -> None:
        assert nemar.extensions is not None
        [tool] = nemar.extensions.client_tools
        assert (tool.name, tool.runtime, tool.requires_permission) == (
            "execute_code",
            "python",
            True,
        )

    def test_the_tool_teaches_the_read_that_is_in_physical_units(
        self, nemar: CommunityConfig
    ) -> None:
        """The model sees this description verbatim. The read it names returns physical
        units with labels, and the one it warns off returns stored digital counts, which
        plot like EEG while being wrong."""
        assert nemar.extensions is not None
        description = nemar.extensions.client_tools[0].description
        assert "eegprep_lean.read_index" in description
        assert "eegprep_lean.read_window" in description
        assert "open_array" in description and "digital counts" in description
        assert "python_zarr" in description
        assert "display(eegprep_lean.plot_window(window).figure)" in description

    def test_the_prompt_says_where_each_value_comes_from(self, nemar: CommunityConfig) -> None:
        """index.store matches a store's path only, and nemar_read_window also accepts
        the zarr name, so the prompt must name the field."""
        prompt = nemar.system_prompt
        assert "nemar_list_recordings" in prompt
        assert "`path`" in prompt and "never its `zarr` name" in prompt

    def test_it_reaches_only_the_data_plane(self, nemar: CommunityConfig) -> None:
        assert nemar.runtime is not None and nemar.runtime.python is not None
        assert nemar.runtime.python.fetch_allow == ["https://zarr.nemar.org/"]

    def test_its_prelude_hands_eegprep_lean_the_runtime_client(
        self, nemar: CommunityConfig
    ) -> None:
        """Without it, eegprep-lean reaches for pyodide.http, which the seal removes,
        and the recipe fails with an import error that names neither."""
        assert nemar.runtime is not None and nemar.runtime.python is not None
        prelude = nemar.runtime.python.prelude or ""
        assert "set_default_transport(eegprep_lean.FetchTransport(osa.fetch))" in prelude

    def test_the_prompt_teaches_the_browser_lane(self, nemar: CommunityConfig) -> None:
        prompt = nemar.system_prompt
        assert "## Running code in the reader's browser" in prompt
        assert "python_browser" in prompt
        assert "eegprep-lean" in prompt

    def test_the_prompt_reads_the_placeholder_a_withheld_image_carries(
        self, nemar: CommunityConfig
    ) -> None:
        """On a model path that takes no images, a figure or overview arrives as a
        placeholder, and the prompt tells the model what that phrase means. Both
        sides are pinned here so a reworded placeholder cannot leave the model
        describing an image it never received."""
        from src.api.tool_results import IMAGES_NOT_SENT

        prompt = nemar.system_prompt
        assert IMAGES_NOT_SENT.startswith("not attached")
        assert prompt.count("says it was not attached") == 1
        assert "says the figure was not attached" in prompt

    def test_the_workspace_caps_it_states_match_workspace_limits(
        self, nemar: CommunityConfig
    ) -> None:
        """The description's "at most 32 files, 10 MB each and 25 MB in all per run" is
        hand-written prose, unlike osa-egress.js's save_script/save_artifact docstrings,
        which are generated FROM WORKSPACE_LIMITS (frontend/test-output.js checks those).
        This is the one place this file's own numbers are checked against the same
        constant, by running the real module rather than re-typing its values here.
        """
        assert nemar.extensions is not None
        description = nemar.extensions.client_tools[0].description
        match = re.search(
            r"at most (\d+) files, (\d+) MB each and (\d+) MB in all per run", description
        )
        assert match is not None, "the description no longer states the workspace caps this way"
        max_files, max_file_mb, max_run_mb = (int(g) for g in match.groups())

        result = subprocess.run(
            [
                "bun",
                "-e",
                "import { WORKSPACE_LIMITS } from './frontend/osa-workspace.js';"
                "console.log(JSON.stringify(WORKSPACE_LIMITS));",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"reading WORKSPACE_LIMITS failed: {result.stderr}"
        limits = json.loads(result.stdout)

        assert max_files == limits["MAX_EXPLICIT_FILES"]
        assert max_file_mb == limits["MAX_FILE_BYTES"] / (1024 * 1024)
        assert max_run_mb == limits["MAX_RUN_BYTES"] / (1024 * 1024)


def _luminance(hex_color: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(hex_a: str, hex_b: str) -> float:
    """WCAG 2 contrast ratio between two hex colors, order-independent."""
    la, lb = _luminance(hex_a), _luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _contrast_with_white(hex_color: str) -> float:
    """WCAG 2 contrast ratio of white text on `hex_color`."""
    return _contrast("#ffffff", hex_color)


def _hover_shade(hex_color: str, amount: int = 25) -> str:
    """Mirrors applyWidgetConfig()'s hover derivation in osa-chat-widget.js: subtract
    `amount` from each channel, floored at 0."""
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{max(0, c - amount):02x}" for c in (r, g, b))


class TestTheWidgetColors:
    """NEMAR's home page search button is nemar.org's brand teal (#5bbad5) with dark
    text (#04121f): white text on that teal is only 2.2:1, well below the 4.5:1 body
    text needs, which is why the widget matches the button with theme_text_color and
    user_bubble_text_color instead of the platform's white. accent_color reuses the
    OLDER, darkened teal (#257a92) as a foreground on the widget's white panel, where
    it needs its own 4.5:1 against white rather than against a surface."""

    def test_the_brand_teal_itself_fails_white_text(self) -> None:
        """Documents why NEMAR does not draw white text on its surfaces (the previous
        design's own darkened teal existed only to fix this for white text; this
        design fixes it by pairing the undarkened teal with dark text instead)."""
        assert _contrast_with_white("#5bbad5") < 4.5

    def test_theme_and_bubble_text_are_readable_on_their_own_surfaces(
        self, nemar: CommunityConfig
    ) -> None:
        assert nemar.widget is not None
        for surface_name, text_name in (
            ("theme_color", "theme_text_color"),
            ("user_bubble_color", "user_bubble_text_color"),
        ):
            surface = getattr(nemar.widget, surface_name)
            text = getattr(nemar.widget, text_name)
            assert surface is not None, f"NEMAR sets no {surface_name}"
            assert text is not None, f"NEMAR sets no {text_name}"
            assert _contrast(text, surface) >= 4.5, (surface_name, text_name, surface, text)

    def test_theme_text_color_stays_readable_on_the_hover_shade(
        self, nemar: CommunityConfig
    ) -> None:
        """The widget derives its hover background by subtracting 25 per channel from
        theme_color (osa-chat-widget.js, applyWidgetConfig); theme_text_color must
        still read on THAT shade too, not just on theme_color itself."""
        assert nemar.widget is not None
        assert nemar.widget.theme_color is not None
        assert nemar.widget.theme_text_color is not None
        hover = _hover_shade(nemar.widget.theme_color)
        assert _contrast(nemar.widget.theme_text_color, hover) >= 4.5, hover

    def test_accent_color_is_readable_on_the_white_panel(self, nemar: CommunityConfig) -> None:
        assert nemar.widget is not None
        assert nemar.widget.accent_color is not None
        assert _contrast_with_white(nemar.widget.accent_color) >= 4.5


class TestTheLockOverlay:
    """NEMAR's own entries. That the overlay verifies and is what its wheels produce is
    checked for every community in test_shipped_runtimes.py."""

    def test_it_adds_zarr_and_eegprep_lean(self, nemar: CommunityConfig) -> None:
        assert nemar.runtime is not None and nemar.runtime.python is not None
        overlay = load_runtime_lock(NEMAR_DIR, nemar.runtime.python.lockfile or "")
        assert set(overlay.packages) == {"zarr", "eegprep-lean"}

    def test_every_preload_package_is_one_the_lock_can_name(self, nemar: CommunityConfig) -> None:
        """The overlay's own; the distribution's are checked against Pyodide's lock in
        test-data-lane.js, which has it."""
        assert nemar.runtime is not None and nemar.runtime.python is not None
        overlay = load_runtime_lock(NEMAR_DIR, nemar.runtime.python.lockfile or "")
        assert {"zarr", "eegprep-lean"} <= set(nemar.runtime.python.preload)
        assert overlay.packages["eegprep-lean"].depends == ["matplotlib", "numpy", "zarr"]


def _wheel(path: Path, name: str, version: str, metadata: str | None = None) -> None:
    """A real, minimal wheel: one package and the METADATA the generator reads."""
    if metadata is None:
        metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(f"{name}/__init__.py", "")
        wheel.writestr(f"{name}-{version}.dist-info/METADATA", metadata)


def _add_depends(runtime: Path, line: str) -> None:
    depends = runtime / "depends.toml"
    depends.write_text(depends.read_text() + line + "\n")


# Each breaks a copy of NEMAR's runtime folder one way, and names what the error says.
_BROKEN: dict[str, tuple[Callable[[Path], None], str]] = {
    "two wheels for one package": (
        lambda rt: _wheel(rt / "wheels" / "zarr-9.0-py3-none-any.whl", "zarr", "9.0"),
        "two wheels provide zarr",
    ),
    "a depends line with no wheel": (
        lambda rt: _add_depends(rt, "ghost = []"),
        "names packages with no wheel: ghost",
    ),
    "depends that is not a list": (
        lambda rt: (
            _wheel(rt / "wheels" / "tiny-1.0-py3-none-any.whl", "tiny", "1.0"),
            _add_depends(rt, 'tiny = "numpy"'),
        ),
        "tiny must be a list of package names",
    ),
    "no depends.toml": (
        lambda rt: (rt / "depends.toml").unlink(),
        "depends.toml cannot be read",
    ),
    "a depends.toml that is not TOML": (
        lambda rt: (rt / "depends.toml").write_text("zarr = [\n"),
        "depends.toml cannot be read",
    ),
    "a wheel that is not a zip": (
        lambda rt: (rt / "wheels" / "broken-1.0-py3-none-any.whl").write_bytes(b"not a zip"),
        "broken-1.0-py3-none-any.whl is not a readable wheel",
    ),
    "a wheel with no METADATA": (
        lambda rt: zipfile.ZipFile(rt / "wheels" / "bare-1.0-py3-none-any.whl", "w").close(),
        "expected one .dist-info/METADATA, found 0",
    ),
    "a wheel whose METADATA has no Name": (
        lambda rt: _wheel(
            rt / "wheels" / "nameless-1.0-py3-none-any.whl",
            "nameless",
            "1.0",
            metadata="Metadata-Version: 2.1\nVersion: 1.0\n",
        ),
        "METADATA has no Name or no Version",
    ),
    "a wheel the browser cannot install": (
        lambda rt: (
            _wheel(rt / "wheels" / "native-1.0-cp313-cp313-linux_x86_64.whl", "native", "1.0"),
            _add_depends(rt, "native = []"),
        ),
        "the overlay would not load",
    ),
    "a committed overlay that does not load": (
        lambda rt: (rt / "nemar-pyodide-lock.json").write_text('{"packages": {}}'),
        "the committed overlay",
    ),
}


class TestTheGenerator:
    """scripts/build_runtime_lock.py, against a copy of NEMAR's runtime folder."""

    @pytest.fixture
    def runtime_copy(self, tmp_path: Path) -> Path:
        shutil.copytree(NEMAR_DIR / "runtime", tmp_path / "runtime")
        return tmp_path / "runtime" / "nemar-pyodide-lock.json"

    def test_it_regenerates_the_committed_overlay_exactly(self, runtime_copy: Path) -> None:
        committed = runtime_copy.read_text()
        runtime_copy.unlink()

        assert build_runtime_lock.main([str(runtime_copy)]) == 0
        assert runtime_copy.read_text() == committed

    def test_new_bytes_under_a_committed_name_are_refused(
        self, runtime_copy: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Wheels are served as immutable for a year, so a rebuilt wheel under an old
        name would reach some readers and not others."""
        wheel = runtime_copy.parent / "wheels" / "eegprep_lean-0.1.0.dev2-py3-none-any.whl"
        wheel.write_bytes(wheel.read_bytes() + b"\0")
        before = runtime_copy.read_text()

        assert build_runtime_lock.main([str(runtime_copy)]) == 1
        assert "new version" in capsys.readouterr().err
        assert runtime_copy.read_text() == before, "and the overlay is left as it was"

    def test_a_wheel_without_a_depends_line_is_refused(
        self, runtime_copy: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        depends = runtime_copy.parent / "depends.toml"
        depends.write_text(
            "\n".join(
                line
                for line in depends.read_text().splitlines()
                if not line.startswith("eegprep-lean")
            )
        )

        assert build_runtime_lock.main([str(runtime_copy)]) == 1
        assert "has no line for eegprep-lean" in capsys.readouterr().err

    @pytest.mark.parametrize("case", list(_BROKEN))
    def test_what_it_cannot_reconcile_it_names_and_does_not_write(
        self, runtime_copy: Path, capsys: pytest.CaptureFixture[str], case: str
    ) -> None:
        breakage, message = _BROKEN[case]
        breakage(runtime_copy.parent)
        before = runtime_copy.read_text()

        assert build_runtime_lock.main([str(runtime_copy)]) == 1
        assert message in capsys.readouterr().err
        assert runtime_copy.read_text() == before

    def test_check_fails_on_a_stale_overlay(self, runtime_copy: Path) -> None:
        overlay = json.loads(runtime_copy.read_text())
        overlay["packages"]["zarr"]["depends"].remove("numpy")
        runtime_copy.write_text(json.dumps(overlay, indent=2) + "\n")

        assert build_runtime_lock.main(["--check", str(runtime_copy)]) == 1

    def test_entries_record_the_wheels_own_facts(self, runtime_copy: Path) -> None:
        overlay = build_runtime_lock.build_overlay(runtime_copy)
        wheel = runtime_copy.parent / "wheels" / "zarr-3.4.0-py3-none-any.whl"
        zarr = overlay["packages"]["zarr"]
        assert (zarr["name"], zarr["version"], zarr["imports"]) == ("zarr", "3.4.0", ["zarr"])
        assert zarr["sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()


def _sources_problems(runtime_dir: Path) -> list[str]:
    """What's wrong between `wheels/` and `sources.toml` in `runtime_dir`, if anything:
    a wheel no entry names, or an entry that names no wheel (missing key, or a name
    that is not in `wheels/`). Empty when every wheel and every entry agree."""
    sources = tomllib.loads((runtime_dir / "sources.toml").read_text())
    wheel_files = {path.name for path in (runtime_dir / "wheels").glob("*.whl")}

    named: dict[str, str] = {}
    problems: list[str] = []
    for key, entry in sources.items():
        wheel = entry.get("wheel") if isinstance(entry, dict) else None
        if not isinstance(wheel, str) or not wheel:
            problems.append(f"{key}: names no wheel")
            continue
        named[key] = wheel
        if wheel not in wheel_files:
            problems.append(f"{key}: names {wheel}, which is not in wheels/")

    for orphan in sorted(wheel_files - set(named.values())):
        problems.append(f"{orphan}: no sources.toml entry names it")
    return problems


class TestSourcesToml:
    """`src/assistants/nemar/runtime/sources.toml`: where each vendored wheel came
    from. Read by scripts/eegprep_lean_drift.py and its weekly workflow; nothing in
    the load path above checks this, so it needs its own test."""

    def test_every_wheel_and_every_entry_agree(self) -> None:
        assert _sources_problems(NEMAR_DIR / "runtime") == []

    def test_the_build_command_builds_the_recorded_commit(self) -> None:
        """The command repeats the commit, so the two could drift apart unnoticed."""
        entry = tomllib.loads((NEMAR_DIR / "runtime" / "sources.toml").read_text())["eegprep-lean"]

        assert f"git archive {entry['commit']} " in entry["build"]
        assert "SOURCE_DATE_EPOCH=" in entry["build"]

    def test_a_wheel_with_no_entry_is_named(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        shutil.copytree(NEMAR_DIR / "runtime", runtime)
        _wheel(runtime / "wheels" / "ghost-1.0-py3-none-any.whl", "ghost", "1.0")

        assert _sources_problems(runtime) == [
            "ghost-1.0-py3-none-any.whl: no sources.toml entry names it"
        ]

    def test_an_entry_that_names_no_wheel_is_named(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        shutil.copytree(NEMAR_DIR / "runtime", runtime)
        sources = runtime / "sources.toml"
        sources.write_text(sources.read_text() + '\n[ghost]\nsource = "nowhere"\n')

        assert _sources_problems(runtime) == ["ghost: names no wheel"]

    def test_an_entry_naming_a_missing_wheel_is_named(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        shutil.copytree(NEMAR_DIR / "runtime", runtime)
        sources = runtime / "sources.toml"
        sources.write_text(
            sources.read_text() + '\n[ghost]\nwheel = "ghost-1.0-py3-none-any.whl"\n'
        )

        assert _sources_problems(runtime) == [
            "ghost: names ghost-1.0-py3-none-any.whl, which is not in wheels/"
        ]
