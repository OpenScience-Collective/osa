"""scripts/build_notebook_site.py, against real files on disk.

Fast, offline pieces (config discovery, the jupyter-lite.json patch, the wheel
copy, starters, bootstrap files, sourcemap stripping) are tested here directly,
each against real files under tmp_path, no mocks. The one piece that genuinely
needs the network -- the real `jupyter lite build` and the real Pyodide lock
fetch -- is a single end-to-end test, marked ``network`` the way this repo marks
its other live tests (see .rules/testing.md and test.yml's separate "Run network
tests" step): CI's ordinary Test job excludes it (``-m "not network"``), and it
runs on push to main/workflow_dispatch instead.

fetch_pyodide_lock's sha256-mismatch path is tested against a REAL local HTTP
server (a real socket, real bytes) rather than a stubbed return value, matching
how this repo already treats an HTTP boundary as an acceptable place to control
inputs (.rules/testing.md: "HTTP response fixtures ... are acceptable for testing
error paths").
"""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

from scripts import build_notebook_site as site


def _entry(name: str, file_name: str, data: bytes) -> dict:
    return {
        "name": name,
        "version": "1.0",
        "file_name": file_name,
        "package_type": "package",
        "install_dir": "site",
        "sha256": hashlib.sha256(data).hexdigest(),
        "imports": [name],
        "depends": [],
    }


def _write_community(
    assistants_dir: Path,
    community_id: str,
    *,
    with_notebook: bool = True,
    with_overlay: bool = True,
    starter_source: str = "# {{dataset_id}}",
    zarr_base: dict[str, str] | None = None,
    dataset_page_base: dict[str, str] | None = None,
) -> None:
    community_dir = assistants_dir / community_id
    community_dir.mkdir(parents=True)

    notebook_block = ""
    if with_notebook:
        notebook_dir = community_dir / "notebook"
        notebook_dir.mkdir()
        (notebook_dir / "starter.ipynb").write_text(
            json.dumps(
                {
                    "cells": [
                        {
                            "cell_type": "markdown",
                            "metadata": {},
                            "source": starter_source,
                        }
                    ],
                    "metadata": {},
                    "nbformat": 4,
                    "nbformat_minor": 5,
                }
            )
        )
        zarr_base = (
            zarr_base
            if zarr_base is not None
            else {
                "production": "https://zarr.example.org",
                "develop": "https://zarr-test.example.org",
            }
        )
        dataset_page_base = (
            dataset_page_base
            if dataset_page_base is not None
            else {"production": "https://example.org", "develop": "https://test.example.org"}
        )
        zarr_base_yaml = "".join(f"    {env}: {url}\n" for env, url in zarr_base.items())
        dataset_page_base_yaml = "".join(
            f"    {env}: {url}\n" for env, url in dataset_page_base.items()
        )
        notebook_block = (
            "notebook:\n"
            "  starter: notebook/starter.ipynb\n"
            '  dataset_pattern: "^nm[0-9]{6}$"\n'
            f"  zarr_base:\n{zarr_base_yaml}"
            f"  dataset_page_base:\n{dataset_page_base_yaml}"
        )

    runtime_block = f'    pyodide_version: "{site.PYODIDE_VERSION}"\n'
    if with_overlay:
        wheel_bytes = f"pretend wheel bytes for {community_id}".encode()
        wheel_name = f"{community_id}pkg-1.0-py3-none-any.whl"
        (community_dir / "runtime" / "wheels").mkdir(parents=True)
        (community_dir / "runtime" / "wheels" / wheel_name).write_bytes(wheel_bytes)
        overlay = {
            "packages": {
                f"{community_id}pkg": _entry(f"{community_id}pkg", wheel_name, wheel_bytes)
            }
        }
        (community_dir / "runtime" / "lock.json").write_text(json.dumps(overlay))
        runtime_block += "    lockfile: runtime/lock.json\n"

    (community_dir / "config.yaml").write_text(
        f"id: {community_id}\n"
        f"name: {community_id.title()}\n"
        f"description: A test community.\n"
        "runtime:\n"
        "  python:\n"
        f"{runtime_block}"
        f"{notebook_block}"
    )


