"""Is the vendored eegprep-lean wheel still what upstream's `develop` branch has?

    uv run python scripts/eegprep_lean_drift.py
    uv run python scripts/eegprep_lean_drift.py --json

Reads `src/assistants/nemar/runtime/sources.toml` for where the vendored wheel came
from (repository, branch, path, commit) and the lock overlay beside it
(`nemar-pyodide-lock.json`) for the version that commit was built at, then asks GitHub
for the newest commit on that branch touching that path.

The comparison is by COMMIT, not by version string: a version string alone is too
weak, because it can sit still across more than one upstream change (sccn/eegprep
#415/#416 landed as two commits before the version moved). Three outcomes:

- ``current``: upstream's newest commit on the branch, for the path, is the one
  recorded here.
- ``behind``: it is not. The report carries both commits and both versions, and
  whether the version moved between them.
- ``unknown``: a fetch or a parse failed anywhere along the way, and the report says
  which. Unknown is never reported as current -- an agent or a workflow that cannot
  tell must not act as though nothing changed.

``.github/workflows/eegprep-lean-watch.yml`` runs this weekly; nothing else watches
upstream for this package. GitHub's API is public and unauthenticated calls work, but
carry ``GITHUB_TOKEN`` when it is set, both for the workflow's own budget and because
an anonymous caller shares the lowest, IP-keyed rate limit with everyone else.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = ROOT / "src" / "assistants" / "nemar" / "runtime" / "sources.toml"
DEFAULT_OVERLAY = ROOT / "src" / "assistants" / "nemar" / "runtime" / "nemar-pyodide-lock.json"

GITHUB_API = "https://api.github.com"
#: A named client, since an unnamed one is not something GitHub's API -- or an
#: operator reading its own request logs -- should have to guess about.
USER_AGENT = "osa-eegprep-lean-drift (+https://github.com/OpenScience-Collective/osa)"
REQUEST_TIMEOUT_S = 15.0

REFRESH_PROCEDURE = "src/assistants/nemar/runtime/README.md"

EXIT_CODES = {"current": 0, "behind": 1, "unknown": 2}


class DriftError(Exception):
    """One fetch or parse step failed. The message says which."""


def _github_headers() -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@dataclass(frozen=True)
class Recorded:
    """What `sources.toml` and the overlay say is vendored today."""

    repository: str
    branch: str
    path: str
    commit: str
    version: str
    reviewed_through: str | None = None


def _read_recorded(sources_path: Path, overlay_path: Path) -> Recorded:
    try:
        sources_text = sources_path.read_text()
    except OSError as err:
        raise DriftError(f"{sources_path} cannot be read: {err}") from err
    try:
        sources = tomllib.loads(sources_text)
    except tomllib.TOMLDecodeError as err:
        raise DriftError(f"{sources_path} is not valid TOML: {err}") from err

    entry = sources.get("eegprep-lean")
    if not isinstance(entry, dict):
        raise DriftError(f"{sources_path} has no [eegprep-lean] table")
    required = ("repository", "branch", "path", "commit")
    missing = [key for key in required if not isinstance(entry.get(key), str) or not entry[key]]
    if missing:
        raise DriftError(f"{sources_path}'s [eegprep-lean] table is missing {', '.join(missing)}")

    try:
        overlay_text = overlay_path.read_text()
    except OSError as err:
        raise DriftError(f"{overlay_path} cannot be read: {err}") from err
    try:
        overlay = json.loads(overlay_text)
    except json.JSONDecodeError as err:
        raise DriftError(f"{overlay_path} is not valid JSON: {err}") from err

    if not isinstance(overlay, dict):
        raise DriftError(f"{overlay_path} is not a JSON object")
    packages = overlay.get("packages")
    package = packages.get("eegprep-lean") if isinstance(packages, dict) else None
    version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(version, str) or not version:
        raise DriftError(f"{overlay_path} names no eegprep-lean version")

    reviewed_through = entry.get("reviewed_through")
    if reviewed_through is not None and (
        not isinstance(reviewed_through, str) or not reviewed_through
    ):
        raise DriftError(f"{sources_path}'s reviewed_through must be a commit sha")
    return Recorded(
        repository=entry["repository"],
        branch=entry["branch"],
        path=entry["path"],
        commit=entry["commit"],
        version=version,
        reviewed_through=reviewed_through,
    )


def _latest_commit(client: httpx.Client, repository: str, branch: str, path: str) -> str:
    """The newest commit sha on `branch` that touched `path`, GitHub's own ordering."""
    url = f"{GITHUB_API}/repos/{repository}/commits"
    try:
        response = client.get(
            url,
            params={"sha": branch, "path": path, "per_page": 1},
            headers=_github_headers(),
            timeout=REQUEST_TIMEOUT_S,
        )
    except httpx.TimeoutException as err:
        raise DriftError(
            f"timed out asking GitHub for the newest commit on {repository}@{branch} touching {path}"
        ) from err
    except httpx.HTTPError as err:
        raise DriftError(
            f"could not reach GitHub for the newest commit on {repository}@{branch} touching {path}: {err}"
        ) from err
    if response.status_code != 200:
        raise DriftError(
            f"GitHub's commits request for {repository}@{branch}:{path} returned "
            f"{response.status_code}"
        )
    try:
        commits = response.json()
    except ValueError as err:
        raise DriftError(f"GitHub's commits response was not valid JSON: {err}") from err
    if not isinstance(commits, list) or not commits or not isinstance(commits[0], dict):
        raise DriftError(f"GitHub reported no commits on {branch} touching {path}")
    sha = commits[0].get("sha")
    if not isinstance(sha, str) or not sha:
        raise DriftError("GitHub's newest commit had no sha")
    return sha


