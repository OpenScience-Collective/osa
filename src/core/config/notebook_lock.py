"""A community's browser-run notebook starter, and the notebook site's merged lock.

Three independent pieces live here, all about the separate site at
notebook.osc.earth/osa (issue #453, docs/adr/0011-the-notebook-site.md; ADR
0010 deferred building it):

- ``NOTEBOOK_SITE_PYODIDE_VERSION``, ``NOTEBOOK_TOKEN`` and
  ``validate_notebook_starter``: the config-time shape and content checks a
  community's ``notebook:`` block needs, in the same style
  ``src.core.config.runtime_lock`` validates a Pyodide lock overlay: shape (a
  relative path, the right suffix) is checked in a pydantic field validator with
  no disk access, and content (does the file exist, is it a real notebook, does
  it carry the token) is checked here, against a community folder, so it can run
  wherever a config is loaded from a real checkout: ``scripts/build-notebook-
  site.py`` and the tests under ``tests/test_core/test_config/``.
- ``ZARR_BASE_TOKEN``, ``DATASET_PAGE_BASE_TOKEN``, ``NOTEBOOK_ENVIRONMENTS``,
  ``environment_base_url_problem`` and ``fill_build_time_tokens``: a second,
  BUILD-time substitution, for a data host that differs per deployment
  (staging's dataset pages read a different Zarr host than production's, and
  each notebook deployment is already paired with one environment). Unlike
  ``NOTEBOOK_TOKEN``, these two tokens are filled once, by the build itself,
  before the starter ever reaches a reader's browser.
- ``merge_site_lock``: the site build's own step, merging every notebook-enabled
  community's lock overlay into ONE Pyodide lock for the whole site. This is the
  same rule ``frontend/osa-worker-core.js``'s ``mergeLock`` enforces for a single
  community's own runtime (an overlay entry may only ADD a package, never replace
  the distribution's own), extended across communities: two communities naming
  the same package must agree on it byte-for-byte, or the build fails rather than
  silently keeping one and dropping the other.

``NOTEBOOK_TOKEN`` substitution itself (writing ``{{dataset_id}}`` into a cell)
is NOT here: it happens client-side, in the reader's own browser, in
``notebook/open.js`` -- this module never touches a dataset id, only the
community-authored template and, separately, the per-environment host it reads
from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.core.config.runtime_lock import (
    RuntimeLockOverlay,
    RuntimeLockPackage,
    relative_community_path_problem,
)

#: The exact Pyodide the notebook site loads (docs/adr/0011-the-notebook-site.md).
#: One site loads one Pyodide, so every community that ships a starter must be
#: pinned to this version in its own ``runtime.python.pyodide_version`` -- checked
#: at config load (``CommunityConfig.validate_notebook_needs_matching_pyodide``)
#: and again, defensively, by the build script itself.
NOTEBOOK_SITE_PYODIDE_VERSION = "0.29.5"

#: The placeholder a starter's cells carry, filled in by ``notebook/open.js`` in the
#: reader's own browser. Braces, not a bare ``{dataset}``, because a bare brace is
#: common in the Python a starter cell contains (an f-string, a dict literal) and
#: would be a false match; ``{{dataset_id}}`` is not.
NOTEBOOK_TOKEN = "{{dataset_id}}"

#: Filled in at build time for one environment (``--environment``), never by
#: ``notebook/open.js``: staging's dataset pages read a different Zarr host than
#: production's, and each notebook deployment is paired with one of them.
ZARR_BASE_TOKEN = "{{zarr_base}}"

#: The same, for the starter's link back to the dataset's page on the website.
DATASET_PAGE_BASE_TOKEN = "{{dataset_page_base}}"

#: The only keys ``NotebookConfig.zarr_base`` and ``.dataset_page_base`` accept, and
#: the only values ``--environment`` accepts; a typo is refused at config load.
NOTEBOOK_ENVIRONMENTS = ("production", "develop")


def environment_base_url_problem(url: str) -> str | None:
    """Why ``url`` cannot be a ``zarr_base`` or ``dataset_page_base`` value, or None.

    The starter template writes ``{{zarr_base}}/{{dataset_id}}/...``, so a value must
    be exactly an https scheme and a host: a path, even a bare ``/``, would double
    or misplace the slash, and a query or fragment would end up mid-URL.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return f"must be an absolute https:// URL, got {url!r}"
    if not parsed.netloc:
        return f"must name a host, got {url!r}"
    if parsed.path:
        return f"must have no path, not even a trailing slash, got {url!r}"
    if parsed.query or parsed.fragment:
        return f"must have no query or fragment, got {url!r}"
    return None


