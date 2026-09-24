#!/usr/bin/env python3
"""Build the notebook.osc.earth/osa static site (issue #453, docs/adr/0011-the-notebook-site.md).

    uv run python scripts/build_notebook_site.py \\
        --site-url https://notebook.osc.earth/osa --output-dir dist/notebook-site

Needs no secrets, and can run in CI: it fetches Pyodide's own build tooling from
PyPI and its lock from jsDelivr (both public, and the lock is pinned and verified
by sha256 -- see PYODIDE_LOCK_SHA256 below), then assembles, for every community
that names a ``notebook:`` block in its ``config.yaml``:

1. A JupyterLite site (``jupyterlite-core`` 0.8.4, ``jupyterlite-pyodide-kernel``
   0.8.0), patched to load Pyodide 0.29.5 from jsDelivr rather than the kernel's own
   default pin, WITHOUT self-hosting it (``--pyodide=<tarball>`` would copy the full
   ~464 MB distribution into the site; ``pyodideUrl`` keeps it a CDN reference).
2. ``lock/pyodide-lock.json``: Pyodide's own lock, pinned and sha256-verified, with
   every notebook-enabled community's own lock overlay merged in (never replacing a
   distribution package; see ``src.core.config.notebook_lock.merge_site_lock``).
3. ``wheels/<community>/<file>``: each overlay wheel, copied from the community's own
   ``runtime/wheels/`` folder, its sha256 already verified by ``load_runtime_lock``.
4. ``starters/<community>.ipynb`` and ``starters/index.json``: each community's
   starter notebook, validated (``validate_notebook_starter``), and the dataset id
   pattern ``notebook/open.js`` gates a request against.
5. The same-origin bootstrap (``open.html``, ``open.js``, vendored ``localforage``),
   copied verbatim from ``notebook/`` at the repository root.

Everything above (1-5) is written under ``--output-dir/<subdir>``, where
``<subdir>`` is ``--site-url``'s own path component (``osa`` for
``https://notebook.osc.earth/osa``): OSC's naming rule is that a subdomain is a
PLANE serving several projects, and the project itself is the PATH
(``api.osc.earth/osa``, ``widget.osc.earth/osa``, ...), and this site follows the
same rule rather than owning its subdomain's root. ``_headers`` is generated
(not copied verbatim) with every path pattern prefixed by ``/<subdir>``, and
written at ``--output-dir`` itself, because Cloudflare Pages only reads
``_headers``/``_redirects`` from the exact root of the published directory --
never from a subdirectory. When ``<subdir>`` is non-empty, a ``_redirects`` file
sending ``/`` to ``/<subdir>/`` is also written at ``--output-dir``. A bare-host
``--site-url`` (no path) is still accepted, and then everything sits at
``--output-dir`` directly with no ``_redirects``, matching how a plain local
test build with no path prefix behaves.

``--expose-app`` sets JupyterLite's ``exposeAppInBrowser``, so a test can drive the
built site through ``window.jupyterapp``; a production build omits it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.config.community import CommunityConfig  # noqa: E402
from src.core.config.notebook_lock import (  # noqa: E402
    NOTEBOOK_SITE_PYODIDE_VERSION,
    merge_site_lock,
    validate_notebook_starter,
)
from src.core.config.runtime_lock import load_runtime_lock, runtime_wheel  # noqa: E402

ASSISTANTS_DIR = ROOT / "src" / "assistants"
NOTEBOOK_SOURCE_DIR = ROOT / "notebook"

JUPYTERLITE_CORE_VERSION = "0.8.4"
JUPYTERLITE_PYODIDE_KERNEL_VERSION = "0.8.0"
PYODIDE_VERSION = NOTEBOOK_SITE_PYODIDE_VERSION

#: Pyodide 0.29.5's own lock, as fetched from jsDelivr on 2026-09-23 (see
#: .context/notebook-surface-measurements.md). Pinned so a change upstream -- a
#: yanked release, a re-published lock -- fails the build loudly instead of quietly
#: shipping a different set of stock packages than every other check ran against.
PYODIDE_LOCK_SHA256 = "14d2c2dba101277999e17135e653d8f15389ad1437f53eae213bf0c3cdff723d"

PYODIDE_CDN_BASE = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
KERNEL_PLUGIN_ID = "@jupyterlite/pyodide-kernel-extension:kernel"

#: The bootstrap and vendored files copied verbatim from notebook/ into the site's
#: own subdirectory. ``_headers`` is NOT here: it is generated (path-prefixed) and
#: written at the true publish root instead -- see ``write_headers``.
BOOTSTRAP_FILES = ("open.html", "open.js", "localforage.min.js")

#: The template ``_headers`` is generated from; see ``write_headers``.
HEADERS_TEMPLATE = NOTEBOOK_SOURCE_DIR / "_headers"


def site_subdir(site_url: str) -> str:
    """The path component of ``site_url`` as a plain subdirectory name.

    ``osa`` for ``https://notebook.osc.earth/osa``; ``""`` for a bare host with
    no path, in which case the site is published at ``--output-dir`` directly.
    """
    return urlparse(site_url).path.strip("/")


class NotebookSiteBuildError(RuntimeError):
    """The notebook site cannot be built as configured or as the tools produced it."""


def discover_notebook_communities() -> dict[str, CommunityConfig]:
    """Every community whose config.yaml declares a ``notebook:`` block, by id."""
    found: dict[str, CommunityConfig] = {}
    for path in sorted(ASSISTANTS_DIR.glob("*/config.yaml")):
        config = CommunityConfig.from_yaml(path)
        if config.notebook is not None:
            found[config.id] = config
    return found


def fetch_pyodide_lock(version: str, expected_sha256: str, url: str | None = None) -> dict:
    """Pyodide's own lock, sha256-verified against the pin above.

    ``url`` defaults to jsDelivr; a test overrides it with a real local HTTP
    server to exercise the mismatch path against genuine bytes over a genuine
    socket, rather than a stubbed return value.
    """
    if url is None:
        url = f"https://cdn.jsdelivr.net/pyodide/v{version}/full/pyodide-lock.json"
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        raise NotebookSiteBuildError(
            f"{url} has sha256 {digest}, not the pinned {expected_sha256}. Pyodide "
            f"{version}'s own lock changed upstream; re-verify and update the pin "
            "deliberately rather than building against an unreviewed lock."
        )
    return json.loads(data)


def run_jupyterlite_build(output_dir: Path) -> None:
    """Shell out to the exact pinned JupyterLite build the maintainer's spike used."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="osa-notebook-lite-dir-") as empty_lite_dir:
        command = [
            "uv",
            "tool",
            "run",
            "--from",
            f"jupyterlite-core=={JUPYTERLITE_CORE_VERSION}",
            "--with",
            f"jupyterlite-pyodide-kernel=={JUPYTERLITE_PYODIDE_KERNEL_VERSION}",
            "--with",
            "jupyter-server",
            "jupyter",
            "lite",
            "build",
            "--output-dir",
            str(output_dir),
            # Explicit, empty: nothing here should merge stray jupyter-lite.json
            # config from wherever this script happens to be run from.
            "--lite-dir",
            empty_lite_dir,
            "--apps",
            "notebooks",
            "--apps",
            "lab",
        ]
        result = subprocess.run(command, cwd=ROOT, check=False)  # noqa: S603
        if result.returncode != 0:
            raise NotebookSiteBuildError(f"jupyter lite build exited {result.returncode}")


