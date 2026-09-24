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

from scripts.eegprep_lean_drift import DriftReport, check_drift, main, render_human

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
    tmp_path: Path,
    *,
    commit: str = RECORDED_COMMIT,
    version: str = RECORDED_VERSION,
    reviewed_through: str | None = None,
) -> tuple[Path, Path]:
    """A real sources.toml and a real overlay, the shape check_drift actually reads."""
    sources = tmp_path / "sources.toml"
    reviewed = f'reviewed_through = "{reviewed_through}"\n' if reviewed_through else ""
    sources.write_text(
        "[eegprep-lean]\n"
        f'wheel = "eegprep_lean-{version}-py3-none-any.whl"\n'
        f'repository = "{REPO}"\n'
        f'branch = "{BRANCH}"\n'
        f'path = "{PKG_PATH}"\n'
        f'commit = "{commit}"\n' + reviewed
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


class TestReviewedThrough:
    """A later upstream commit that changes nothing the wheel ships is acknowledged
    with `reviewed_through`, since a re-vendor needs a new version and a new file name."""

    @respx.mock
    def test_current_when_upstream_stopped_at_the_reviewed_commit(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path, reviewed_through=UPSTREAM_COMMIT)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(UPSTREAM_COMMIT))
        respx.get(CONTENTS_URL).mock(return_value=_pyproject_response(RECORDED_VERSION))

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "current"
        assert report.reviewed_through == UPSTREAM_COMMIT

    @respx.mock
    def test_behind_when_the_reviewed_commit_moved_the_version(self, tmp_path: Path) -> None:
        """A review cannot cover a new release: that is a re-vendor."""
        sources, overlay = _write_recorded(tmp_path, reviewed_through=UPSTREAM_COMMIT)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(UPSTREAM_COMMIT))
        respx.get(CONTENTS_URL).mock(return_value=_pyproject_response(UPSTREAM_VERSION))

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "behind"
        assert report.version_moved is True

    @respx.mock
    def test_behind_when_upstream_moved_past_the_reviewed_commit(self, tmp_path: Path) -> None:
        later = "c" * 40
        sources, overlay = _write_recorded(tmp_path, reviewed_through=UPSTREAM_COMMIT)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(later))
        respx.get(CONTENTS_URL).mock(return_value=_pyproject_response(RECORDED_VERSION))

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "behind"
        assert report.upstream_commit == later

    def test_an_empty_reviewed_through_is_unknown_not_ignored(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        sources.write_text(sources.read_text() + 'reviewed_through = ""\n')

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "unknown"
        assert "reviewed_through" in (report.reason or "")


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
        """No network involved at all: the earliest failure point, which must read as
        unknown rather than as anything a caller could act on."""
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


class TestGitHubResponseShapes:
    """What GitHub sends when something is wrong is still a response, and each
    shape must read as unknown rather than crash or pass."""

    @respx.mock
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(403, json={"message": "API rate limit exceeded"}),
            httpx.Response(500, text="upstream failure"),
            httpx.Response(200, json={"message": "an object, not a list"}),
            httpx.Response(200, json=[]),
            httpx.Response(200, json=[{"sha": 12345}]),
            httpx.Response(200, json=[{}]),
        ],
        ids=["rate-limited", "server-error", "object", "empty", "sha-not-a-string", "no-sha"],
    )
    def test_each_is_unknown(self, tmp_path: Path, response: httpx.Response) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=response)

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "unknown"
        assert report.reason

    @respx.mock
    def test_base64_wrapped_as_github_sends_it_decodes(self, tmp_path: Path) -> None:
        """GitHub's contents API wraps base64 at 60 characters with newlines."""
        text = (
            f'[project]\nname = "eegprep-lean"\nversion = "{UPSTREAM_VERSION}"\n' + "# pad\n" * 20
        )
        encoded = base64.encodebytes(text.encode()).decode()
        assert "\n" in encoded.strip()
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(UPSTREAM_COMMIT))
        respx.get(CONTENTS_URL).mock(
            return_value=httpx.Response(200, json={"content": encoded, "encoding": "base64"})
        )

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "behind"
        assert report.upstream_version == UPSTREAM_VERSION

    def test_an_overlay_that_is_not_an_object_is_unknown(self, tmp_path: Path) -> None:
        sources, overlay = _write_recorded(tmp_path)
        overlay.write_text("null")

        with httpx.Client() as client:
            report = check_drift(sources, overlay, client=client)

        assert report.status == "unknown"
        assert "not a JSON object" in (report.reason or "")