def fill_build_time_tokens(
    notebook: dict[str, Any], *, zarr_base: str, dataset_page_base: str
) -> dict[str, Any]:
    """A copy of ``notebook`` with ``ZARR_BASE_TOKEN`` and ``DATASET_PAGE_BASE_TOKEN``
    filled in every cell, for one environment's build.

    ``NOTEBOOK_TOKEN`` is left alone: ``notebook/open.js`` fills it per reader.
    """

    def fill(text: str) -> str:
        return text.replace(ZARR_BASE_TOKEN, zarr_base).replace(
            DATASET_PAGE_BASE_TOKEN, dataset_page_base
        )

    filled = json.loads(json.dumps(notebook))
    for cell in filled.get("cells") or []:
        source = cell.get("source")
        if isinstance(source, list):
            cell["source"] = [fill(line) for line in source]
        elif isinstance(source, str):
            cell["source"] = fill(source)
    return filled


def starter_path_problem(path: str) -> str | None:
    """Why ``path`` cannot name a starter notebook inside a community folder, or None."""
    return relative_community_path_problem(path, ".ipynb")


class NotebookConfigError(ValueError):
    """A community's ``notebook.starter`` cannot be used as committed."""


def validate_notebook_starter(
    community_dir: Path, starter: str, token: str = NOTEBOOK_TOKEN
) -> dict[str, Any]:
    """Check a starter notebook's existence, structure and token, and return it parsed.

    Deliberately does not import ``nbformat``: it is a dev-only dependency (see
    ``pyproject.toml``'s ``[dependency-groups]``), so a production install -- the
    build script's own runtime, and anything importing ``src.core.config`` without
    the ``server`` extra -- must not need it. What is checked here is the minimum a
    real reader's browser needs: valid JSON, nbformat 4, a list of cells each with a
    recognized ``cell_type`` and a ``source`` the token appears in somewhere. A test
    that also wants nbformat's own, fuller validation runs it directly (dev-only, so
    only tests may depend on it).

    Returns:
        The parsed notebook, so a caller (the build script) that also needs to copy
        or re-serialize it does not read and parse the file a second time.

    Raises:
        NotebookConfigError: The file is missing, unreadable, not valid JSON, not a
            recognizable nbformat 4 notebook, or the token appears in no cell.
    """
    path = community_dir / starter
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise NotebookConfigError(f"starter notebook {path} cannot be read: {err}") from err

    try:
        notebook = json.loads(text)
    except json.JSONDecodeError as err:
        raise NotebookConfigError(f"starter notebook {path} is not valid JSON: {err}") from err

    if not isinstance(notebook, dict):
        raise NotebookConfigError(f"starter notebook {path} is not a JSON object")

    nbformat_major = notebook.get("nbformat")
    if nbformat_major != 4:
        raise NotebookConfigError(f"starter notebook {path} has nbformat={nbformat_major!r}, not 4")

    cells = notebook.get("cells")
    if not isinstance(cells, list) or not cells:
        raise NotebookConfigError(f"starter notebook {path} has no cells")

    found_token = False
    for i, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise NotebookConfigError(f"starter notebook {path}: cell {i} is not an object")
        cell_type = cell.get("cell_type")
        if cell_type not in ("markdown", "code"):
            raise NotebookConfigError(
                f"starter notebook {path}: cell {i} has cell_type={cell_type!r}, "
                "not 'markdown' or 'code'"
            )
        source = cell.get("source")
        if isinstance(source, list):
            if not all(isinstance(line, str) for line in source):
                raise NotebookConfigError(
                    f"starter notebook {path}: cell {i}'s source has a non-string line"
                )
            text_source = "".join(source)
        elif isinstance(source, str):
            text_source = source
        else:
            raise NotebookConfigError(
                f"starter notebook {path}: cell {i} has no string or list-of-string source"
            )
        if token in text_source:
            found_token = True

    if not found_token:
        raise NotebookConfigError(
            f"starter notebook {path} never uses the token {token!r}; nothing would ever "
            "be filled in for a reader"
        )

    return notebook