@pytest.fixture
def assistants_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "assistants"
    directory.mkdir()
    monkeypatch.setattr(site, "ASSISTANTS_DIR", directory)
    return directory


class TestDiscoverNotebookCommunities:
    def test_finds_only_communities_with_a_notebook_block(self, assistants_dir: Path) -> None:
        _write_community(assistants_dir, "withnb", with_notebook=True)
        _write_community(assistants_dir, "without", with_notebook=False, with_overlay=False)

        found = site.discover_notebook_communities()

        assert set(found) == {"withnb"}


class TestPatchRootConfig:
    def _built_config(self, output_dir: Path, litePluginSettings: dict | None = None) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "jupyter-lite.json"
        jcd = {"appUrl": "./lab", "baseUrl": "./"}
        if litePluginSettings is not None:
            jcd["litePluginSettings"] = litePluginSettings
        path.write_text(json.dumps({"jupyter-config-data": jcd}))
        return path

    def test_pins_pyodide_url_and_load_options_while_keeping_pipliteurls(
        self, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "out"
        config_path = self._built_config(
            output_dir,
            {
                site.KERNEL_PLUGIN_ID: {
                    "pipliteUrls": [
                        "./extensions/@jupyterlite/pyodide-kernel-extension/static/pypi/all.json"
                    ]
                }
            },
        )

        site.patch_root_config(output_dir, expose_app=False)

        patched = json.loads(config_path.read_text())
        kernel = patched["jupyter-config-data"]["litePluginSettings"][site.KERNEL_PLUGIN_ID]
        assert kernel["pyodideUrl"] == f"{site.PYODIDE_CDN_BASE}pyodide.mjs"
        assert kernel["loadPyodideOptions"] == {
            "lockFileURL": "./lock/pyodide-lock.json",
            "packageBaseUrl": site.PYODIDE_CDN_BASE,
        }
        # The build's own entry survives the patch (the whole point of
        # post-processing rather than a --lite-dir merge, which would have
        # replaced litePluginSettings wholesale -- see patch_root_config's
        # docstring).
        assert kernel["pipliteUrls"] == [
            "./extensions/@jupyterlite/pyodide-kernel-extension/static/pypi/all.json"
        ]
        assert "exposeAppInBrowser" not in patched["jupyter-config-data"]

    def test_expose_app_sets_the_flag(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        config_path = self._built_config(
            output_dir, {site.KERNEL_PLUGIN_ID: {"pipliteUrls": ["x"]}}
        )

        site.patch_root_config(output_dir, expose_app=True)

        patched = json.loads(config_path.read_text())
        assert patched["jupyter-config-data"]["exposeAppInBrowser"] is True

    def test_missing_pipliteurls_is_refused(self, tmp_path: Path) -> None:
        """A JupyterLite version that stopped writing pipliteUrls must fail the
        build loudly, not silently ship a kernel that cannot install anything
        from PyPI at all."""
        output_dir = tmp_path / "out"
        self._built_config(output_dir, {site.KERNEL_PLUGIN_ID: {}})

        with pytest.raises(site.NotebookSiteBuildError, match="pipliteUrls"):
            site.patch_root_config(output_dir, expose_app=False)

    def test_missing_config_file_is_refused(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        with pytest.raises(site.NotebookSiteBuildError, match="cannot be read"):
            site.patch_root_config(output_dir, expose_app=False)


class TestBuildMergedLockAndWheels:
    def test_writes_the_merged_lock_and_copies_every_overlay_wheel(
        self, assistants_dir: Path, tmp_path: Path
    ) -> None:
        _write_community(assistants_dir, "nemarlike")
        communities = site.discover_notebook_communities()
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        stock = {"info": {"python": "3.13.2"}, "packages": {"numpy": {"name": "numpy"}}}

        site.build_merged_lock_and_wheels(
            output_dir, communities, stock, "https://notebook.osc.earth"
        )

        merged = json.loads((output_dir / "lock" / "pyodide-lock.json").read_text())
        assert "numpy" in merged["packages"]
        entry = merged["packages"]["nemarlikepkg"]
        assert entry["file_name"] == (
            "https://notebook.osc.earth/wheels/nemarlike/nemarlikepkg-1.0-py3-none-any.whl"
        )
        wheel_path = output_dir / "wheels" / "nemarlike" / "nemarlikepkg-1.0-py3-none-any.whl"
        assert wheel_path.read_bytes() == b"pretend wheel bytes for nemarlike"

    def test_a_community_with_no_lockfile_adds_no_wheels(
        self, assistants_dir: Path, tmp_path: Path
    ) -> None:
        _write_community(assistants_dir, "bare", with_overlay=False)
        communities = site.discover_notebook_communities()
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        stock = {"info": {}, "packages": {}}

        site.build_merged_lock_and_wheels(output_dir, communities, stock, "https://x.example")

        merged = json.loads((output_dir / "lock" / "pyodide-lock.json").read_text())
        assert merged["packages"] == {}
        assert not (output_dir / "wheels").exists()


class TestWriteStarters:
    def test_writes_a_validated_starter_and_an_index(
        self, assistants_dir: Path, tmp_path: Path
    ) -> None:
        _write_community(assistants_dir, "nemarlike", with_overlay=False)
        communities = site.discover_notebook_communities()
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        site.write_starters(output_dir, communities, "production")

        starter = json.loads((output_dir / "starters" / "nemarlike.ipynb").read_text())
        assert starter["nbformat"] == 4
        index = json.loads((output_dir / "starters" / "index.json").read_text())
        assert index == {"nemarlike": {"dataset_pattern": "^nm[0-9]{6}$"}}

    def test_fills_zarr_base_and_dataset_page_base_for_the_given_environment(
        self, assistants_dir: Path, tmp_path: Path
    ) -> None:
        """The served starter carries the right host per environment (PR review
        finding, staging's dataset pages read a different Zarr host than
        production's)."""
        _write_community(
            assistants_dir,
            "nemarlike",
            with_overlay=False,
            starter_source="[{{dataset_id}}]({{dataset_page_base}}/dataset/{{dataset_id}}) {{zarr_base}}",
        )
        communities = site.discover_notebook_communities()
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        site.write_starters(output_dir, communities, "develop")

        starter = json.loads((output_dir / "starters" / "nemarlike.ipynb").read_text())
        text = "".join(starter["cells"][0]["source"])
        assert "https://zarr-test.example.org" in text
        assert "https://test.example.org/dataset/{{dataset_id}}" in text
        # The client-side token is left alone for open.js to fill per reader.
        assert "{{zarr_base}}" not in text
        assert "{{dataset_page_base}}" not in text
        assert "{{dataset_id}}" in text

    def test_refuses_an_environment_the_community_never_declared(
        self, assistants_dir: Path, tmp_path: Path
    ) -> None:
        _write_community(
            assistants_dir,
            "nemarlike",
            with_overlay=False,
            zarr_base={"production": "https://zarr.example.org"},  # no "develop"
        )
        communities = site.discover_notebook_communities()
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        with pytest.raises(
            site.NotebookSiteBuildError, match="no zarr_base declared for environment 'develop'"
        ):
            site.write_starters(output_dir, communities, "develop")


class TestCopyBootstrapFiles:
    def test_copies_every_bootstrap_file(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        site.copy_bootstrap_files(output_dir)

        for name in site.BOOTSTRAP_FILES:
            assert (output_dir / name).exists(), name
        assert (output_dir / "open.js").read_bytes() == (
            site.NOTEBOOK_SOURCE_DIR / "open.js"
        ).read_bytes()
        assert "_headers" not in site.BOOTSTRAP_FILES  # generated separately; see write_headers


class TestSiteSubdir:
    """OSC's naming rule: a subdomain is a plane, the project is the path."""

    def test_a_path_becomes_a_plain_subdirectory_name(self) -> None:
        assert site.site_subdir("https://notebook.osc.earth/osa") == "osa"

    def test_a_trailing_slash_does_not_change_it(self) -> None:
        assert site.site_subdir("https://notebook.osc.earth/osa/") == "osa"

    def test_a_bare_host_has_no_subdir(self) -> None:
        assert site.site_subdir("https://notebook.osc.earth") == ""

    def test_a_bare_host_with_a_trailing_slash_has_no_subdir(self) -> None:
        assert site.site_subdir("https://notebook.osc.earth/") == ""


class TestWriteHeaders:
    def test_every_path_pattern_gets_the_subdir_prefix(self, tmp_path: Path) -> None:
        publish_root = tmp_path / "out"
        publish_root.mkdir()

        site.write_headers(publish_root, "osa")

        text = (publish_root / "_headers").read_text()
        assert "/osa/wheels/*" in text
        assert "/osa/open.html" in text
        assert "/osa/*" in text  # the site-wide CSP/Referrer-Policy block
        assert "\n/wheels/*" not in text  # the unprefixed form is gone

    def test_a_comment_line_is_left_alone(self, tmp_path: Path) -> None:
        publish_root = tmp_path / "out"
        publish_root.mkdir()

        site.write_headers(publish_root, "osa")

        text = (publish_root / "_headers").read_text()
        assert any(line.startswith("#") for line in text.splitlines())

    def test_no_subdir_leaves_patterns_unprefixed(self, tmp_path: Path) -> None:
        publish_root = tmp_path / "out"
        publish_root.mkdir()

        site.write_headers(publish_root, "")

        text = (publish_root / "_headers").read_text()
        assert "\n/wheels/*" in f"\n{text}"


class TestWriteRootRedirect:
    def test_redirects_root_to_the_subdir(self, tmp_path: Path) -> None:
        publish_root = tmp_path / "out"
        publish_root.mkdir()

        site.write_root_redirect(publish_root, "osa")

        assert (publish_root / "_redirects").read_text().strip() == "/  /osa/  302"


class TestFinalizePublishRoot:
    """The exact wiring a real build's own end-to-end test cannot gate a PR with
    (it is network-marked): _headers/_redirects must land at the true publish
    root, never inside the site subdirectory. Proven against a synthetic output
    tree -- a site subdirectory with a placeholder file, standing in for a real
    jupyterlite build's output -- so this runs with no network and no subprocess.
    """

    def test_headers_and_redirects_land_at_the_publish_root_not_the_site_subdir(
        self, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "out"
        site_root = output_dir / "osa"
        site_root.mkdir(parents=True)
        (site_root / "index.html").write_text("<html></html>")  # stands in for a real build

        site.finalize_publish_root(output_dir, "osa")

        assert (output_dir / "_headers").is_file()
        assert (output_dir / "_redirects").is_file()
        assert not (site_root / "_headers").exists()
        assert not (site_root / "_redirects").exists()

    def test_a_bare_host_writes_no_redirect_file(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        site.finalize_publish_root(output_dir, "")

        assert (output_dir / "_headers").is_file()
        assert not (output_dir / "_redirects").exists()


class TestStripSourcemaps:
    def test_removes_every_map_file_and_nothing_else(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        (output_dir / "build").mkdir(parents=True)
        (output_dir / "build" / "bundle.js").write_text("x")
        (output_dir / "build" / "bundle.js.map").write_text("{}")
        (output_dir / "jupyter-lite.json").write_text("{}")

        removed = site.strip_sourcemaps(output_dir)

        assert removed == 1
        assert (output_dir / "build" / "bundle.js").exists()
        assert not (output_dir / "build" / "bundle.js.map").exists()
        assert (output_dir / "jupyter-lite.json").exists()


class TestFetchPyodideLock:
    def test_a_verified_download_returns_the_parsed_lock(self) -> None:
        payload = json.dumps({"info": {}, "packages": {}}).encode()
        digest = hashlib.sha256(payload).hexdigest()
        server, url = self._serve(payload)
        try:
            result = site.fetch_pyodide_lock("0.29.5", digest, url=url)
        finally:
            server.shutdown()
        assert result == {"info": {}, "packages": {}}

    def test_a_mismatched_sha256_is_refused(self) -> None:
        payload = b'{"info": {}, "packages": {}}'
        server, url = self._serve(payload)
        try:
            with pytest.raises(site.NotebookSiteBuildError, match="sha256"):
                site.fetch_pyodide_lock("0.29.5", "0" * 64, url=url)
        finally:
            server.shutdown()

    @staticmethod
    def _serve(payload: bytes) -> tuple[http.server.ThreadingHTTPServer, str]:
        """A real local HTTP server returning `payload` for any GET -- a real
        socket and real bytes, not a stubbed function return value."""

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:  # noqa: ARG002
                pass  # keep test output quiet

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        return server, f"http://{host}:{port}/pyodide-lock.json"


@pytest.mark.network
class TestFullBuildEndToEnd:
    """The real `jupyter lite build`, the real Pyodide lock fetch, and the real
    NEMAR overlay, into a temp directory. Needs the network (PyPI, for the
    JupyterLite build tools; jsDelivr, for Pyodide's own lock) and runs `uv tool
    run`, so it is slow (a couple of minutes) -- marked ``network``, excluded from
    the ordinary `-m "not network"` runs in test.yml/tests.yml, and run in
    test.yml's separate "Run network tests" step (push to main / workflow_dispatch,
    continue-on-error) the same way every other real-network test in this suite is.
    """

    def test_the_real_site_builds_and_every_deliverable_is_present(self, tmp_path: Path) -> None:
        """Built with a path-prefixed --site-url, matching production
        (OSC's naming rule: notebook.osc.earth/osa, not the bare host)."""
        output_dir = tmp_path / "site"
        site_root = output_dir / "osa"

        site.build("https://notebook.osc.earth/osa", output_dir, "production", expose_app=True)

        assert (site_root / "jupyter-lite.json").exists()
        assert (site_root / "notebooks" / "index.html").exists()
        assert (site_root / "lab" / "index.html").exists()
        assert (site_root / "lock" / "pyodide-lock.json").exists()
        assert (
            site_root / "wheels" / "nemar" / "eegprep_lean-0.1.0.dev2-py3-none-any.whl"
        ).exists()
        assert (site_root / "wheels" / "nemar" / "zarr-3.4.0-py3-none-any.whl").exists()
        assert (site_root / "starters" / "nemar.ipynb").exists()
        assert (site_root / "starters" / "index.json").exists()
        for name in site.BOOTSTRAP_FILES:
            assert (site_root / name).exists()

        # _headers and _redirects live at the TRUE publish root, never under
        # osa/: Cloudflare Pages only reads them from exactly there.
        assert not (site_root / "_headers").exists()
        headers_text = (output_dir / "_headers").read_text()
        assert "/osa/wheels/*" in headers_text
        assert (output_dir / "_redirects").read_text().strip() == "/  /osa/  302"

        config = json.loads((site_root / "jupyter-lite.json").read_text())
        jcd = config["jupyter-config-data"]
        assert jcd["exposeAppInBrowser"] is True
        kernel = jcd["litePluginSettings"][site.KERNEL_PLUGIN_ID]
        assert kernel["pyodideUrl"] == f"{site.PYODIDE_CDN_BASE}pyodide.mjs"
        assert kernel["loadPyodideOptions"]["packageBaseUrl"] == site.PYODIDE_CDN_BASE
        assert kernel["pipliteUrls"]  # the build's own entry, preserved by the patch

        import nbformat

        notebook = nbformat.read(site_root / "starters" / "nemar.ipynb", as_version=4)
        nbformat.validate(notebook)

        # zarr_base/dataset_page_base filled in for "production"; {{dataset_id}}
        # left alone for open.js to fill client-side per reader.
        starter_text = json.dumps(notebook)
        assert "https://zarr.nemar.org" in starter_text
        assert "https://nemar.org/dataset/" in starter_text
        assert "{{zarr_base}}" not in starter_text
        assert "{{dataset_page_base}}" not in starter_text
        assert "{{dataset_id}}" in starter_text

        assert not list(output_dir.rglob("*.map")), "sourcemaps should have been stripped"

        merged_lock = json.loads((site_root / "lock" / "pyodide-lock.json").read_text())
        assert "numpy" in merged_lock["packages"]  # stock package, untouched
        assert merged_lock["packages"]["eegprep-lean"]["file_name"] == (
            "https://notebook.osc.earth/osa/wheels/nemar/eegprep_lean-0.1.0.dev2-py3-none-any.whl"
        )
