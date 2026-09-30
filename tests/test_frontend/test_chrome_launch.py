"""The browser harness launches Chrome again once when it reports no DevTools endpoint.

A release PR's run failed with "Chrome did not report a DevTools endpoint": a shared
runner had not started Chrome inside the harness's 30 seconds. `launch` in
frontend/browser-harness/chrome.js now kills that Chrome and launches another, in a
fresh profile, with a longer wait, before the run fails.

The real `launch` runs here, against a stand-in executable that behaves as Chrome does
when it is slow: it prints nothing on its first start and the endpoint line on its second.
The stand-in is a shell script, since `launch` only needs an executable that prints the
line on stderr; nothing of the widget or of a real browser is replaced.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "chrome_launch_fixture.js"

# Every start is logged; one that finds FAKE_CHROME_SILENT_STARTS lines already in the
# log (or more) reports the endpoint, and an earlier one stays silent.
FAKE_CHROME = """#!/bin/sh
echo "$@" >> "$FAKE_CHROME_LOG"
started=$(wc -l < "$FAKE_CHROME_LOG" | tr -d ' ')
if [ "$started" -le "$FAKE_CHROME_SILENT_STARTS" ]; then
  exec sleep 30
fi
echo "DevTools listening on ws://127.0.0.1:9/devtools/browser/fake" >&2
exec sleep 30
"""


def _launch(
    tmp_path: Path, silent_starts: int, waits_ms: tuple[int, ...]
) -> tuple[dict, list[str]]:
    """Run `launch` against a stand-in that stays silent for its first `silent_starts` starts."""
    fake = tmp_path / "fake-chrome"
    fake.write_text(FAKE_CHROME)
    fake.chmod(0o755)
    log = tmp_path / "starts.log"
    log.write_text("")
    profile = tmp_path / "profile"
    result = subprocess.run(
        ["bun", str(FIXTURE), str(fake), str(profile), *map(str, waits_ms)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": os.environ["PATH"],
            "FAKE_CHROME_LOG": str(log),
            "FAKE_CHROME_SILENT_STARTS": str(silent_starts),
        },
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    return outcome, log.read_text().splitlines()


def test_a_chrome_that_reports_at_once_is_launched_once(tmp_path: Path) -> None:
    outcome, starts = _launch(tmp_path, silent_starts=0, waits_ms=(2000, 4000))
    assert outcome["ok"] is True
    assert outcome["wsUrl"].startswith("ws://127.0.0.1:9/")
    assert len(starts) == 1


def test_a_chrome_that_reports_nothing_the_first_time_is_launched_again(tmp_path: Path) -> None:
    outcome, starts = _launch(tmp_path, silent_starts=1, waits_ms=(400, 4000))
    assert outcome["ok"] is True, outcome
    assert outcome["wsUrl"].startswith("ws://127.0.0.1:9/")
    assert len(starts) == 2


def test_the_second_launch_gets_a_profile_of_its_own(tmp_path: Path) -> None:
    _, starts = _launch(tmp_path, silent_starts=1, waits_ms=(400, 4000))
    profiles = [
        next(a for a in line.split() if a.startswith("--user-data-dir=")) for line in starts
    ]
    assert len(set(profiles)) == 2, profiles


def test_a_chrome_that_never_reports_fails_after_the_second_launch(tmp_path: Path) -> None:
    outcome, starts = _launch(tmp_path, silent_starts=99, waits_ms=(300, 600))
    assert outcome["ok"] is False
    assert "did not report a DevTools endpoint" in outcome["message"]
    assert len(starts) == 2


@pytest.mark.parametrize("waits_ms", [(300,), (300, 600)])
def test_the_retry_does_not_add_launches_beyond_the_waits_given(
    tmp_path: Path, waits_ms: tuple[int, ...]
) -> None:
    _, starts = _launch(tmp_path, silent_starts=99, waits_ms=waits_ms)
    assert len(starts) == len(waits_ms)
