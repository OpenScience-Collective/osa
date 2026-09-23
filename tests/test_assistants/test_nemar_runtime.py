"""NEMAR runs code in the reader's browser, with the runtime its shipped files describe.

Reads the real `src/assistants/nemar/` folder: the config the app loads, the lock
overlay beside it, and the wheels it pins. What the runtime does with them in a real
Pyodide is `frontend/test-data-lane.js`; this is the server's side of the same files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
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

    def test_the_tool_points_the_model_at_the_browser_recipe(self, nemar: CommunityConfig) -> None:
        """The model sees this description verbatim, and the recipe it names is the
        one that works here; the desktop one starts a thread a browser cannot."""
        assert nemar.extensions is not None
        description = nemar.extensions.client_tools[0].description
        assert "python_browser" in description
        assert "never use" in description and "python_zarr" in description
        assert "display(eegprep_lean.plot_window(window).figure)" in description

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
        wheel = runtime_copy.parent / "wheels" / "eegprep_lean-0.1.0.dev1-py3-none-any.whl"
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
