"""Regenerate a community's Pyodide lock overlay from the wheels committed beside it.

    uv run python scripts/build_runtime_lock.py src/assistants/nemar/runtime/nemar-pyodide-lock.json
    uv run python scripts/build_runtime_lock.py --check src/assistants/nemar/runtime/nemar-pyodide-lock.json

Every wheel in ``wheels/`` beside the overlay becomes one entry: its name, version and
importable top-level names are read from the wheel itself, its sha256 is computed, and
its ``depends`` come from ``depends.toml`` beside the overlay, because a wheel's own
metadata names PyPI requirements and not the Pyodide lock entries that satisfy them.

The overlay is reviewed in a pull request like any lockfile. ``--check`` exits non-zero
when the committed overlay is not what the wheels and ``depends.toml`` produce, and
``tests/test_assistants/test_shipped_runtimes.py`` runs it for every community that
names an overlay.

A wheel whose name is already in the overlay with a different sha256 is refused, not
rewritten: wheels are served as immutable for a year, so new bytes need a new version.
See ``src/core/config/runtime_lock.py`` for how the overlay is loaded and served.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import sys
import tomllib
import zipfile
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.config.runtime_lock import (  # noqa: E402
    WHEELS_DIR_NAME,
    RuntimeLockOverlay,
    canonical_name,
)

DEPENDS_FILE_NAME = "depends.toml"


class LockBuildError(ValueError):
    """The wheels, depends.toml and the committed overlay cannot be reconciled."""


def _metadata(wheel: zipfile.ZipFile) -> tuple[str, str]:
    """The distribution's name and version, from its own METADATA."""
    names = [n for n in wheel.namelist() if n.count("/") == 1 and n.endswith(".dist-info/METADATA")]
    if len(names) != 1:
        raise LockBuildError(
            f"{wheel.filename}: expected one .dist-info/METADATA, found {len(names)}"
        )
    try:
        text = wheel.read(names[0]).decode("utf-8")
    except UnicodeDecodeError as err:
        raise LockBuildError(f"{wheel.filename}: METADATA is not UTF-8: {err}") from err
    message = email.parser.Parser().parsestr(text)
    name, version = message["Name"], message["Version"]
    if not name or not version:
        raise LockBuildError(f"{wheel.filename}: METADATA has no Name or no Version")
    return name, version


def _imports(wheel: zipfile.ZipFile) -> list[str]:
    """Top-level importable names: packages and single-file modules at the root."""
    found: set[str] = set()
    for name in wheel.namelist():
        head, _, rest = name.partition("/")
        if head.endswith((".dist-info", ".data")):
            continue
        if rest:
            found.add(head)
        elif head.endswith(".py"):
            found.add(head.removesuffix(".py"))
    return sorted(found)


def build_overlay(overlay_path: Path) -> dict:
    """The overlay the committed wheels and depends.toml produce, as JSON-ready data."""
    runtime_dir = overlay_path.parent
    depends_path = runtime_dir / DEPENDS_FILE_NAME
    try:
        depends = tomllib.loads(depends_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise LockBuildError(f"{depends_path} cannot be read: {err}") from err
    for key, names in depends.items():
        # A bare string would sort into its characters and be written as depends.
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise LockBuildError(f"{DEPENDS_FILE_NAME}: {key} must be a list of package names")

    # What each committed wheel name was recorded as, so new bytes under an old name
    # are caught. Read through the model, so a hand-mangled overlay is a clear error.
    committed_sha256: dict[str, str] = {}
    if overlay_path.exists():
        try:
            committed = RuntimeLockOverlay.model_validate_json(overlay_path.read_text())
        except (OSError, ValidationError) as err:
            raise LockBuildError(
                f"the committed overlay {overlay_path} does not load, so the wheels cannot be "
                f"checked against it: {err}"
            ) from err
        committed_sha256 = {e.file_name: e.sha256 for e in committed.packages.values()}

    packages: dict[str, dict] = {}
    for wheel_path in sorted((runtime_dir / WHEELS_DIR_NAME).glob("*.whl")):
        try:
            with zipfile.ZipFile(wheel_path) as wheel:
                name, version = _metadata(wheel)
                imports = _imports(wheel)
        except (OSError, zipfile.BadZipFile) as err:
            raise LockBuildError(f"{wheel_path.name} is not a readable wheel: {err}") from err
        key = canonical_name(name)
        if key in packages:
            raise LockBuildError(f"two wheels provide {key}")
        if key not in depends:
            raise LockBuildError(f"{wheel_path.name}: {DEPENDS_FILE_NAME} has no line for {key}")
        sha256 = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        earlier = committed_sha256.get(wheel_path.name)
        if earlier is not None and earlier != sha256:
            raise LockBuildError(
                f"{wheel_path.name} has new bytes under a name already in the overlay. Served "
                "wheels are cached by name for a year, so build it as a new version instead."
            )
        packages[key] = {
            "name": name,
            "version": version,
            "file_name": wheel_path.name,
            "package_type": "package",
            "install_dir": "site",
            "sha256": sha256,
            "imports": imports,
            "depends": sorted(depends[key]),
        }

    unused = sorted(set(depends) - set(packages))
    if unused:
        raise LockBuildError(
            f"{DEPENDS_FILE_NAME} names packages with no wheel: {', '.join(unused)}"
        )
    overlay = {"packages": dict(sorted(packages.items()))}
    # The shape the server will load, checked here so a bad overlay is never written.
    try:
        RuntimeLockOverlay.model_validate(overlay)
    except ValidationError as err:
        raise LockBuildError(f"the overlay would not load: {err}") from err
    return overlay


def render(overlay: dict) -> str:
    return json.dumps(overlay, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate a community's Pyodide lock overlay from its committed wheels."
    )
    parser.add_argument(
        "overlay", type=Path, help="the overlay JSON, beside wheels/ and depends.toml"
    )
    parser.add_argument(
        "--check", action="store_true", help="fail if the committed overlay is stale"
    )
    args = parser.parse_args(argv)

    try:
        text = render(build_overlay(args.overlay))
    except LockBuildError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if args.check:
        current = args.overlay.read_text() if args.overlay.exists() else ""
        if current != text:
            print(
                f"{args.overlay} is not what its wheels and {DEPENDS_FILE_NAME} produce; regenerate it",
                file=sys.stderr,
            )
            return 1
        print(f"{args.overlay} is current")
        return 0
    args.overlay.write_text(text)
    print(f"wrote {args.overlay}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
