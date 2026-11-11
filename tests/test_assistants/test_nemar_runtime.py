"""NEMAR runs code in the reader's browser, with the runtime its shipped files describe.

Reads the real `src/assistants/nemar/` folder: the config the app loads, the lock
overlay beside it, and the wheels it pins. What the runtime does with them in a real
Pyodide is `frontend/test-data-lane.js`; this is the server's side of the same files.
"""

from __future__ import annotations

import ast
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
from scripts.plan_consistency import normalize_step
from src.core.config.community import MAX_PROCEDURE_CHARS, CommunityConfig
from src.core.config.runtime_lock import load_runtime_lock

ROOT = Path(__file__).resolve().parents[2]
NEMAR_DIR = ROOT / "src" / "assistants" / "nemar"


@pytest.fixture(scope="module")
def nemar() -> CommunityConfig:
    return CommunityConfig.from_yaml(NEMAR_DIR / "config.yaml")


# NEMAR's seven procedures (#719, #721): the documents the model fetches with
# `get_procedure`, and the sequence of operations each one's plan must normalize to in
# scripts/plan_consistency.py, the check that measures whether plans agree.
SPECTRUM = "power-spectrum"
BAND_POWER = "band-power"
BAND_TOPOGRAPHY = "band-power-topography"
ERP = "erp-by-condition"
ERP_TOPOGRAPHY = "erp-topography"
ERP_IMAGE = "erp-image-by-condition"
TIME_FREQUENCY = "time-frequency-by-condition"
DECLARED_SEQUENCES = {
    SPECTRUM: ("spectrum", "plot"),
    BAND_POWER: ("spectrum", "plot"),
    BAND_TOPOGRAPHY: ("spectrum", "plot"),
    ERP: ("load", "filter", "epoch", "average", "plot"),
    ERP_TOPOGRAPHY: ("load", "filter", "epoch", "average", "plot"),
    ERP_IMAGE: ("load", "filter", "epoch", "plot"),
    # Decided in the document: no filter step, since the wavelet is already band-selective.
    TIME_FREQUENCY: ("load", "epoch", "spectrum", "plot"),
}
TOPOGRAPHIES = (BAND_TOPOGRAPHY, ERP_TOPOGRAPHY)
# The 30 EEG labels of nm000132 (ERP CORE), as the hand run on its sub-001 recording
# (sub-001/eeg/sub-001_task-N170_eeg.set, group eeg_250hz) printed them, in that order,
# after leaving out HEOG_left, HEOG_right and VEOG_lower.
NM000132_EEG_LABELS = (
    "FP1", "F3", "F7", "FC3", "C3", "C5", "P3", "P7", "P9", "PO7",
    "PO3", "O1", "Oz", "Pz", "CPz", "FP2", "Fz", "F4", "F8", "FC4",
    "FCz", "Cz", "C4", "C6", "P4", "P8", "P10", "PO8", "PO4", "O2",
)  # fmt: skip
# The consistency dataset xx099905 has three EEG channels.
XX099905_EEG_LABELS = ("C3", "Cz", "C4")
# The aliases and extras the position table keeps beside the 10-10 labels: the old names
# (T3 to T6 are T7, T8, P7, P8), the 10-20 extras and the reference sites.
TABLE_ALIASES_AND_EXTRAS = (
    "T3", "T4", "T5", "T6", "P9", "P10", "T9", "T10", "Iz", "A1", "A2", "M1", "M2", "Fpz",
)  # fmt: skip
# A 10-10 label: Fp, AF, F, FT, FC, T, C, TP, CP, P, PO or O, then a number or z.
TEN_TEN_LABEL = re.compile(r"(Fp|AF|F|FT|FC|T|C|TP|CP|P|PO|O)(\d+|z)")
# The two topography documents measure about 15.3k and 16.8k characters, against the issue's
# target of about 12k (a test cap of 12.5k): the 10-10 table is 2.3k of them, and the rest is
# the shared plotting code, the byte-identical Welch step and each document's own steps, none
# of which can go without breaking a test that pins it. The cap sits just above the larger
# document so that growth shows; going lower is the maintainer's call (see #721).
TOPOGRAPHY_SIZE_CAP = 17_000
PYTHON_FENCE = re.compile(r"^```python\n([\s\S]*?)\n```", re.MULTILINE)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _plan_titles(document: str) -> list[str]:
    """The step titles a document's ``## Plan`` section lists, in order."""
    plan = document.split("## Plan", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\d+\. \*\*(.+?)\*\*", plan, flags=re.MULTILINE)


class TestTheShippedConfig:
    def test_the_scalp_map_is_among_the_three_dataset_page_questions_a_zarr_dataset_shows(
        self, nemar: CommunityConfig
    ) -> None:
        """The widget shows the first three templates that fit on the opening screen (the
        first two in the compact row), so a template placed later is never seen."""
        assert nemar.widget is not None
        templates = nemar.widget.dataset_suggested_questions
        shown = [t.text for t in templates][:3]
        assert any("scalp map" in text for text in shown), shown
        # A dataset without a Zarr copy skips every needs_zarr one and keeps three.
        assert sum(1 for t in templates if not t.needs_zarr) >= 3

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

    def test_the_tool_names_every_package_the_runtime_preloads(
        self, nemar: CommunityConfig
    ) -> None:
        """The model reads this on every call, and it says nothing else can be installed,
        so a preloaded package it leaves out is one the model will not use: SciPy was
        preloaded before the description said so (#495). The spectrum rule is here too: the
        recording is read in pieces sized by the budget and welch's limit, one channel of
        a piece at a time, averaged by segment count, since past either limit the call
        fails in this runtime and a capped read would report a part of the recording as
        the whole."""
        assert nemar.extensions is not None and nemar.runtime is not None
        assert nemar.runtime.python is not None
        description = nemar.extensions.client_tools[0].description.lower()
        head, marker, _ = description.partition("are installed and nothing else can be")
        assert marker, "the description no longer says what is installed"
        installed = head.rsplit(".", 1)[-1]
        for package in nemar.runtime.python.preload:
            assert package.replace("-", "_") in installed, (package, installed)
        flat = " ".join(description.split())
        assert "read the recording in pieces sized by the reader's memory budget" in flat
        assert "and by welch's limit" in flat
        assert "scipy.signal.welch on one channel of a piece at a time" in flat
        assert "average over the pieces weighted by their segment counts" in flat
        # The old recipe, which read the whole recording first and chunked it in memory.
        assert "chunks of at most 2**28 // nperseg samples" not in flat
        assert "spectrum of the whole recording" in flat
        assert "array is too big" in flat
        # The capped recipe is gone: no read cut at a maximum, and no "on at most".
        assert "on at most" not in flat

    @pytest.mark.parametrize("procedure_id", [SPECTRUM, BAND_POWER])
    def test_the_spectrum_recipe_reads_pieces_and_covers_the_whole_recording(
        self, nemar: CommunityConfig, procedure_id: str
    ) -> None:
        """The recipe reads the recording in pieces, welches each channel of each piece,
        weights the pieces by their segment counts, takes its piece from the reader's
        memory budget as well as from the runtime's own limit, and names no `most` cap and
        no "cut short" sentence (E5.2, #669). The block's budget line is the one number the
        model edits besides the group, and the prose says so (the data lane suite runs the
        block and holds it to one welch call). Since #719 it lives in the procedure
        documents that use the spectrum, not in the prompt."""
        flat = _flat(nemar.procedure_documents()[procedure_id])
        assert (
            "piece = min(2**28 // nperseg, budget_mb * 2**20 // 4 // (group.n_channels * 8 + 160))"
            in flat
        )
        assert "await eegprep_lean.read_window(" in flat
        # The block stands alone: it opens its own index and store, which the model fills in.
        assert 'index = await eegprep_lean.read_index("DATASET_ID")' in flat
        assert 'store = index.store("RECORDING_PATH")' in flat
        assert "`DATASET_ID`, `RECORDING_PATH` and `GROUP`" in flat
        assert "left_out" in flat
        assert "summed[row] += k * p" in flat
        assert "power = summed / used" in flat
        assert "assert used == total" in flat
        # The totals come from the recording's length, not from one piece's.
        assert "total = (group.n_samples - noverlap) // hop" in flat
        assert "eeg.shape[1]" not in flat
        assert flat.count("budget_mb = 512") == 1
        assert "`budget_mb` with the reader's budget" in flat and "change nothing else" in flat
        assert "most =" not in flat and "[:most]" not in flat
        assert "cut the read short" not in flat
        assert "when `most`" not in flat
        assert "chunk" not in flat.replace("chunks", "")

    def test_the_prompt_points_at_the_documents_for_the_channel_count(
        self, nemar: CommunityConfig
    ) -> None:
        flat = _flat(nemar.system_prompt)
        assert "(the code in the procedure documents prints the count" in flat
        assert "the block below prints the count" not in flat

    def test_the_prompt_says_where_each_value_comes_from(self, nemar: CommunityConfig) -> None:
        """index.store matches a store's path only, and nemar_read_window also accepts
        the zarr name, so the prompt must name the field."""
        prompt = nemar.system_prompt
        assert "nemar_list_recordings" in prompt
        assert "`path`" in prompt and "never its `zarr` name" in prompt

    def test_it_reaches_only_the_data_plane(self, nemar: CommunityConfig) -> None:
        """Production's data plane in production, staging's on develop, which test.nemar.org
        embeds and whose datasets only the staging host has (#480)."""
        assert nemar.runtime is not None
        production = nemar.runtime.for_deployment("production").python
        develop = nemar.runtime.for_deployment("develop").python
        assert production is not None and develop is not None
        assert production.fetch_allow == ["https://zarr.nemar.org/"]
        assert develop.fetch_allow == ["https://zarr-test.nemar.org/"]

    def test_the_develop_prelude_points_read_index_at_the_staging_host(
        self, nemar: CommunityConfig
    ) -> None:
        """read_index takes its URL from eegprep_lean.index.INDEX_URL_TEMPLATE at each call;
        on develop the prelude sets it to the host fetch_allow names, and production's
        prelude leaves eegprep-lean's own default alone."""
        assert nemar.runtime is not None
        develop = nemar.runtime.for_deployment("develop").python
        production = nemar.runtime.for_deployment("production").python
        assert develop is not None and production is not None
        assert (
            'eegprep_lean.index.INDEX_URL_TEMPLATE = "https://zarr-test.nemar.org/{dataset_id}/zarr/index.json"'
            in (develop.prelude or "")
        )
        assert develop.fetch_allow[0].rstrip("/") in (develop.prelude or "")
        assert "INDEX_URL_TEMPLATE" not in (production.prelude or "")

    def test_its_prelude_hands_eegprep_lean_the_runtime_client(
        self, nemar: CommunityConfig
    ) -> None:
        """Without it, eegprep-lean reaches for pyodide.http, which the seal removes,
        and the recipe fails with an import error that names neither."""
        assert nemar.runtime is not None
        for deployment in ("production", "develop"):
            python = nemar.runtime.for_deployment(deployment).python
            assert python is not None
            assert "set_default_transport(eegprep_lean.FetchTransport(osa.fetch))" in (
                python.prelude or ""
            ), deployment

    def test_the_prompt_asks_for_one_condition_per_events_call(
        self, nemar: CommunityConfig
    ) -> None:
        """On nemar.org (2026-09-24) a model asked for every stimulus of ERP CORE's N170
        recording in one call, sorted the 320 rows into faces and scrambled faces while
        copying them into code, and averaged 282 "faces" of a recording that has 80. The
        prompt asks for one condition per call and an assert on each list's length, so a
        copying slip stops the run; this keeps both instructions from being dropped.
        Compared with whitespace collapsed, so reflowing the prose does not break it."""
        prompt = " ".join(nemar.system_prompt.split())
        assert "ask for one condition per call" in prompt
        assert 'where: {"event_type": ["face"]}' in prompt
        assert "Never sort one call's mixed rows into conditions by hand" in prompt
        assert "assert len(face) == 80" in prompt
        # A condition longer than `limit` comes back truncated; without this, the
        # assert fires and a model may "fix" it by matching the short list.
        assert "when `truncated` is true" in prompt

    def test_the_prompt_leaves_out_non_eeg_channels_by_substring(
        self, nemar: CommunityConfig
    ) -> None:
        """ERP CORE labels its EOG channels HEOG_left, HEOG_right and VEOG_lower, so a
        model that excluded "HEOG" and "VEOG" by exact name kept all three."""
        prompt = " ".join(nemar.system_prompt.split())
        assert "`HEOG_left`, `HEOG_right` and `VEOG_lower`" in prompt
        assert "`EOG`, `ECG`, `EKG` or `EMG`" in prompt
        assert "anywhere in the label" in prompt

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
        wheel = runtime_copy.parent / "wheels" / "eegprep_lean-0.5.0-py3-none-any.whl"
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


# What the system prompt no longer carries and each document does (#719). The phrases are
# the moved text itself, not a paraphrase: the prompt keeps the short rules every analysis
# needs and the documents carry the procedures.
MOVED_OUT_OF_THE_PROMPT = [
    "signal.welch",
    "sosfiltfilt",
    "ONE CHANNEL AT A TIME",
    "piece = min(2**28 // nperseg",
    "array is too big",
    "a third of its lowest value at the bottom",
    "signal.butter(4, cutoff",
    "drop epochs over 100",
    "Order the rows, then average each row with its neighbors",
    "95th percentile of the absolute values",
    "mark each response on the image",
    "**Power spectrum.**",
    "**Epochs and ERP images.**",
]
MOVED_INTO_DOCUMENTS = {
    SPECTRUM: [
        "ONE CHANNEL AT A TIME",
        "piece = min(2**28 // nperseg, budget_mb * 2**20 // 4 // (group.n_channels * 8 + 160))",
        "array is too big",
        "a third of its lowest value at the bottom",
        "Name any peak you describe with its channel and frequency",
    ],
    BAND_POWER: [
        "ONE CHANNEL AT A TIME",
        "piece = min(2**28 // nperseg, budget_mb * 2**20 // 4 // (group.n_channels * 8 + 160))",
        "array is too big",
        "trapezoid",
    ],
    ERP: [
        "signal.butter(4, cutoff",
        "`sosfiltfilt` runs the filter forward and back, so nothing shifts in time",
        "subtract each epoch's pre-event mean, and drop epochs over 100",
        "PO8 for ERP CORE's N170",
    ],
    ERP_IMAGE: [
        "signal.butter(4, cutoff",
        "`sosfiltfilt` runs the filter forward and back, so nothing shifts in time",
        "Order the rows, then average each row with its neighbors (five epochs)",
        "at the 95th percentile of the absolute values of every panel's smoothed rows",
        "Put rows with no response last, and mark each response on the image",
        "say that this ordering is built in and is not a finding",
        "PO8 for ERP CORE's N170",
    ],
}


class TestTheProceduresLeaveThePrompt:
    def test_nemar_declares_its_procedures(self, nemar: CommunityConfig) -> None:
        assert {p.id for p in nemar.procedures} >= set(DECLARED_SEQUENCES)

    @pytest.mark.parametrize("phrase", MOVED_OUT_OF_THE_PROMPT)
    def test_the_moved_text_is_gone_from_the_prompt(
        self, nemar: CommunityConfig, phrase: str
    ) -> None:
        assert phrase not in _flat(nemar.system_prompt)

    def test_the_short_rules_every_analysis_needs_stay(self, nemar: CommunityConfig) -> None:
        flat = _flat(nemar.system_prompt)
        assert "### Spectra, events and ERP images" in flat
        assert "Read a recording once and compute everything from that read" in flat
        assert "The first figure is often all a reader looks at" in flat
        assert "**Which channels.**" in flat
        assert "**Events.**" in flat
        assert "sample_index" in flat and "assert len(face) == 80" in flat
        assert (
            "When `execute_code` is not offered, this page cannot run code: give the recipe."
            in flat
        )

    def test_the_worked_example_stays(self, nemar: CommunityConfig) -> None:
        flat = _flat(nemar.system_prompt)
        assert "**Worked example.** `nm000132` (ERP CORE)" in flat
        assert "sub-001/eeg/sub-001_task-N170_eeg.set" in flat
        assert "Use it when a user asks what this can do" in flat

    @pytest.mark.parametrize(("procedure_id", "phrases"), MOVED_INTO_DOCUMENTS.items())
    def test_each_document_carries_its_moved_text(
        self, nemar: CommunityConfig, procedure_id: str, phrases: list[str]
    ) -> None:
        flat = _flat(nemar.procedure_documents()[procedure_id])
        for phrase in phrases:
            assert phrase in flat, (procedure_id, phrase)


class TestEachDocumentDeclaresItsPlan:
    @pytest.mark.parametrize(("procedure_id", "sequence"), DECLARED_SEQUENCES.items())
    def test_the_step_titles_normalize_to_the_declared_sequence(
        self, nemar: CommunityConfig, procedure_id: str, sequence: tuple[str, ...]
    ) -> None:
        titles = _plan_titles(nemar.procedure_documents()[procedure_id])
        assert tuple(normalize_step({"title": title, "detail": ""}) for title in titles) == sequence

    def test_the_band_power_document_decides_the_integration_once(
        self, nemar: CommunityConfig
    ) -> None:
        flat = _flat(nemar.procedure_documents()[BAND_POWER])
        assert "ONE step, decided here once" in flat
        assert "never a separate read step" in flat
        titles = _plan_titles(nemar.procedure_documents()[BAND_POWER])
        assert titles == ["Compute the band power", "Plot the band power"]

    def test_the_spectrum_document_has_no_separate_read_step(self, nemar: CommunityConfig) -> None:
        titles = _plan_titles(nemar.procedure_documents()[SPECTRUM])
        assert titles == ["Compute the power spectrum", "Plot the power spectrum"]
        assert "There is no separate read step" in _flat(nemar.procedure_documents()[SPECTRUM])

    def test_every_step_says_what_its_one_run_does_and_its_memory(
        self, nemar: CommunityConfig
    ) -> None:
        for procedure_id in DECLARED_SEQUENCES:
            plan = nemar.procedure_documents()[procedure_id].split("## Plan", 1)[1]
            plan = plan.split("\n## ", 1)[0]
            steps = re.split(r"^\d+\. \*\*", plan, flags=re.MULTILINE)[1:]
            assert len(steps) == len(DECLARED_SEQUENCES[procedure_id]), procedure_id
            for step in steps:
                assert "Memory:" in step, (procedure_id, step[:60])
                assert "one run" in step, (procedure_id, step[:60])

    def test_the_erp_documents_share_their_first_three_steps(self, nemar: CommunityConfig) -> None:
        by_condition = _plan_titles(nemar.procedure_documents()[ERP])
        image = _plan_titles(nemar.procedure_documents()[ERP_IMAGE])
        assert by_condition[:3] == image[:3]
        assert by_condition[-1] == "Plot the ERPs"
        assert image[-1] == "Plot the ERP images"

    def test_a_document_that_runs_code_uses_the_names_the_runs_share(
        self, nemar: CommunityConfig
    ) -> None:
        """Runs share one Python namespace, so later steps name earlier steps' variables."""
        spectrum = _flat(nemar.procedure_documents()[SPECTRUM])
        assert "share one Python namespace" in spectrum
        assert "`freqs` and `power`" in spectrum
        band = _flat(nemar.procedure_documents()[BAND_POWER])
        assert "`band_power` and `bands`" in band

    @pytest.mark.parametrize("procedure_id", list(DECLARED_SEQUENCES))
    def test_its_code_fences_parse(self, nemar: CommunityConfig, procedure_id: str) -> None:
        fences = PYTHON_FENCE.findall(nemar.procedure_documents()[procedure_id])
        assert fences
        for body in fences:
            compile(
                body, procedure_id, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT | ast.PyCF_ONLY_AST
            )

    def test_the_band_integration_follows_the_welch_block_in_the_same_run(
        self, nemar: CommunityConfig
    ) -> None:
        document = nemar.procedure_documents()[BAND_POWER]
        welch, integration = PYTHON_FENCE.findall(document)
        assert "signal.welch" in welch
        # It uses what the welch block made (freqs, power, keep) and nothing it did not.
        for name in ("freqs", "power", "keep", "trapezoid"):
            assert name in integration
        assert "read_window" not in integration
        assert "directly after that block" in _flat(document)


def _fence_with(document: str, marker: str) -> str:
    """The one python fence of a document that contains ``marker``."""
    [fence] = [body for body in PYTHON_FENCE.findall(document) if marker in body]
    return fence


def _the_matching(document: str, capsys: pytest.CaptureFixture[str]) -> tuple[dict, Callable]:
    """The document's own position table and matching code, run: (table, ``locate``)."""
    table_source = _fence_with(document, "positions = {")
    table = ast.literal_eval(table_source.split("=", 1)[1].strip())
    scope: dict = {"positions": table}
    exec(_fence_with(document, "def locate("), scope)  # the document's own code
    capsys.readouterr()
    return table, scope["locate"]


class TestTheTopographyAndTimeFrequencyDocuments:
    """Three documents (#721) that reuse blocks of the earlier four, and share their own."""

    @pytest.fixture(scope="class")
    def documents(self, nemar: CommunityConfig) -> dict[str, str]:
        return nemar.procedure_documents()

    def test_every_document_has_headroom_under_the_size_limit(
        self, documents: dict[str, str]
    ) -> None:
        for procedure_id, document in documents.items():
            assert len(document) < 0.875 * MAX_PROCEDURE_CHARS, (procedure_id, len(document))

    def test_the_band_topography_reuses_the_band_power_blocks_byte_for_byte(
        self, documents: dict[str, str]
    ) -> None:
        welch, integration = PYTHON_FENCE.findall(documents[BAND_POWER])
        assert _fence_with(documents[BAND_TOPOGRAPHY], "signal.welch(") == welch
        assert _fence_with(documents[BAND_TOPOGRAPHY], "trapezoid(") == integration
        # The whole step is the same text, so the prose around the blocks cannot drift.
        step = documents[BAND_POWER].split("## Step 1", 1)[1].split("\n## ", 1)[0]
        assert step in documents[BAND_TOPOGRAPHY]

    def test_the_erp_topography_reuses_the_erp_filter_block_byte_for_byte(
        self, documents: dict[str, str]
    ) -> None:
        [erp_filter] = PYTHON_FENCE.findall(documents[ERP])
        assert _fence_with(documents[ERP_TOPOGRAPHY], "signal.butter(4, cutoff") == erp_filter

    @pytest.mark.parametrize("marker", ["positions = {", "def locate(", "def scalp_maps("])
    def test_the_topographies_share_the_positions_matching_and_drawing_blocks(
        self, documents: dict[str, str], marker: str
    ) -> None:
        band, erp = (_fence_with(documents[procedure_id], marker) for procedure_id in TOPOGRAPHIES)
        assert band == erp

    @pytest.mark.parametrize("procedure_id", TOPOGRAPHIES)
    def test_the_table_is_the_1010_positions_and_aliases_to_three_decimals(
        self, documents: dict[str, str], procedure_id: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        table, _ = _the_matching(documents[procedure_id], capsys)
        # The aliases and the extras the readers' recordings use.
        assert set(TABLE_ALIASES_AND_EXTRAS) <= set(table)
        for name, (x, y) in table.items():
            assert (x, y) == (round(x, 3), round(y, 3)), name
            assert x**2 + y**2 < 0.65**2, name  # on or near a head of radius 0.5
        assert table["Cz"] == (0.0, 0.0)
        # The nose is up, the right ear at the right: Fpz in front of Oz, T8 right of T7.
        assert table["Fpz"][1] > 0.3 > -0.3 > table["Oz"][1]
        assert table["T8"][0] > 0.4 and table["T7"][0] < -0.4
        # An old name is the position of its new one.
        assert table["T3"] == table["T7"] and table["T6"] == table["P8"]
        assert {"C3", "Cz", "C4"} <= set(table)
        # The 10-05 labels (AFp1h, FFC1h, ...) are left out: a name is a 10-10 label or an extra.
        for name in set(table) - set(TABLE_ALIASES_AND_EXTRAS):
            assert TEN_TEN_LABEL.fullmatch(name), name
        flat = _flat(documents[procedure_id])
        assert "MNE's `standard_1005`" in flat
        assert f"The positions are {len(table)} scalp positions" in flat

    def test_the_two_topographies_carry_the_same_table_and_stay_small(
        self, documents: dict[str, str]
    ) -> None:
        # (The two position fences are byte-identical: the marker test above pins that.)
        band = _fence_with(documents[BAND_TOPOGRAPHY], "positions = {")
        # The 10-10 table is a fence of about 2.3k characters; the 343-label one was 9k.
        assert len(band) < 3_000
        for procedure_id in TOPOGRAPHIES:
            assert len(documents[procedure_id]) <= TOPOGRAPHY_SIZE_CAP, procedure_id

    def test_the_changelog_names_the_real_count_of_positions(
        self, documents: dict[str, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        table, _ = _the_matching(documents[BAND_TOPOGRAPHY], capsys)
        changelog = (ROOT / "CHANGELOG.md").read_text()
        assert f"one table of {len(table)} standard positions" in changelog

    @pytest.mark.parametrize("procedure_id", TOPOGRAPHIES)
    @pytest.mark.parametrize("labels", [NM000132_EEG_LABELS, XX099905_EEG_LABELS])
    def test_every_eeg_label_of_the_two_datasets_has_a_position_after_the_matching(
        self,
        documents: dict[str, str],
        procedure_id: str,
        labels: tuple[str, ...],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, locate = _the_matching(documents[procedure_id], capsys)
        found, points = locate(list(labels))
        assert list(found) == list(range(len(labels)))
        assert points.shape == (len(labels), 2)
        assert "left out, no position: none" in capsys.readouterr().out

    @pytest.mark.parametrize("procedure_id", TOPOGRAPHIES)
    def test_the_matching_ignores_case_prefix_and_reference_and_says_what_it_left_out(
        self, documents: dict[str, str], procedure_id: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, locate = _the_matching(documents[procedure_id], capsys)
        labels = ["EEG Fp1-REF", "eeg cz-le", "Pz-AVG", "HEOG_left", "T3", "T7", "Foo9"]
        found, points = locate(labels)
        assert list(found) == [0, 1, 2, 4]  # T7 is T3's place, so only the first is kept
        assert points.shape == (4, 2)
        printed = capsys.readouterr().out
        assert "HEOG_left" in printed and "Foo9" in printed and "T7" in printed
        none, nothing = locate(["HEOG_left"])
        assert len(none) == 0 and nothing.shape == (0, 2)

    @pytest.mark.parametrize("procedure_id", TOPOGRAPHIES)
    def test_each_topography_plan_puts_a_caveat_in_the_findings_and_still_proposes(
        self, documents: dict[str, str], procedure_id: str
    ) -> None:
        plan = _flat(documents[procedure_id].split("## Plan", 1)[1].split("\n## ", 1)[0])
        assert "fewer than 8" in plan
        assert "put a caveat in the plan's `findings`" in plan
        assert "only 3 channels" in plan
        assert "and still propose the plan" in plan
        assert "Never end the turn to ask" in plan

    @pytest.mark.parametrize("procedure_id", TOPOGRAPHIES)
    def test_the_drawing_block_draws_markers_only_below_eight_positioned_channels(
        self, documents: dict[str, str], procedure_id: str
    ) -> None:
        scalp = _fence_with(documents[procedure_id], "def scalp_maps(")
        assert "if len(xy) >= 8:" in scalp
        assert "RBFInterpolator" in scalp
        for part in ("Circle(", "Polygon(", "colorbar(", "suptitle("):  # head, nose, bar, title
            assert part in scalp

    def test_the_erp_topography_warns_about_the_size_of_all_channel_epochs(
        self, documents: dict[str, str]
    ) -> None:
        flat = _flat(documents[ERP_TOPOGRAPHY])
        assert "epochs x channels x samples x 8 bytes" in flat
        assert "keep only a running sum per condition" in flat
        assert "one symmetric range for every map" in flat
        assert "the first minus the second" in flat

    def test_the_time_frequency_document_decides_there_is_no_filter_step(
        self, documents: dict[str, str]
    ) -> None:
        document = documents[TIME_FREQUENCY]
        flat = _flat(document)
        assert "Filtering is NOT a step here, decided once" in flat
        assert "the data are used as recorded" in flat
        plan = _flat(document.split("## Plan", 1)[1].split("\n## ", 1)[0])
        assert "re-reference" not in plan
        assert "PO8 for ERP CORE's N170" in flat
        assert "epochs x frequencies x times" in flat

    def test_the_time_frequency_plan_warns_before_approval_that_the_run_is_slow(
        self, documents: dict[str, str]
    ) -> None:
        document = documents[TIME_FREQUENCY]
        plan = _flat(document.split("## Plan", 1)[1].split("\n## ", 1)[0])
        for phrase in (
            "single processor",
            "limited compute and memory",
            "likely take a long time",
            "in the plan's findings",
            "again in step 3",
        ):
            assert phrase in plan, phrase
        # The warning comes before the numbered steps, so it is said before approval.
        assert plan.index("likely take a long time") < plan.index("1. **Read the recording**")

    def test_the_time_frequency_epochs_hold_the_lowest_frequencys_wavelet(
        self, documents: dict[str, str]
    ) -> None:
        document = documents[TIME_FREQUENCY]
        epoching = _fence_with(document, "pad_s")
        compute = _fence_with(document, "fftconvolve")
        # The epoch is the window plus the pad; the compute block cuts the pad off afterwards.
        assert "pad_s = 3 * n_cycles / (2 * np.pi * fmin)" in epoching
        assert "pre = int(np.ceil((0.5 + pad_s) * rate))" in epoching
        assert "post = int(np.ceil((1.0 + pad_s) * rate))" in epoching
        assert "analysed = (times >= -0.5) & (times <= 1.0)" in compute
        assert "assert trials.shape[1] == pre + post" in compute
        assert "10 * np.log10(" in compute

    def test_the_when_lines_do_not_share_their_discriminating_phrases(
        self, nemar: CommunityConfig
    ) -> None:
        when = {p.id: p.when.lower() for p in nemar.procedures}
        assert set(when) >= set(DECLARED_SEQUENCES)
        owners = {
            "scalp": set(TOPOGRAPHIES),
            "topograph": set(TOPOGRAPHIES),
            "ersp": {TIME_FREQUENCY},
            "time-frequency": {TIME_FREQUENCY},
            "spectrogram": {TIME_FREQUENCY},
            "bar chart": {BAND_POWER},
            "waveform": {ERP},
            "erp image": {ERP_IMAGE},
        }
        for phrase, allowed in owners.items():
            assert {pid for pid, line in when.items() if phrase in line} <= allowed, phrase
        # Each of the seven says what it is for, and the two pairs that sound alike differ.
        assert "scalp" in when[BAND_TOPOGRAPHY] and "scalp" in when[ERP_TOPOGRAPHY]
        assert "erp" not in when[BAND_TOPOGRAPHY]
        assert "power" not in when[ERP_TOPOGRAPHY]
        assert "time window" in when[ERP_TOPOGRAPHY]
        assert "ersp" in when[TIME_FREQUENCY]
        for procedure_id in (BAND_POWER, ERP):
            assert "scalp" not in when[procedure_id]
            assert "topograph" not in when[procedure_id]
        assert "bar chart or a table" in when[BAND_POWER]
