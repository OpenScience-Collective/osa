"""The plan card's /chat/resume payload, read by the server's own models (E2.3, #662).

frontend/test-widget-plan-card.js holds the card's behavior and the exact shape of the body
it posts. This adds what only the server's code can say: the body the widget builds is a
valid `ResumeRequest` whose `result` parses as a `PlanDecision` (so it is not mistaken for a
code result, which `chat_resume` would refuse with a 409), and `build_plan_tool_message`
turns it into the message the model reads.

`build_plan_payload_fixture.js` drives the REAL widget (the runtime bundle as built, in
happy-dom) through the card, with the clicks of each scenario, and writes the bodies it
posted; nothing in the fixture builds a payload on its own. The server's side of the
conversation there is the recorded `propose_plan` tool_request of
`frontend/test-support/plan-widget-harness.js`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from src.api.routers.community import ResumeRequest
from src.api.tool_results import (
    MAX_PLAN_FEEDBACK_CHARS,
    ClientToolResult,
    PlanDecision,
    build_plan_tool_message,
)
from src.core.config.community import PROPOSE_PLAN_TOOL_NAME
from src.tools.client_tools import (
    MAX_PLAN_FINDINGS_CHARS,
    MAX_PLAN_PEAK_MB,
    MAX_PLAN_STEP_DETAIL_CHARS,
    MAX_PLAN_STEP_TITLE_CHARS,
    MAX_PLAN_STEPS,
    ProposePlanArgs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_SCRIPT = Path(__file__).resolve().parent / "build_plan_payload_fixture.js"

SCENARIOS = [
    "approved_edited",
    "approved_run_all",
    "changes_with_feedback",
    "changes_without_feedback",
    "changes_largest",
    "changes_astral",
    "approved_with_page_context",
]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Drive the real widget once per scenario and hand every test what it posted."""
    out = tmp_path_factory.mktemp("osa-plan-payload") / "built.json"
    result = subprocess.run(
        ["bun", str(FIXTURE_SCRIPT), str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"driving the widget failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_limits_the_widget_holds_are_the_servers(built: dict[str, Any]) -> None:
    """The card's bounds (osa-plan.js PLAN_LIMITS) are the server's, so a decision it sends fits."""
    assert built["limits"] == {
        "steps": MAX_PLAN_STEPS,
        "title": MAX_PLAN_STEP_TITLE_CHARS,
        "detail": MAX_PLAN_STEP_DETAIL_CHARS,
        "findings": MAX_PLAN_FINDINGS_CHARS,
        "feedback": MAX_PLAN_FEEDBACK_CHARS,
        "peakMb": MAX_PLAN_PEAK_MB,
    }


@pytest.mark.parametrize("name", SCENARIOS)
def test_the_body_is_a_resume_request_carrying_a_plan_decision(
    built: dict[str, Any], name: str
) -> None:
    """`extra="forbid"` on both models means any field the widget invented would fail here."""
    body = built[name]["body"]
    request = ResumeRequest.model_validate(body)
    assert isinstance(request.result, PlanDecision)
    assert not isinstance(request.result, ClientToolResult)
    assert request.session_id == body["session_id"]
    assert request.result.call_id == "plan-1"
    assert PROPOSE_PLAN_TOOL_NAME in request.client_tools


@pytest.mark.parametrize("name", SCENARIOS)
def test_the_proposal_the_server_validates_is_the_one_the_widget_answers(
    built: dict[str, Any], name: str
) -> None:
    """The recorded `propose_plan` arguments are valid, so the decision answers a real call."""
    ProposePlanArgs.model_validate(built[name]["proposed"])


def decision_of(built: dict[str, Any], name: str) -> PlanDecision:
    result = ResumeRequest.model_validate(built[name]["body"]).result
    assert isinstance(result, PlanDecision)
    return result


def message_of(built: dict[str, Any], name: str) -> ToolMessage:
    return build_plan_tool_message(decision_of(built, name), built[name]["proposed"])


def test_edited_steps_reach_the_decision_and_are_marked_different(built: dict[str, Any]) -> None:
    decision = decision_of(built, "approved_edited")
    assert decision.decision == "approved"
    assert decision.run_mode == "pause"
    assert [step.title for step in decision.steps] == [
        "Count the face onsets",
        "Average per condition",
        "Summarize",
    ]
    assert decision.steps[0].detail == "Per condition, from the events file."
    message = message_of(built, "approved_edited")
    assert message.tool_call_id == "plan-1"
    assert message.name == PROPOSE_PLAN_TOOL_NAME
    assert "Plan decision: approved" in message.content
    assert "DIFFERENT from what you proposed" in message.content
    assert "1. Count the face onsets: Per condition, from the events file." in message.content
    assert "3. Summarize: Say what the N170 shows." in message.content
    assert "pause" in message.content


def test_a_plan_left_as_proposed_is_marked_the_same(built: dict[str, Any]) -> None:
    """Whitespace in a title is collapsed on both sides, so an untouched plan compares equal."""
    decision = decision_of(built, "approved_run_all")
    assert decision.run_mode == "run_all"
    message = message_of(built, "approved_run_all")
    assert "the same as you proposed" in message.content
    assert "run_all" in message.content
    assert "Next: run exactly one execute_code per step" in message.content


def test_changes_requested_carries_the_note_and_runs_no_code(built: dict[str, Any]) -> None:
    decision = decision_of(built, "changes_with_feedback")
    assert decision.decision == "changes_requested"
    assert decision.feedback == "Use sub-003 and leave out the EOG channels."
    message = message_of(built, "changes_with_feedback")
    assert "Plan decision: changes_requested" in message.content
    assert "Reader feedback: Use sub-003 and leave out the EOG channels." in message.content
    assert "run no code" in message.content


def test_changes_requested_without_a_note_has_no_feedback(built: dict[str, Any]) -> None:
    assert "feedback" not in built["changes_without_feedback"]["body"]["result"]
    assert decision_of(built, "changes_without_feedback").feedback == ""
    assert "Reader feedback" not in message_of(built, "changes_without_feedback").content


def test_the_largest_plan_and_note_still_fit(built: dict[str, Any]) -> None:
    """Eight steps at the title and detail limits, and a note cut to 1000 characters."""
    decision = decision_of(built, "changes_largest")
    assert len(decision.steps) == MAX_PLAN_STEPS
    assert all(len(step.title) == MAX_PLAN_STEP_TITLE_CHARS for step in decision.steps)
    assert all(len(step.detail) == MAX_PLAN_STEP_DETAIL_CHARS for step in decision.steps)
    assert len(decision.feedback) == MAX_PLAN_FEEDBACK_CHARS
    assert "the same as you proposed" in message_of(built, "changes_largest").content


def test_the_shared_page_context_does_not_break_the_decision(built: dict[str, Any]) -> None:
    body = built["approved_with_page_context"]["body"]
    assert "page_context" in body
    request = ResumeRequest.model_validate(body)
    assert request.page_context is not None
    assert isinstance(request.result, PlanDecision)


def test_characters_beyond_the_basic_plane_are_counted_as_the_server_counts_them(
    built: dict[str, Any],
) -> None:
    """The limits are code points (pydantic max_length), not UTF-16 code units.

    A title of 120 code points is two code units per emoji; cutting it by code units would
    drop half of it, or split a surrogate pair that the server's JSON parser refuses (a 422).
    """
    emoji = "\U0001f600"
    decision = decision_of(built, "changes_astral")
    (step,) = decision.steps
    assert step.title == "a" + emoji * (MAX_PLAN_STEP_TITLE_CHARS - 1)
    assert step.detail == "a" + emoji * (MAX_PLAN_STEP_DETAIL_CHARS - 1)
    assert decision.feedback == "n" + emoji * (MAX_PLAN_FEEDBACK_CHARS - 1)
    for text in (step.title, step.detail, decision.feedback):
        text.encode("utf-8")  # a lone surrogate would raise here
