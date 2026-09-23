"""scripts/eegprep_lean_drift.py: does the vendored eegprep-lean wheel match upstream?

Real files on disk for `sources.toml` and the overlay -- the NO MOCK policy's business
logic side -- and respx for GitHub's responses, which is exactly the HTTP-boundary
exception `.rules/testing.md` names for this watcher. Every GitHub call goes through
`check_drift`'s own `client` parameter, so a test never depends on
`eegprep_lean_drift.main` constructing one, and the exit-code/CLI tests below are the
only ones that go through `main` at all.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
import respx

from scripts.eegprep_lean_drift import check_drift, main

REPO = "sccn/eegprep"
BRANCH = "develop"
PKG_PATH = "packages/eegprep-lean"
RECORDED_COMMIT = "67670f5b0f8e8b47bf30df78a9700f2a12a94c79"
RECORDED_VERSION = "0.1.0.dev1"
UPSTREAM_COMMIT = "b4aa18bdbc0556c943a10ca3bd4105f6b4971ed6"
UPSTREAM_VERSION = "0.1.0.dev2"

COMMITS_URL = f"https://api.github.com/repos/{REPO}/commits"
CONTENTS_URL = f"https://api.github.com/repos/{REPO}/contents/{PKG_PATH}/pyproject.toml"


def _write_recorded(
    tmp_path: Path, *, commit: str = RECORDED_COMMIT, version: str = RECORDED_VERSION
) -> tuple[Path, Path]:
    """A real sources.toml and a real overlay, the shape check_drift actually reads."""
    sources = tmp_path / "sources.toml"
    sources.write_text(
        "[eegprep-lean]\n"
        f'wheel = "eegprep_lean-{version}-py3-none-any.whl"\n'
        f'repository = "{REPO}"\n'
        f'branch = "{BRANCH}"\n'
        f'path = "{PKG_PATH}"\n'
        f'commit = "{commit}"\n'
    )
    overlay = tmp_path / "nemar-pyodide-lock.json"
    overlay.write_text(json.dumps({"packages": {"eegprep-lean": {"version": version}}}))
    return sources, overlay


def _commits_response(sha: str) -> httpx.Response:
    return httpx.Response(200, json=[{"sha": sha}])


def _pyproject_response(version: str) -> httpx.Response:
    text = f'[project]\nname = "eegprep-lean"\nversion = "{version}"\n'
    encoded = base64.b64encode(text.encode()).decode()
    return httpx.Response(200, json={"content": encoded, "encoding": "base64"})


class TestCurrent:
    @respx.mock
    def test_current_when_upstreams_newest_commit_is_the_recorded_one(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(RECORDED_COMMIT))

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "current"
        assert report.upstream_commit == RECORDED_COMMIT
        assert report.upstream_version == RECORDED_VERSION
        assert report.version_moved is False


class TestBehind:
    @respx.mock
    def test_behind_when_the_commit_moved_but_the_version_did_not(self, tmp_path: Path) -> None:
        """Two upstream commits without a version bump: a version-string-only compare
        would say 'current' here, and that would be wrong."""
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(UPSTREAM_COMMIT))
        respx.get(CONTENTS_URL).mock(return_value=_pyproject_response(RECORDED_VERSION))

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "behind"
        assert report.upstream_commit == UPSTREAM_COMMIT
        assert report.upstream_version == RECORDED_VERSION
        assert report.version_moved is False

    @respx.mock
    def test_behind_when_the_version_moved_too(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(UPSTREAM_COMMIT))
        respx.get(CONTENTS_URL).mock(return_value=_pyproject_response(UPSTREAM_VERSION))

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "behind"
        assert report.upstream_commit == UPSTREAM_COMMIT
        assert report.upstream_version == UPSTREAM_VERSION
        assert report.version_moved is True


class TestUnknown:
    """Unknown is never current: every case here must not report 'current'."""

    @respx.mock
    def test_unknown_on_a_404(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=httpx.Response(404))

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "unknown"
        assert report.status != "current"
        assert "404" in (report.reason or "")

    @respx.mock
    def test_unknown_on_a_timeout(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(side_effect=httpx.TimeoutException("timed out"))

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "unknown"
        assert report.status != "current"
        assert "timed out" in (report.reason or "")

    @respx.mock
    def test_unknown_on_malformed_json(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(
            return_value=httpx.Response(
                200, content=b"not json", headers={"content-type": "application/json"}
            )
        )

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "unknown"
        assert report.status != "current"
        assert "JSON" in (report.reason or "")

    @respx.mock
    def test_unknown_on_a_pyproject_without_a_version(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(UPSTREAM_COMMIT))
        encoded = base64.b64encode(b'[project]\nname = "eegprep-lean"\n').decode()
        respx.get(CONTENTS_URL).mock(
            return_value=httpx.Response(200, json={"content": encoded, "encoding": "base64"})
        )

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "unknown"
        assert report.status != "current"
        assert "version" in (report.reason or "")

    def test_unknown_when_sources_toml_cannot_even_be_read(self, tmp_path: Path) -> None:
        """No network involved at all: the earliest failure point. This is also the
        path the mutation check exercises (see this repository's PR for the run): drop
        the ``status = "unknown"`` assignment in ``check_drift``'s outermost except
        clause and this test fails, because ``status`` never leaves its ``"current"``
        default."""
        sources = tmp_path / "sources.toml"  # deliberately never created
        overlay = tmp_path / "nemar-pyodide-lock.json"
        overlay.write_text(json.dumps({"packages": {"eegprep-lean": {"version": "0.1.0.dev1"}}}))

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "unknown"
        assert report.status != "current"


class TestExitCodesAndCli:
    @respx.mock
    def test_exit_code_0_for_current(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(RECORDED_COMMIT))

        code = main(["--sources", str(sources), "--overlay", str(overlay)])

        assert code == 0
        assert "current" in capsys.readouterr().out

    @respx.mock
    def test_exit_code_1_for_behind(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(UPSTREAM_COMMIT))
        respx.get(CONTENTS_URL).mock(return_value=_pyproject_response(UPSTREAM_VERSION))

        assert main(["--sources", str(sources), "--overlay", str(overlay)]) == 1

    @respx.mock
    def test_exit_code_2_for_unknown(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=httpx.Response(404))

        assert main(["--sources", str(sources), "--overlay", str(overlay)]) == 2

    @respx.mock
    def test_json_output_is_one_parseable_object(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(RECORDED_COMMIT))

        main(["--sources", str(sources), "--overlay", str(overlay), "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "current"