def patch_root_config(output_dir: Path, expose_app: bool) -> None:
    """Pin the kernel's Pyodide to jsDelivr's own 0.29.5, without self-hosting it.

    JupyterLite's own build-time config merge (``--lite-dir``) REPLACES
    ``litePluginSettings`` wholesale rather than merging it (verified by reading
    ``jupyterlite_core.addons.base.BaseAddon.merge_jupyter_config_data``: only
    ``disabledExtensions``/``federated_extensions``/``settingsOverrides`` are
    merged; every other key, ``litePluginSettings`` included, is a plain
    overwrite), so a ``--lite-dir`` seed file would silently drop the build's own
    ``pipliteUrls`` entry. Post-processing the built file instead, as here, keeps
    it: this function reads it back and requires it to still be there.

    Patching only the root ``jupyter-lite.json`` is enough for the ``notebooks``
    app too: ``config-utils.js`` (bundled into every app page) fetches every
    ancestor directory's ``jupyter-lite.json`` up to the site root at runtime and
    merges each plugin's settings shallowly, so the notebooks app inherits this
    file's values without its own copy needing the same patch.
    """
    config_path = output_dir / "jupyter-lite.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as err:
        raise NotebookSiteBuildError(f"{config_path} cannot be read: {err}") from err

    jcd = data.get("jupyter-config-data")
    if not isinstance(jcd, dict):
        raise NotebookSiteBuildError(f"{config_path} has no jupyter-config-data")

    settings = jcd.setdefault("litePluginSettings", {})
    kernel_settings = settings.setdefault(KERNEL_PLUGIN_ID, {})
    if "pipliteUrls" not in kernel_settings:
        raise NotebookSiteBuildError(
            f"{config_path}: {KERNEL_PLUGIN_ID} lost its pipliteUrls entry; the "
            "installed jupyterlite-pyodide-kernel version may have changed how it "
            "writes this file"
        )

    kernel_settings["pyodideUrl"] = f"{PYODIDE_CDN_BASE}pyodide.mjs"
    kernel_settings["loadPyodideOptions"] = {
        # Ends in "URL": the kernel's own code resolves this against the site's
        # base URL, so a root-relative "./..." is correct here.
        "lockFileURL": "./lock/pyodide-lock.json",
        # Does NOT end in "URL" (it ends in "baseUrl"), so the kernel does not
        # resolve it -- it must already be absolute, or every non-root-relative
        # package fetch (every stock Pyodide package this site does not vendor)
        # would resolve against this site instead of jsDelivr.
        "packageBaseUrl": PYODIDE_CDN_BASE,
    }
    if expose_app:
        jcd["exposeAppInBrowser"] = True

    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def build_merged_lock_and_wheels(
    output_dir: Path,
    communities: dict[str, CommunityConfig],
    stock_lock: dict,
    site_url: str,
) -> None:
    """Write lock/pyodide-lock.json and copy every overlay wheel beside it."""
    overlays = {}
    lockfiles: dict[str, str] = {}
    for community_id, config in communities.items():
        python = config.runtime.python if config.runtime else None
        lockfile = python.lockfile if python else None
        if lockfile is None:
            continue  # this community's starter needs nothing beyond stock Pyodide
        overlays[community_id] = load_runtime_lock(ASSISTANTS_DIR / community_id, lockfile)
        lockfiles[community_id] = lockfile

    merged = merge_site_lock(stock_lock, overlays, site_url)
    lock_dir = output_dir / "lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "pyodide-lock.json").write_text(json.dumps(merged, indent=2) + "\n")

    for community_id, overlay in overlays.items():
        lockfile = lockfiles[community_id]
        dest_dir = output_dir / "wheels" / community_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        for entry in overlay.packages.values():
            data = runtime_wheel(ASSISTANTS_DIR / community_id, lockfile, entry.file_name)
            if data is None:
                raise NotebookSiteBuildError(
                    f"{community_id}: overlay lists {entry.file_name} but it was not "
                    "found among its verified wheels"
                )
            (dest_dir / entry.file_name).write_bytes(data)