def _pyproject_version(client: httpx.Client, repository: str, path: str, commit: str) -> str:
    """`[project].version` from `<path>/pyproject.toml`, as committed at `commit`."""
    url = f"{GITHUB_API}/repos/{repository}/contents/{path}/pyproject.toml"
    try:
        response = client.get(
            url, params={"ref": commit}, headers=_github_headers(), timeout=REQUEST_TIMEOUT_S
        )
    except httpx.TimeoutException as err:
        raise DriftError(f"timed out fetching pyproject.toml at {commit}") from err
    except httpx.HTTPError as err:
        raise DriftError(f"could not fetch pyproject.toml at {commit}: {err}") from err
    if response.status_code != 200:
        raise DriftError(
            f"GitHub's contents request for pyproject.toml at {commit} returned "
            f"{response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as err:
        raise DriftError(
            f"GitHub's contents response for pyproject.toml at {commit} was not valid JSON: {err}"
        ) from err
    encoded = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(encoded, str):
        raise DriftError(
            f"GitHub's contents response for pyproject.toml at {commit} had no content"
        )
    try:
        text = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as err:
        raise DriftError(f"pyproject.toml at {commit} could not be decoded: {err}") from err
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise DriftError(f"pyproject.toml at {commit} is not valid TOML: {err}") from err
    project = data.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or not version:
        raise DriftError(f"pyproject.toml at {commit} has no [project].version")
    return version


@dataclass(frozen=True)
class DriftReport:
    status: str  # "current" | "behind" | "unknown"
    recorded_commit: str
    recorded_version: str
    reviewed_through: str | None = None
    upstream_commit: str | None = None
    upstream_version: str | None = None
    version_moved: bool | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def check_drift(sources_path: Path, overlay_path: Path, *, client: httpx.Client) -> DriftReport:
    """The report for the wheel `sources_path`/`overlay_path` record, against upstream.

    Fails closed: every return names its status, and `current` is returned only after
    upstream's newest commit was fetched and matched. The match is the vendored
    commit, or `reviewed_through` when a maintainer has read a later commit and found
    that it changes nothing the wheel ships, as long as the version has not moved.
    """
    try:
        recorded = _read_recorded(sources_path, overlay_path)
    except DriftError as err:
        return DriftReport(
            status="unknown", recorded_commit="", recorded_version="", reason=str(err)
        )

    def report(status: str, **fields: object) -> DriftReport:
        return DriftReport(
            status=status,
            recorded_commit=recorded.commit,
            recorded_version=recorded.version,
            reviewed_through=recorded.reviewed_through,
            **fields,
        )

    try:
        upstream_commit = _latest_commit(
            client, recorded.repository, recorded.branch, recorded.path
        )
    except DriftError as err:
        return report("unknown", reason=str(err))
    if upstream_commit == recorded.commit:
        return report(
            "current",
            upstream_commit=upstream_commit,
            upstream_version=recorded.version,
            version_moved=False,
        )

    try:
        upstream_version = _pyproject_version(
            client, recorded.repository, recorded.path, upstream_commit
        )
    except DriftError as err:
        return report("unknown", upstream_commit=upstream_commit, reason=str(err))
    version_moved = upstream_version != recorded.version
    status = (
        "current"
        if upstream_commit == recorded.reviewed_through and not version_moved
        else "behind"
    )
    return report(
        status,
        upstream_commit=upstream_commit,
        upstream_version=upstream_version,
        version_moved=version_moved,
    )


def render_human(report: DriftReport) -> str:
    lines = ["eegprep-lean drift check"]
    lines.append(
        f"  recorded: commit {report.recorded_commit or '(unreadable)'}"
        f" (version {report.recorded_version or '(unreadable)'})"
    )
    if report.reviewed_through:
        lines.append(f"  reviewed through: commit {report.reviewed_through}")
    if report.status == "unknown":
        if report.upstream_commit:
            lines.append(f"  upstream: commit {report.upstream_commit}")
        lines.append(f"  status: unknown -- {report.reason}")
        lines.append("  unknown is not current: this needs a look, not a shrug.")
    elif report.status == "current":
        lines.append(
            f"  upstream: commit {report.upstream_commit} (version {report.upstream_version})"
        )
        lines.append("  status: current")
    else:
        lines.append(
            f"  upstream: commit {report.upstream_commit} (version {report.upstream_version})"
        )
        moved = (
            f"version moved {report.recorded_version} -> {report.upstream_version}"
            if report.version_moved
            else f"version unchanged at {report.recorded_version}"
        )
        lines.append(f"  status: behind (commit moved; {moved})")
        if not report.version_moved:
            lines.append(
                "  upstream changed the package without a new version. If the change "
                "leaves the wheel as it is, set reviewed_through in sources.toml to "
                f"{report.upstream_commit}; if not, a re-vendor needs a new upstream "
                "version first, since a wheel's name is its identity."
            )
        lines.append(f"  refresh procedure: {REFRESH_PROCEDURE}")
    return "\n".join(lines)


def render_machine(report: DriftReport) -> str:
    def fmt(value: object) -> str:
        if value is None:
            return "-"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return " ".join(f"{key}={fmt(value)}" for key, value in report.as_dict().items())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the vendored eegprep-lean wheel against upstream develop."
    )
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES, help="sources.toml path")
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY, help="lock overlay path")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead")
    args = parser.parse_args(argv)

    try:
        with httpx.Client() as client:
            report = check_drift(args.sources, args.overlay, client=client)
    except Exception as err:  # noqa: BLE001 - a crash is an unknown, never a verdict
        report = DriftReport(
            status="unknown",
            recorded_commit="",
            recorded_version="",
            reason=f"the watcher itself failed: {type(err).__name__}: {err}",
        )

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(render_human(report))
        print(render_machine(report))

    return EXIT_CODES[report.status]


if __name__ == "__main__":
    sys.exit(main())
