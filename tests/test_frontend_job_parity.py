"""The `frontend-tests` job is written twice.

`test.yml` and `tests.yml` each carry a full copy of the frontend job -- every
Bun suite, the bundle staleness check, `chrome.js` -- because `tests.yml` is
what branch protection actually gates (`ci_cd.md`, "Required status checks")
while `test.yml` additionally runs on `main` with an extra Python version.
Nothing enforces that the two copies stay identical, and "keep these in sync"
comments do not keep anything in sync (`.rules/self_improve.md`): a suite
added to one file is silently missing from the other unless something reads
both and compares them. This is that something.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
JOB_NAME = "frontend-tests"


def _frontend_job_steps(workflow_file: str) -> list[dict[str, Any]]:
    workflow = yaml.safe_load((WORKFLOWS / workflow_file).read_text())
    jobs = workflow["jobs"]
    assert JOB_NAME in jobs, f"{workflow_file} no longer has a {JOB_NAME!r} job"
    steps = jobs[JOB_NAME]["steps"]
    assert steps, f"{workflow_file}'s {JOB_NAME!r} job has no steps to compare"
    return steps


def _step_names(steps: list[dict[str, Any]]) -> list[str]:
    """A step's `name`, or its `run`/`uses` line when it has none, so a
    mismatch is named even for the rare unnamed step."""
    return [
        step.get("name") or step.get("run") or step.get("uses") or "<unnamed step>"
        for step in steps
    ]


def test_frontend_tests_job_is_identical_in_both_workflows() -> None:
    steps_a = _frontend_job_steps("tests.yml")
    steps_b = _frontend_job_steps("test.yml")

    names_a = _step_names(steps_a)
    names_b = _step_names(steps_b)
    assert names_a == names_b, (
        "the frontend-tests step SEQUENCE has diverged between tests.yml and "
        f"test.yml.\n  tests.yml: {names_a}\n  test.yml:  {names_b}"
    )

    for name, step_a, step_b in zip(names_a, steps_a, steps_b, strict=True):
        assert step_a == step_b, (
            f"the frontend-tests step {name!r} differs between tests.yml and "
            f"test.yml.\n  tests.yml: {step_a}\n  test.yml:  {step_b}"
        )