class NotebookLockError(ValueError):
    """The notebook site's merged Pyodide lock cannot be built as configured."""


def _identity(
    entry: RuntimeLockPackage,
) -> tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]]:
    """What makes two communities' entries for the same package the same package.

    ``file_name`` is included: two communities that both depend on, say, zarr 3.4.0
    have to ship the identical wheel for this to be a safe no-op merge -- one
    community's build with a stray local patch would have a different sha256 and be
    caught below, not silently preferred.
    """
    return (
        entry.name,
        entry.version,
        entry.file_name,
        entry.sha256,
        tuple(entry.imports),
        tuple(entry.depends),
    )


def merge_site_lock(
    stock: dict[str, Any], overlays: dict[str, RuntimeLockOverlay], site_url: str
) -> dict[str, Any]:
    """Pyodide's own lock, with every notebook-enabled community's overlay merged in.

    Mirrors ``frontend/osa-worker-core.js``'s ``mergeLock``: an overlay entry may only
    ADD a package, never replace one the Pyodide distribution itself ships. Extended
    here across communities, since this lock serves every community's starter from
    one site rather than one community's own runtime: two communities that both name
    the same package must have identical entries (see ``_identity``), or the build
    fails rather than silently keeping the first one seen and dropping the other.

    Every added entry's ``file_name`` is rewritten to an absolute
    ``<site_url>/wheels/<community_id>/<file_name>`` URL. Pyodide resolves a lock
    entry's ``file_name`` against ``packageBaseUrl`` when it is relative, and this
    site's ``packageBaseUrl`` is jsDelivr's own Pyodide distribution (see
    ``docs/adr/0011-the-notebook-site.md``), not this site -- a relative
    ``file_name`` here would fetch a community's wheel from the wrong host entirely.

    Args:
        stock: Pyodide's own ``pyodide-lock.json``, parsed.
        overlays: Each notebook-enabled community's own, already-verified lock
            overlay (see ``src.core.config.runtime_lock.load_runtime_lock``), keyed
            by community id.
        site_url: The notebook site's own absolute base URL, no trailing slash
            required (trimmed if given one).

    Raises:
        NotebookLockError: ``site_url`` is not an absolute http(s) URL, ``stock``
            has no ``packages``, an overlay would replace a distribution package, or
            two communities disagree about a shared package.
    """
    if not re.match(r"^https?://", site_url):
        raise NotebookLockError(f"site_url must be an absolute http(s) URL: {site_url!r}")
    site_url = site_url.rstrip("/")

    stock_packages = stock.get("packages")
    if not isinstance(stock_packages, dict):
        raise NotebookLockError("the Pyodide distribution's lock has no packages")

    merged: dict[str, Any] = dict(stock_packages)
    # canonical key -> (community that first added it, its original entry), so a
    # later community naming the same key can be compared against the one already
    # merged rather than only against the newcomer.
    origin: dict[str, tuple[str, RuntimeLockPackage]] = {}

    for community_id in sorted(overlays):
        overlay = overlays[community_id]
        for key, entry in overlay.packages.items():
            if key in stock_packages:
                raise NotebookLockError(
                    f"{community_id}: lock entry {key!r} would replace the Pyodide "
                    "distribution's own"
                )
            if key in origin:
                other_community, other_entry = origin[key]
                if _identity(other_entry) != _identity(entry):
                    raise NotebookLockError(
                        f"{community_id} and {other_community} both add {key!r} with "
                        "different entries; a package two communities share must be "
                        "byte-identical across both"
                    )
                continue  # identical; already merged under other_community's URL
            origin[key] = (community_id, entry)
            merged[key] = {
                "name": entry.name,
                "version": entry.version,
                "file_name": f"{site_url}/wheels/{community_id}/{entry.file_name}",
                "package_type": entry.package_type,
                "install_dir": entry.install_dir,
                "sha256": entry.sha256,
                "imports": list(entry.imports),
                "depends": list(entry.depends),
            }

    return {"info": stock.get("info"), "packages": merged}