def write_starters(output_dir: Path, communities: dict[str, CommunityConfig]) -> None:
    """starters/<community>.ipynb (validated, byte-identical to the source) and index.json."""
    starters_dir = output_dir / "starters"
    starters_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict[str, str]] = {}
    for community_id, config in communities.items():
        assert config.notebook is not None  # discover_notebook_communities guarantees this
        notebook = validate_notebook_starter(ASSISTANTS_DIR / community_id, config.notebook.starter)
        (starters_dir / f"{community_id}.ipynb").write_text(
            json.dumps(notebook, indent=2) + "\n", encoding="utf-8"
        )
        index[community_id] = {"dataset_pattern": config.notebook.dataset_pattern}
    (starters_dir / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def copy_bootstrap_files(site_root: Path) -> None:
    for name in BOOTSTRAP_FILES:
        source = NOTEBOOK_SOURCE_DIR / name
        if not source.exists():
            raise NotebookSiteBuildError(f"missing bootstrap file: {source}")
        shutil.copy2(source, site_root / name)


def write_headers(publish_root: Path, subdir: str) -> None:
    """``_headers``, every path pattern prefixed by ``/<subdir>``, at the site's
    TRUE publish root (never a subdirectory: Cloudflare Pages only reads
    ``_headers``/``_redirects`` from exactly there)."""
    try:
        text = HEADERS_TEMPLATE.read_text(encoding="utf-8")
    except OSError as err:
        raise NotebookSiteBuildError(
            f"missing _headers template: {HEADERS_TEMPLATE}: {err}"
        ) from err
    if subdir:
        text = (
            "\n".join(
                f"/{subdir}{line}" if line.startswith("/") else line for line in text.splitlines()
            )
            + "\n"
        )
    (publish_root / "_headers").write_text(text, encoding="utf-8")


def write_root_redirect(publish_root: Path, subdir: str) -> None:
    """``/`` -> ``/<subdir>/``, Cloudflare Pages' own ``_redirects`` format.

    Only written when there IS a subdir: a bare-host build has nothing to
    redirect away from.
    """
    (publish_root / "_redirects").write_text(f"/  /{subdir}/  302\n", encoding="utf-8")


def strip_sourcemaps(output_dir: Path) -> int:
    """Remove every *.map file: a browser fetches one only when devtools asks for
    it, so their absence changes nothing a reader's session depends on -- checked
    directly by notebook/e2e-check.js, which runs the full notebook flow against a
    build this function has already stripped."""
    removed = 0
    for map_file in output_dir.rglob("*.map"):
        map_file.unlink()
        removed += 1
    return removed


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site-url",
        required=True,
        help="the site's own absolute base URL, e.g. https://notebook.osc.earth/osa",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--expose-app",
        action="store_true",
        help="set JupyterLite's exposeAppInBrowser (window.jupyterapp); test builds only",
    )
    return parser.parse_args(argv)