class TestRenderHuman:
    """The text that lands in the tracking issue."""

    def test_current(self) -> None:
        text = render_human(
            DriftReport(
                status="current",
                recorded_commit=RECORDED_COMMIT,
                recorded_version=RECORDED_VERSION,
                upstream_commit=RECORDED_COMMIT,
                upstream_version=RECORDED_VERSION,
                version_moved=False,
            )
        )

        assert "  status: current" in text
        assert f"recorded: commit {RECORDED_COMMIT} (version {RECORDED_VERSION})" in text

    def test_behind_with_a_new_version_names_both(self) -> None:
        text = render_human(
            DriftReport(
                status="behind",
                recorded_commit=RECORDED_COMMIT,
                recorded_version=RECORDED_VERSION,
                upstream_commit=UPSTREAM_COMMIT,
                upstream_version=UPSTREAM_VERSION,
                version_moved=True,
            )
        )

        assert f"version moved {RECORDED_VERSION} -> {UPSTREAM_VERSION}" in text
        assert "reviewed_through" not in text
        assert "refresh procedure: src/assistants/nemar/runtime/README.md" in text

    def test_behind_without_a_new_version_says_what_to_do(self) -> None:
        text = render_human(
            DriftReport(
                status="behind",
                recorded_commit=RECORDED_COMMIT,
                recorded_version=RECORDED_VERSION,
                upstream_commit=UPSTREAM_COMMIT,
                upstream_version=RECORDED_VERSION,
                version_moved=False,
            )
        )

        assert f"version unchanged at {RECORDED_VERSION}" in text
        assert f"set reviewed_through in sources.toml to {UPSTREAM_COMMIT}" in text
        assert "a re-vendor needs a new upstream version first" in text

    def test_unknown_gives_the_reason_and_is_not_current(self) -> None:
        text = render_human(
            DriftReport(
                status="unknown",
                recorded_commit=RECORDED_COMMIT,
                recorded_version=RECORDED_VERSION,
                reason="GitHub's commits request returned 403",
            )
        )

        assert "status: unknown -- GitHub's commits request returned 403" in text
        assert "unknown is not current" in text

    def test_a_reviewed_commit_is_named(self) -> None:
        text = render_human(
            DriftReport(
                status="current",
                recorded_commit=RECORDED_COMMIT,
                recorded_version=RECORDED_VERSION,
                reviewed_through=UPSTREAM_COMMIT,
                upstream_commit=UPSTREAM_COMMIT,
                upstream_version=RECORDED_VERSION,
                version_moved=False,
            )
        )

        assert f"reviewed through: commit {UPSTREAM_COMMIT}" in text


class TestMainReports:
    @respx.mock
    def test_the_human_report_and_the_status_line_are_both_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The workflow reads the status from the status line, never the exit code."""
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(return_value=_commits_response(UPSTREAM_COMMIT))
        respx.get(CONTENTS_URL).mock(return_value=_pyproject_response(UPSTREAM_VERSION))

        main(["--sources", str(sources), "--overlay", str(overlay)])

        out = capsys.readouterr().out.splitlines()
        assert out[0] == "eegprep-lean drift check"
        assert out[-1].startswith("status=behind ")

    @respx.mock
    def test_a_crash_inside_the_check_is_reported_as_unknown(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sources, overlay = _write_recorded(tmp_path)
        respx.get(COMMITS_URL).mock(side_effect=RuntimeError("boom"))

        code = main(["--sources", str(sources), "--overlay", str(overlay)])

        assert code == 2
        out = capsys.readouterr().out
        assert out.splitlines()[-1].startswith("status=unknown ")
        assert "RuntimeError: boom" in out

    @respx.mock
    @pytest.mark.parametrize(
        ("upstream", "version", "fields"),
        [
            (UPSTREAM_COMMIT, UPSTREAM_VERSION, {"status": "behind", "version_moved": True}),
            (None, None, {"status": "unknown", "upstream_commit": None}),
        ],
        ids=["behind", "unknown"],
    )
    def test_json_carries_the_fields_for_each_status(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        upstream: str | None,
        version: str | None,
        fields: dict,
    ) -> None:
        sources, overlay = _write_recorded(tmp_path)
        if upstream is None:
            respx.get(COMMITS_URL).mock(return_value=httpx.Response(404))
        else:
            respx.get(COMMITS_URL).mock(return_value=_commits_response(upstream))
            respx.get(CONTENTS_URL).mock(return_value=_pyproject_response(version))

        main(["--sources", str(sources), "--overlay", str(overlay), "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert {key: payload[key] for key in fields} == fields
        assert set(payload) == {
            "status",
            "recorded_commit",
            "recorded_version",
            "reviewed_through",
            "upstream_commit",
            "upstream_version",
            "version_moved",
            "reason",
        }
