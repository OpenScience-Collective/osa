"""The `latest` container tag must never point at a pre-release.

Production (`deploy/auto-update.sh`) pulls `ghcr.io/.../osa:latest` on an hourly
timer, so whatever moves that tag is deployed. `docker-build.yml` subscribes to
the `release` event's `published` type, which GitHub fires for pre-releases as
well as stable ones -- so the guard against shipping a release candidate to
production lives entirely in one `enable=` expression (issue #379).

These tests read that expression OUT OF the workflow file and evaluate it, rather
than restating it here. Rewriting the expression in the workflow therefore
re-runs these cases against the new version instead of silently diverging from a
copy. The evaluator understands only the operators the expression is allowed to
use and refuses anything else, so a change that reaches for a new construct fails
loudly rather than being quietly mis-evaluated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "docker-build.yml"


def latest_tag_expression() -> str:
    """The `${{ ... }}` body of `enable=` on the `type=raw,value=latest` line."""
    for line in WORKFLOW.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("type=raw,value=latest"):
            match = re.search(r"enable=\$\{\{(.+)\}\}\s*$", stripped)
            assert match, f"no enable= expression found on: {stripped}"
            return match.group(1).strip()
    raise AssertionError("no `type=raw,value=latest` tag line in docker-build.yml")


class Context:
    """The slice of the `github` context the expression is allowed to read."""

    def __init__(self, event_name: str, ref: str, prerelease: bool | None):
        self.event_name = event_name
        self.ref = ref
        self.prerelease = prerelease


def evaluate(expression: str, ctx: Context) -> bool:
    """Evaluate a GitHub Actions expression restricted to the operators in use.

    Translates to Python rather than interpreting, which keeps this short; the
    substitutions below are exact strings, so an expression that reaches for
    anything else survives into the final parse and raises.
    """
    prerelease = "None" if ctx.prerelease is None else str(ctx.prerelease)
    python = expression
    replacements = {
        "github.event_name": repr(ctx.event_name),
        "github.event.release.prerelease": prerelease,
        "github.ref": repr(ctx.ref),
        "&&": "and",
        "||": "or",
        "!contains": "not contains",
        "!startsWith": "not startsWith",
        "true": "True",
        "false": "False",
    }
    for old, new in replacements.items():
        python = python.replace(old, new)

    allowed = {
        "startsWith": lambda haystack, needle: haystack.startswith(needle),
        "contains": lambda haystack, needle: needle in haystack,
        "True": True,
        "False": False,
        "None": None,
    }
    # Any identifier left over is something this evaluator does not model. String
    # literals are stripped first: after substitution the expression contains
    # quoted refs like 'refs/heads/main', whose words are data, not identifiers.
    without_literals = re.sub(r"'[^']*'", "''", python)
    leftover = (
        set(re.findall(r"[A-Za-z_][A-Za-z_0-9.]*", without_literals))
        - set(allowed)
        - {"and", "or", "not"}
    )
    assert not leftover, (
        f"expression uses constructs this test does not model: {sorted(leftover)}. "
        "Extend the evaluator deliberately rather than loosening the assertion."
    )
    return bool(eval(python, {"__builtins__": {}}, allowed))  # noqa: S307 - restricted namespace


# name, event_name, ref, prerelease, latest-should-move
CASES = [
    ("push to main does not move latest", "push", "refs/heads/main", None, False),
    ("push to develop does not move latest", "push", "refs/heads/develop", None, False),
    ("a hand-pushed stable tag moves latest", "push", "refs/tags/v1.2.3", None, True),
    ("a hand-pushed rc tag does not", "push", "refs/tags/v1.2.3-rc1", None, False),
    ("a stable release moves latest", "release", "refs/tags/v1.2.3", False, True),
    ("a pre-release does not", "release", "refs/tags/v1.2.3-rc1", True, False),
    # The one a naive `startsWith`/`!contains` test waves through: the payload
    # says pre-release, the tag name gives no hint, and production is downstream.
    (
        "a pre-release with no hyphen in its tag does not",
        "release",
        "refs/tags/v1.2.3",
        True,
        False,
    ),
    (
        "dispatch against a tag republishes latest",
        "workflow_dispatch",
        "refs/tags/v0.8.9",
        None,
        True,
    ),
    ("dispatch against a branch does not", "workflow_dispatch", "refs/heads/main", None, False),
    ("a pull request never does", "pull_request", "refs/pull/42/merge", None, False),
]


@pytest.mark.parametrize(
    ("name", "event_name", "ref", "prerelease", "expected"),
    CASES,
    ids=[case[0] for case in CASES],
)
def test_latest_tag_policy(name, event_name, ref, prerelease, expected):
    result = evaluate(latest_tag_expression(), Context(event_name, ref, prerelease))
    assert result is expected, name


def test_workflow_subscribes_to_published_releases():
    """The reason the pre-release guard has to exist at all.

    If this ever narrows to `released`, GitHub stops delivering pre-releases here
    and the guard becomes belt-and-braces -- but until then it is the only thing
    standing between a release candidate and production.
    """
    text = WORKFLOW.read_text()
    assert "types: [published]" in text


def test_moving_tag_builds_share_one_concurrency_group():
    """Two builds that can both write `:latest` must queue, not race.

    The v0.8.9 incident: two commits landed on `main` ten seconds apart, both
    builds ran, both wrote `:latest`, and the older commit's image won because it
    finished last. The fix is a `concurrency.group` shared by every event that can
    move a moving tag, keyed on `github.workflow` (constant) plus a fixed literal
    for all tag/release builds and `github.ref` for branch builds -- so two tag
    pushes land in the *same* group instead of one group per ref, which would just
    narrow the race instead of closing it.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text())
    concurrency = workflow.get("concurrency")
    assert concurrency, "docker-build.yml must define top-level `concurrency`"

    group = concurrency.get("group", "")
    assert "github.workflow" in group, "group must key on the workflow name"
    assert "refs/tags/" in group, "group must collapse every tag/release build into one bucket"
    assert "github.ref" in group, "branch builds must still get a per-ref group"


def test_moving_tag_builds_are_not_cancelled_mid_push():
    """Only pull_request runs may cancel in progress; every push/tag/release run must queue.

    `build-and-test` alone (the pull_request path) never touches the registry, so
    cancelling a stale run just wastes a runner. Every other event can be mid-push
    to the registry when a new run starts, so cancelling it there -- rather than
    queuing behind it -- is exactly the race this concurrency group exists to close.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text())
    cancel_in_progress = workflow["concurrency"].get("cancel-in-progress", "")
    assert "github.event_name" in cancel_in_progress
    assert "pull_request" in cancel_in_progress