def finalize_publish_root(output_dir: Path, subdir: str) -> None:
    """Write ``_headers`` (and ``_redirects``, when ``subdir`` is non-empty) at the
    true Cloudflare Pages publish root, ``output_dir`` -- NEVER at ``output_dir /
    subdir`` (the site itself): Cloudflare Pages reads ``_headers``/``_redirects``
    only from the exact root of the published directory, never from a
    subdirectory (see this module's own docstring).

    Pulled out of ``build()`` as its own step, callable on its own against a
    synthetic output tree, so a regression in exactly this wiring -- passing the
    site subdirectory instead of the publish root to either write -- is caught
    by a fast offline test (``tests/test_scripts/test_build_notebook_site.py::
    TestFinalizePublishRoot``) rather than only by the network-marked full build.
    """
    write_headers(output_dir, subdir)
    if subdir:
        write_root_redirect(output_dir, subdir)


def build(site_url: str, output_dir: Path, expose_app: bool = False) -> None:
    """The whole build, callable directly (tests use this; main() is the CLI wrapper)."""
    site_url = site_url.rstrip("/")
    if not site_url:
        raise NotebookSiteBuildError("--site-url must not be empty")

    subdir = site_subdir(site_url)
    site_root = output_dir / subdir if subdir else output_dir

    communities = discover_notebook_communities()
    if not communities:
        raise NotebookSiteBuildError("no community declares a notebook: block; nothing to build")

    for community_id, config in communities.items():
        python = config.runtime.python if config.runtime else None
        # Already enforced at config load (CommunityConfig.validate_notebook_needs_
        # matching_pyodide); re-checked here so a build can never silently proceed
        # even if that model validator were ever loosened.
        if python is None or python.pyodide_version != PYODIDE_VERSION:
            raise NotebookSiteBuildError(
                f"{community_id}: notebook is configured but runtime.python."
                f"pyodide_version is not {PYODIDE_VERSION!r}"
            )
        assert config.notebook is not None
        validate_notebook_starter(ASSISTANTS_DIR / community_id, config.notebook.starter)

    print(
        f"building JupyterLite {JUPYTERLITE_CORE_VERSION} "
        f"(kernel {JUPYTERLITE_PYODIDE_KERNEL_VERSION}) for {sorted(communities)} "
        f"into {site_root} (site url {site_url})"
    )
    run_jupyterlite_build(site_root)
    patch_root_config(site_root, expose_app)

    print(f"fetching Pyodide {PYODIDE_VERSION}'s own lock")
    stock_lock = fetch_pyodide_lock(PYODIDE_VERSION, PYODIDE_LOCK_SHA256)
    build_merged_lock_and_wheels(site_root, communities, stock_lock, site_url)

    write_starters(site_root, communities)
    copy_bootstrap_files(site_root)

    finalize_publish_root(output_dir, subdir)

    removed = strip_sourcemaps(site_root)
    print(f"stripped {removed} sourcemap file(s)")
    print(f"done: {output_dir}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        build(args.site_url, args.output_dir, expose_app=args.expose_app)
    except NotebookSiteBuildError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
