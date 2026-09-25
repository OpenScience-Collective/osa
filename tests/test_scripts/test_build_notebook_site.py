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
import os
import subprocess
import threading
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

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

        site.patch_root_config(output_dir)

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

    def test_always_exposes_the_app_for_the_bridge(self, tmp_path: Path) -> None:
        """osa-bridge.js drives the notebook through window.jupyterapp, so a
        deployed build without it would open notebooks whose setup never runs."""
        output_dir = tmp_path / "out"
        config_path = self._built_config(
            output_dir, {site.KERNEL_PLUGIN_ID: {"pipliteUrls": ["x"]}}
        )

        site.patch_root_config(output_dir)

        patched = json.loads(config_path.read_text())
        assert patched["jupyter-config-data"]["exposeAppInBrowser"] is True

    def test_sets_the_theme_and_autosave_overrides_and_keeps_existing_ones(
        self, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "out"
        config_path = self._built_config(
            output_dir, {site.KERNEL_PLUGIN_ID: {"pipliteUrls": ["x"]}}
        )
        data = json.loads(config_path.read_text())
        data["jupyter-config-data"]["settingsOverrides"] = {
            "@jupyterlab/docmanager-extension:plugin": {"autosave": True},
            "@jupyterlab/notebook-extension:tracker": {"kernelShutdown": False},
        }
        config_path.write_text(json.dumps(data))

        site.patch_root_config(output_dir)

        overrides = json.loads(config_path.read_text())["jupyter-config-data"]["settingsOverrides"]
        assert overrides["@jupyterlab/apputils-extension:themes"] == {"adaptive-theme": True}
        assert overrides["@jupyterlab/docmanager-extension:plugin"] == {
            "autosave": True,
            "autosaveInterval": 5,
        }
        assert overrides["@jupyterlab/notebook-extension:tracker"] == {"kernelShutdown": False}

    def test_missing_pipliteurls_is_refused(self, tmp_path: Path) -> None:
        """A JupyterLite version that stopped writing pipliteUrls must fail the
        build loudly, not silently ship a kernel that cannot install anything
        from PyPI at all."""
        output_dir = tmp_path / "out"
        self._built_config(output_dir, {site.KERNEL_PLUGIN_ID: {}})

        with pytest.raises(site.NotebookSiteBuildError, match="pipliteUrls"):
            site.patch_root_config(output_dir)

    def test_missing_config_file_is_refused(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        with pytest.raises(site.NotebookSiteBuildError, match="cannot be read"):
            site.patch_root_config(output_dir)


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

    def test_refuses_an_environment_missing_only_its_dataset_page_base(
        self, assistants_dir: Path, tmp_path: Path
    ) -> None:
        _write_community(
            assistants_dir,
            "nemarlike",
            with_overlay=False,
            dataset_page_base={"production": "https://example.org"},  # no "develop"
        )
        communities = site.discover_notebook_communities()
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        with pytest.raises(
            site.NotebookSiteBuildError,
            match="no dataset_page_base declared for environment 'develop'",
        ):
            site.write_starters(output_dir, communities, "develop")


class TestNemarStarterPerEnvironment:
    """The SHIPPED NEMAR config and starter, filled for each environment, offline.

    A typo in config.yaml's develop hosts (say, dataset_page_base.develop set to
    production's https://nemar.org) builds cleanly and passes e2e-check.js, which
    only reads data; only an exact assertion on the filled starter catches it.
    """

    @pytest.mark.parametrize(
        ("environment", "zarr_base", "page_base", "other_zarr_base", "other_page_base"),
        [
            (
                "production",
                "https://zarr.nemar.org",
                "https://nemar.org",
                "https://zarr-test.nemar.org",
                "https://test.nemar.org",
            ),
            (
                "develop",
                "https://zarr-test.nemar.org",
                "https://test.nemar.org",
                "https://zarr.nemar.org",
                "https://nemar.org",
            ),
        ],
    )
    def test_the_starter_reads_and_links_this_environments_hosts_only(
        self,
        tmp_path: Path,
        environment: str,
        zarr_base: str,
        page_base: str,
        other_zarr_base: str,
        other_page_base: str,
    ) -> None:
        communities = site.discover_notebook_communities()
        assert "nemar" in communities

        site.write_starters(tmp_path, communities, environment)

        starter = json.loads((tmp_path / "starters" / "nemar.ipynb").read_text())
        source = "\n".join(
            "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
            for cell in starter["cells"]
        )
        assert f'index_url="{zarr_base}/{{{{dataset_id}}}}/zarr/index.json"' in source
        assert f"]({page_base}/dataset/{{{{dataset_id}}}})" in source
        assert f"{other_zarr_base}/" not in source
        assert f"]({other_page_base}/" not in source


class TestEnvironmentIsRequired:
    def test_the_cli_refuses_a_build_that_names_no_environment(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exited:
            site.main(["--site-url", "https://x.example/osa", "--output-dir", str(tmp_path)])

        assert exited.value.code == 2
        assert "--environment" in capsys.readouterr().err

    def test_the_cli_refuses_an_unknown_environment(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exited:
            site.main(
                [
                    "--site-url",
                    "https://x.example/osa",
                    "--output-dir",
                    str(tmp_path),
                    "--environment",
                    "staging",
                ]
            )

        assert exited.value.code == 2
        assert "invalid choice: 'staging'" in capsys.readouterr().err

    def test_build_refuses_an_unknown_environment_before_touching_the_output(
        self, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "out"

        with pytest.raises(site.NotebookSiteBuildError, match="must be one of"):
            site.build("https://x.example/osa", output_dir, "staging")

        assert not output_dir.exists()

    def test_build_refuses_an_undeclared_environment_before_the_jupyterlite_build(
        self, assistants_dir: Path, tmp_path: Path
    ) -> None:
        """The pre-flight check has to fire before run_jupyterlite_build, which
        needs the network and writes the site: if it only fired in
        write_starters, the build would get this far and leave a site behind."""
        _write_community(
            assistants_dir,
            "nemarlike",
            with_overlay=False,
            zarr_base={"production": "https://zarr.example.org"},
        )
        output_dir = tmp_path / "out"

        with pytest.raises(
            site.NotebookSiteBuildError, match="no zarr_base declared for environment 'develop'"
        ):
            site.build("https://x.example/osa", output_dir, "develop")

        assert not output_dir.exists()


def _deploy_steps() -> dict[str, dict]:
    workflow = yaml.safe_load(
        (site.ROOT / ".github" / "workflows" / "deploy-notebook.yml").read_text()
    )
    return {step.get("name"): step for step in workflow["jobs"]["deploy"]["steps"]}


class TestDeployWorkflowEnvironment:
    """deploy-notebook.yml's branch-to-environment mapping, run for real.

    e2e-check.js always names its environment explicitly, so a swap of
    production and develop here would pass every other check and ship a
    production site that reads staging's data host.
    """

    @pytest.mark.parametrize(
        ("branch", "url", "environment"),
        [
            ("main", "https://notebook.osc.earth/osa", "production"),
            ("develop", "https://develop-notebook.osc.earth/osa", "develop"),
        ],
    )
    def test_each_branch_builds_for_its_own_environment(
        self, tmp_path: Path, branch: str, url: str, environment: str
    ) -> None:
        script = _deploy_steps()["Pick the site URL for this branch"]["run"]
        script = script.replace("${{ github.ref_name }}", branch)
        assert "${{" not in script, "the step reads an expression this test does not fill in"
        github_output = tmp_path / "github_output"

        subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            env={**os.environ, "GITHUB_OUTPUT": str(github_output)},
            check=True,
        )

        outputs = dict(line.split("=", 1) for line in github_output.read_text().splitlines())
        assert outputs["url"] == url
        assert outputs["environment"] == environment

    def test_the_build_step_passes_the_picked_url_and_environment(self) -> None:
        run = _deploy_steps()["Build the notebook site"]["run"]

        assert '--site-url "${{ steps.site.outputs.url }}"' in run
        assert '--environment "${{ steps.site.outputs.environment }}"' in run

    def test_the_site_check_runs_the_browser_check_for_every_environment(self) -> None:
        workflow = yaml.safe_load(
            (site.ROOT / ".github" / "workflows" / "notebook-site-check.yml").read_text()
        )
        runs = [step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]]

        for environment in site.NOTEBOOK_ENVIRONMENTS:
            assert f"bun notebook/e2e-check.js --environment {environment}" in runs


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

        site.write_headers(publish_root, "osa", ["https://nemar.org"])

        text = (publish_root / "_headers").read_text()
        assert "/osa/wheels/*" in text
        assert "/osa/open.html" in text
        assert "/osa/*" in text  # the site-wide CSP/Referrer-Policy block
        assert "\n/wheels/*" not in text  # the unprefixed form is gone

    def test_a_comment_line_is_left_alone(self, tmp_path: Path) -> None:
        publish_root = tmp_path / "out"
        publish_root.mkdir()

        site.write_headers(publish_root, "osa", ["https://nemar.org"])

        text = (publish_root / "_headers").read_text()
        assert any(line.startswith("#") for line in text.splitlines())

    def test_no_subdir_leaves_patterns_unprefixed(self, tmp_path: Path) -> None:
        publish_root = tmp_path / "out"
        publish_root.mkdir()

        site.write_headers(publish_root, "", ["https://nemar.org"])

        text = (publish_root / "_headers").read_text()
        assert "\n/wheels/*" in f"\n{text}"

    def test_frame_ancestors_is_self_then_the_given_origins(self, tmp_path: Path) -> None:
        publish_root = tmp_path / "out"
        publish_root.mkdir()

        site.write_headers(publish_root, "osa", ["https://nemar.org", "https://*.osc.earth"])

        text = (publish_root / "_headers").read_text()
        assert (
            "Content-Security-Policy: frame-ancestors 'self' https://nemar.org https://*.osc.earth"
            in text
        )
        assert site.FRAME_ANCESTORS_TOKEN not in text
        assert "'none'" not in text
        # For a browser too old to read frame-ancestors: it refuses to embed at all.
        assert "X-Frame-Options: SAMEORIGIN" in text

    def test_the_pages_served_open_path_is_not_cached(self, tmp_path: Path) -> None:
        """Cloudflare Pages answers /osa/open.html with a 308 to /osa/open, and a
        header rule matches the path served, so /osa/open needs its own rule."""
        publish_root = tmp_path / "out"
        publish_root.mkdir()

        site.write_headers(publish_root, "osa", [])

        lines = (publish_root / "_headers").read_text().splitlines()
        for path in ("/osa/open", "/osa/open.html", "/osa/osa-bridge.js"):
            assert path in lines
            assert lines[lines.index(path) + 1].strip() == "Cache-Control: no-cache"

    def test_a_template_without_the_frame_ancestors_token_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        template = tmp_path / "_headers"
        template.write_text("/*\n  Content-Security-Policy: frame-ancestors 'none'\n")
        monkeypatch.setattr(site, "HEADERS_TEMPLATE", template)
        publish_root = tmp_path / "out"
        publish_root.mkdir()

        with pytest.raises(site.NotebookSiteBuildError, match="exactly once"):
            site.write_headers(publish_root, "osa", ["https://nemar.org"])


class TestEmbedOrigins:
    def test_production_is_the_platform_hosts_then_each_community_origin(
        self, assistants_dir: Path
    ) -> None:
        _write_community(assistants_dir, "nemarlike", with_overlay=False)
        communities = site.discover_notebook_communities()
        communities["nemarlike"].cors_origins = ["https://example.org", "https://demo.osc.earth"]

        origins = site.embed_origins(communities, "production")

        assert origins == [
            "https://demo.osc.earth",
            "https://osa-demo.pages.dev",
            "https://example.org",
        ]

    def test_every_build_environment_declares_its_platform_hosts(self) -> None:
        assert set(site.PLATFORM_EMBED_ORIGINS) == set(site.NOTEBOOK_ENVIRONMENTS)

    def test_only_develop_admits_loopback_and_the_preview_hosts(self) -> None:
        production = site.embed_origins({}, "production")
        develop = site.embed_origins({}, "develop")

        for origin in ("http://localhost:*", "http://127.0.0.1:*", "https://*.osc.earth"):
            assert origin in develop
            assert origin not in production

    @pytest.mark.parametrize(
        "origin",
        [
            "https://*-preview.example.org",  # a partial-label wildcard
            'https://nemar.org"; frame-ancestors *',  # would end the header value
            "https://nemar.org;evil",
            "https://nemar.org\nX-Injected: 1",  # would start a new header line
            "https://nemar.org/path",
        ],
    )
    def test_an_origin_frame_ancestors_cannot_express_is_refused(
        self, assistants_dir: Path, origin: str
    ) -> None:
        _write_community(assistants_dir, "nemarlike", with_overlay=False)
        communities = site.discover_notebook_communities()
        communities["nemarlike"].cors_origins = [origin]

        with pytest.raises(site.NotebookSiteBuildError, match="frame-ancestors"):
            site.embed_origins(communities, "develop")

    @pytest.mark.parametrize(
        "origin",
        [
            "https://*-preview.example.org",
            'https://nemar.org"; frame-ancestors *',
            "https://nemar.org\nX-Injected: 1",
        ],
    )
    def test_the_config_loader_refuses_such_an_origin_first(
        self, assistants_dir: Path, origin: str
    ) -> None:
        """CommunityConfig's own cors_origins rule is the first guard; the check
        in embed_origins is the second, for an origin that reaches it some other
        way, or a loader rule loosened later."""
        _write_community(assistants_dir, "nemarlike", with_overlay=False)
        config = assistants_dir / "nemarlike" / "config.yaml"
        config.write_text(config.read_text() + f"cors_origins:\n  - {json.dumps(origin)}\n")

        with pytest.raises(ValidationError, match="Invalid CORS origin"):
            site.discover_notebook_communities()

    def test_the_shipped_nemar_config_may_embed_from_its_own_sites(self) -> None:
        communities = site.discover_notebook_communities()

        for environment in site.NOTEBOOK_ENVIRONMENTS:
            origins = site.embed_origins(communities, environment)
            for origin in ("https://nemar.org", "https://www.nemar.org", "https://test.nemar.org"):
                assert origin in origins, (environment, origin)


class TestInjectBridge:
    def _page(self, site_root: Path, html: str) -> Path:
        page = site_root / site.NOTEBOOK_PAGE
        page.parent.mkdir(parents=True)
        page.write_text(html)
        return page

    def test_adds_the_bridge_once_just_before_the_end_of_head(self, tmp_path: Path) -> None:
        page = self._page(tmp_path, "<html><head><title>n</title></head><body></body></html>")

        site.inject_bridge(tmp_path)

        html = page.read_text()
        assert html.count(site.BRIDGE_SCRIPT_TAG) == 1
        assert html.index(site.BRIDGE_SCRIPT_TAG) < html.index("</head>")
        assert "<title>n</title>" in html

    def test_a_page_without_a_single_head_end_is_refused(self, tmp_path: Path) -> None:
        self._page(tmp_path, "<html><body></body></html>")

        with pytest.raises(site.NotebookSiteBuildError, match="exactly one </head>"):
            site.inject_bridge(tmp_path)

    def test_a_page_with_two_head_ends_is_refused(self, tmp_path: Path) -> None:
        self._page(tmp_path, "<html><head></head><head></head></html>")

        with pytest.raises(site.NotebookSiteBuildError, match="exactly one </head>"):
            site.inject_bridge(tmp_path)

    def test_a_page_that_already_has_the_bridge_is_refused(self, tmp_path: Path) -> None:
        self._page(tmp_path, f"<html><head>{site.BRIDGE_SCRIPT_TAG}</head></html>")

        with pytest.raises(site.NotebookSiteBuildError, match="already references"):
            site.inject_bridge(tmp_path)


class TestNemarStarterSetupCell:
    def test_the_first_code_cell_is_the_only_one_that_runs_on_open(self) -> None:
        config = site.discover_notebook_communities()["nemar"]
        assert config.notebook is not None
        starter = json.loads((site.ASSISTANTS_DIR / "nemar" / config.notebook.starter).read_text())
        code = [cell for cell in starter["cells"] if cell["cell_type"] == "code"]
        tagged = [cell for cell in code if "osa-autorun" in cell["metadata"].get("tags", [])]

        assert tagged == [code[0]]
        source = "".join(code[0]["source"])
        assert "%pip install eegprep-lean" in source
        assert "import eegprep_lean" in source


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

        site.finalize_publish_root(output_dir, "osa", ["https://nemar.org"])

        assert (output_dir / "_headers").is_file()
        assert (output_dir / "_redirects").is_file()
        assert not (site_root / "_headers").exists()
        assert not (site_root / "_redirects").exists()

    def test_a_bare_host_writes_no_redirect_file(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        site.finalize_publish_root(output_dir, "", ["https://nemar.org"])

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

        site.build("https://notebook.osc.earth/osa", output_dir, "production")

        assert (site_root / "jupyter-lite.json").exists()
        assert (site_root / "notebooks" / "index.html").exists()
        assert (site_root / "lab" / "index.html").exists()
        assert (site_root / "lock" / "pyodide-lock.json").exists()
        assert (
            site_root / "wheels" / "nemar" / "eegprep_lean-0.1.0.dev3-py3-none-any.whl"
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
        # The widget's hosts may embed the notebook; loopback, develop only, may not.
        assert "frame-ancestors 'self' https://demo.osc.earth" in headers_text
        assert "https://nemar.org" in headers_text
        assert "localhost" not in headers_text
        notebook_page = (site_root / "notebooks" / "index.html").read_text()
        assert notebook_page.count(site.BRIDGE_SCRIPT_TAG) == 1
        assert (output_dir / "_redirects").read_text().strip() == "/  /osa/  302"

        config = json.loads((site_root / "jupyter-lite.json").read_text())
        jcd = config["jupyter-config-data"]
        assert jcd["exposeAppInBrowser"] is True
        for plugin_id, values in site.NOTEBOOK_SETTINGS_OVERRIDES.items():
            assert values.items() <= jcd["settingsOverrides"][plugin_id].items()
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
            "https://notebook.osc.earth/osa/wheels/nemar/eegprep_lean-0.1.0.dev3-py3-none-any.whl"
        )
