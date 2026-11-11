"""`propose_plan` through the real app: park, decide, resume.

`POST /{community}/chat` and `/chat/resume` are driven through the ASGI app, with the real
router, the real `create_community_assistant` (its real tool binding and prompt), the real
graph, the real session store and the real SSE assembly. The model is the only stand-in:
`create_anthropic_llm` is patched to hand out a `StreamingScriptedChatModel` per offered
model, the division `.rules/testing_guidelines.md` draws. The harness is the one
`test_model_routing.py` uses.

The widget does not declare `propose_plan` yet (the plan card is plan item E2.3), so every
test here declares it explicitly in `client_tools`, which is what a widget that can answer
it will do.
"""

import json
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk, ToolMessage

import src.api.routers.community as community_module
from src.api.routers.community import (
    ChatSession,
    _get_session_store,
    create_community_router,
)
from src.assistants.registry import registry
from src.core.config.community import (
    PROPOSE_PLAN_TOOL_NAME,
    ClientToolConfig,
    CommunityConfig,
    ExtensionsConfig,
    PythonRuntimeConfig,
    RuntimeConfig,
)
from src.core.limits import MAX_BROWSER_RUNS_PER_REPLY
from src.core.services.anthropic_models import HAIKU, SONNET
from src.tools.client_tools import CLIENT_TOOL_KILL_SWITCH_ENV
from tests.helpers.chat_models import StreamingScriptedChatModel
from tests.helpers.provider_replies import text_chunk

COMMUNITY = "plantest"
ORIGIN = "https://plan.example"
BYOK = {"X-Anthropic-API-Key": "sk-ant-plan-test-key"}
EXECUTE_CODE = "execute_code"
PROPOSE_PLAN = PROPOSE_PLAN_TOOL_NAME
DECLARED = [EXECUTE_CODE, "get_full_output", PROPOSE_PLAN]
ANALYSIS_REQUEST = "plot the ERP for famous faces"
PLAN_TEXT = "PLAN-FIRST-MARKER: call propose_plan before any execute_code."

PLAN_ARGS = {
    "findings": "18 participants with 3 runs each; 2 event types.",
    "steps": [
        {"title": "Load epochs", "detail": "Load run 1 and epoch around the stimulus."},
        {"title": "Average", "detail": "Average the epochs per condition."},
        {"title": "Plot", "detail": "Plot the ERP for each condition."},
    ],
    "est_peak_mb": 400,
}
EDITED_STEPS = [
    {"title": "Load epochs", "detail": "Load runs 1 and 2 and epoch around the stimulus."},
    {"title": "Plot", "detail": "Plot the grand average ERP."},
]


def _call_id(n: int) -> str:
    return f"toolu_01plan{n:018d}"


def _config() -> CommunityConfig:
    return CommunityConfig(
        id=COMMUNITY,
        name="Plan Test",
        description="Drives propose_plan",
        extensions=ExtensionsConfig(
            client_tools=[
                ClientToolConfig(name=EXECUTE_CODE, runtime="python", description="Run code.")
            ]
        ),
        runtime=RuntimeConfig(
            python=PythonRuntimeConfig(pyodide_version="314.0.6", lockfile="runtime/l.json")
        ),
        cors_origins=[ORIGIN],
        quick_model="haiku",
        analysis_model="sonnet",
        plan_first_instructions=PLAN_TEXT,
    )


def say(text: str) -> list[AIMessageChunk]:
    return [text_chunk(text)]


def call(name: str, args: dict, call_id: str) -> list[AIMessageChunk]:
    """One model run that calls `name`, streamed as a provider streams a call."""
    return [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": name, "args": json.dumps(args), "id": call_id, "index": 0}],
        )
    ]


def plan(call_id: str, **overrides) -> list[AIMessageChunk]:
    return call(PROPOSE_PLAN, {**PLAN_ARGS, **overrides}, call_id)


def run_code(call_id: str) -> list[AIMessageChunk]:
    return call(EXECUTE_CODE, {"code": "print(1)", "description": "Print one."}, call_id)


@dataclass
class Rig:
    analysis: StreamingScriptedChatModel
    quick: StreamingScriptedChatModel
    assistants: list = field(default_factory=list)

    @property
    def prompts(self) -> list[str]:
        return [awm.assistant.system_prompt for awm in self.assistants]


@pytest.fixture(autouse=True)
def _community():
    registry.register_from_config(_config())
    _get_session_store(COMMUNITY).clear()
    yield
    registry._assistants.pop(COMMUNITY, None)
    _get_session_store(COMMUNITY).clear()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_community_router(COMMUNITY))
    return TestClient(app)


def _rig(monkeypatch, script: list[list[AIMessageChunk]]) -> Rig:
    analysis = StreamingScriptedChatModel(chunk_script=script)
    quick = StreamingScriptedChatModel(chunk_script=[say("unused")])
    by_model = {HAIKU: quick, SONNET: analysis}
    monkeypatch.setattr(
        community_module, "create_anthropic_llm", lambda *, model, **_kw: by_model[model]
    )
    rig = Rig(analysis=analysis, quick=quick)
    real_create = community_module.create_community_assistant

    def spy(*args, **kwargs):
        awm = real_create(*args, **kwargs)
        rig.assistants.append(awm)
        return awm

    monkeypatch.setattr(community_module, "create_community_assistant", spy)
    return rig


def _events(response) -> list[dict]:
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _chat(client, *, declared=DECLARED, message=ANALYSIS_REQUEST) -> list[dict]:
    response = client.post(
        f"/{COMMUNITY}/chat",
        json={"message": message, "client_tools": declared},
        headers={"Origin": ORIGIN, **BYOK},
    )
    assert response.status_code == 200, response.text
    return _events(response)


def _chat_in(client, session_id: str, message: str, *, declared=DECLARED) -> list[dict]:
    """A follow-up message in an existing session."""
    response = client.post(
        f"/{COMMUNITY}/chat",
        json={"message": message, "client_tools": declared, "session_id": session_id},
        headers={"Origin": ORIGIN, **BYOK},
    )
    assert response.status_code == 200, response.text
    return _events(response)


def _resume_response(client, session_id: str, result: dict, *, declared=DECLARED):
    return client.post(
        f"/{COMMUNITY}/chat/resume",
        json={"session_id": session_id, "result": result, "client_tools": declared},
        headers={"Origin": ORIGIN, **BYOK},
    )


def _resume(client, session_id: str, result: dict) -> list[dict]:
    response = _resume_response(client, session_id, result)
    assert response.status_code == 200, response.text
    return _events(response)


def _request(events: list[dict]) -> dict:
    return next(e for e in events if e["event"] == "tool_request")


def _decision(call_id: str, **overrides) -> dict:
    return {
        "call_id": call_id,
        "decision": "approved",
        "steps": EDITED_STEPS,
        "run_mode": "run_all",
        **overrides,
    }


def _code_result(call_id: str) -> dict:
    return {"call_id": call_id, "status": "ok", "summary": "printed 1"}


def _parked_session(client, monkeypatch, script=None):
    """Run 1 of an analysis turn that proposes a plan; returns the rig and its tool_request."""
    rig = _rig(monkeypatch, script or [plan(_call_id(1)), say("Step 1 done.")])
    events = _chat(client)
    return rig, _request(events)


class TestProposingAPlan:
    def test_an_analysis_turn_emits_a_tool_request_carrying_the_plan(
        self, client, monkeypatch
    ) -> None:
        _rig(monkeypatch, [plan(_call_id(1))])

        events = _chat(client)

        request = _request(events)
        assert request["tool"] == PROPOSE_PLAN
        assert request["call_id"] == _call_id(1)
        assert request["args"] == PLAN_ARGS
        assert request["requires_permission"] is False
        assert not any(e["event"] == "done" for e in events)

    def test_the_call_is_parked_and_runs_no_code(self, client, monkeypatch) -> None:
        _, request = _parked_session(client, monkeypatch)

        session = _get_session_store(COMMUNITY)[request["session_id"]]

        assert session.pending_call is not None
        assert session.pending_call.tool == PROPOSE_PLAN
        assert session.pending_call.runs_code is False

    def test_the_tool_is_bound_when_declared_and_only_then(self, client, monkeypatch) -> None:
        declared_rig = _rig(monkeypatch, [say("ok")])
        _chat(client)
        assert PROPOSE_PLAN in declared_rig.analysis.bound_tool_names

        _get_session_store(COMMUNITY).clear()
        undeclared_rig = _rig(monkeypatch, [say("ok")])
        _chat(client, declared=[EXECUTE_CODE, "get_full_output"])

        assert PROPOSE_PLAN not in undeclared_rig.analysis.bound_tool_names
        assert EXECUTE_CODE in undeclared_rig.analysis.bound_tool_names

    def test_a_call_with_invalid_arguments_is_answered_not_parked(
        self, client, monkeypatch
    ) -> None:
        rig = _rig(monkeypatch, [plan(_call_id(1), steps=[]), say("Let me fix the plan.")])

        events = _chat(client)

        assert not any(e["event"] == "tool_request" for e in events)
        assert next(e for e in events if e["event"] == "done")
        error = next(m for m in rig.analysis.seen_message_lists[1] if isinstance(m, ToolMessage))
        assert error.status == "error"
        assert f"Invalid arguments for {PROPOSE_PLAN}" in error.content


class TestDecidingAPlan:
    def test_an_approval_with_edited_steps_and_a_mode_reaches_the_model_and_history(
        self, client, monkeypatch
    ) -> None:
        rig, request = _parked_session(client, monkeypatch)

        events = _resume(client, request["session_id"], _decision(request["call_id"]))

        assert (
            next(e for e in events if e["event"] == "done")["session_id"] == request["session_id"]
        )
        assert rig.analysis.calls == 2

        live = rig.analysis.seen_message_lists[1][-1]
        assert isinstance(live, ToolMessage)
        assert live.tool_call_id == request["call_id"]
        for step in EDITED_STEPS:
            assert step["detail"] in live.content
        assert "Plot the ERP for each condition." not in live.content
        assert "DIFFERENT from what you proposed" in live.content
        assert "run_all" in live.content

        session = _get_session_store(COMMUNITY)[request["session_id"]]
        stored = [m for m in session.messages if isinstance(m, ToolMessage)]
        assert [m.content for m in stored] == [live.content]
        assert session.pending_call is None

    def test_an_unedited_approval_is_reported_as_the_same_plan(self, client, monkeypatch) -> None:
        rig, request = _parked_session(client, monkeypatch)

        _resume(
            client,
            request["session_id"],
            _decision(request["call_id"], steps=PLAN_ARGS["steps"], run_mode="pause"),
        )

        live = rig.analysis.seen_message_lists[1][-1]
        assert "the same as you proposed" in live.content
        assert "pause" in live.content

    def test_a_second_resume_of_the_same_plan_is_a_409(self, client, monkeypatch) -> None:
        _, request = _parked_session(client, monkeypatch)
        _resume(client, request["session_id"], _decision(request["call_id"]))

        again = _resume_response(client, request["session_id"], _decision(request["call_id"]))

        assert again.status_code == 409

    def test_changes_requested_leads_to_a_second_plan(self, client, monkeypatch) -> None:
        revised = {"steps": EDITED_STEPS}
        rig, request = _parked_session(
            client, monkeypatch, [plan(_call_id(1)), plan(_call_id(2), **revised)]
        )

        events = _resume(
            client,
            request["session_id"],
            _decision(request["call_id"], decision="changes_requested", feedback="Use run 2."),
        )

        second = _request(events)
        assert second["tool"] == PROPOSE_PLAN
        assert second["call_id"] == _call_id(2)
        assert second["args"]["steps"] == EDITED_STEPS
        live = rig.analysis.seen_message_lists[1][-1]
        assert "Reader feedback: Use run 2." in live.content
        assert "run no code" in live.content
        session = _get_session_store(COMMUNITY)[request["session_id"]]
        assert session.pending_call.call_id == _call_id(2)

    def test_the_decision_is_validated(self, client, monkeypatch) -> None:
        _, request = _parked_session(client, monkeypatch)

        for bad in (
            _decision(request["call_id"], steps=[]),
            _decision(request["call_id"], run_mode="sprint"),
            _decision(request["call_id"], feedback="x" * 1001),
            _decision(request["call_id"], decision="shrug"),
        ):
            assert _resume_response(client, request["session_id"], bad).status_code == 422

        session = _get_session_store(COMMUNITY)[request["session_id"]]
        assert session.pending_call is not None


class TestTheAnswerMustMatchTheCall:
    def test_a_code_result_for_a_plan_is_a_409_and_the_plan_stays_parked(
        self, client, monkeypatch
    ) -> None:
        rig, request = _parked_session(client, monkeypatch)

        wrong = _resume_response(client, request["session_id"], _code_result(request["call_id"]))

        assert wrong.status_code == 409
        session = _get_session_store(COMMUNITY)[request["session_id"]]
        assert session.pending_call is not None
        assert session.pending_call.call_id == request["call_id"]
        assert rig.analysis.calls == 1
        assert not any(isinstance(m, ToolMessage) for m in session.messages)
        # And the right answer still works.
        assert (
            _resume_response(
                client, request["session_id"], _decision(request["call_id"])
            ).status_code
            == 200
        )

    def test_a_decision_for_a_code_call_is_a_409_and_the_call_stays_parked(
        self, client, monkeypatch
    ) -> None:
        rig, request = _parked_session(client, monkeypatch, [run_code(_call_id(1)), say("Done.")])
        assert request["tool"] == EXECUTE_CODE

        wrong = _resume_response(client, request["session_id"], _decision(request["call_id"]))

        assert wrong.status_code == 409
        session = _get_session_store(COMMUNITY)[request["session_id"]]
        assert session.pending_call is not None
        assert rig.analysis.calls == 1
        assert (
            _resume_response(
                client, request["session_id"], _code_result(request["call_id"])
            ).status_code
            == 200
        )


class TestALeftPlan:
    def test_typing_instead_of_deciding_closes_the_plan_in_its_own_words(
        self, client, monkeypatch
    ) -> None:
        rig, request = _parked_session(
            client, monkeypatch, [plan(_call_id(1)), say("What should I look at instead?")]
        )

        events = _chat_in(client, request["session_id"], "Actually, just list the events.")

        assert next(e for e in events if e["event"] == "done")
        answer = next(m for m in rig.analysis.seen_message_lists[1] if isinstance(m, ToolMessage))
        assert answer.tool_call_id == request["call_id"]
        text = answer.content[0]["text"]
        assert "did not decide on the plan" in text
        assert "Run no code from it" in text
        assert "Browser execution" not in text
        session = _get_session_store(COMMUNITY)[request["session_id"]]
        assert session.pending_call is None


class TestPlansAndTheBrowserRunBudget:
    def test_twenty_plans_do_not_exhaust_the_budget_for_code(self, client, monkeypatch) -> None:
        rounds = MAX_BROWSER_RUNS_PER_REPLY
        script = [plan(_call_id(n)) for n in range(1, rounds + 2)] + [run_code(_call_id(99))]
        rig, request = _parked_session(client, monkeypatch, script)
        session_id = request["session_id"]

        for n in range(1, rounds + 1):
            events = _resume(
                client, session_id, _decision(_call_id(n), decision="changes_requested")
            )
            request = _request(events)
            assert request["tool"] == PROPOSE_PLAN, f"plan {n + 1} was refused"
            assert request["call_id"] == _call_id(n + 1)

        session = _get_session_store(COMMUNITY)[session_id]
        assert session.pending_call.runs_before == 0

        # The plan is finally approved, and code still runs.
        events = _resume(client, session_id, _decision(_call_id(rounds + 1)))
        request = _request(events)
        assert request["tool"] == EXECUTE_CODE
        assert request["call_id"] == _call_id(99)
        assert rig.analysis.calls == rounds + 2

    def test_a_plan_does_not_count_against_the_runs_around_it(self, client, monkeypatch) -> None:
        rig, request = _parked_session(
            client,
            monkeypatch,
            [run_code(_call_id(1)), plan(_call_id(2)), run_code(_call_id(3)), say("Done.")],
        )
        session_id = request["session_id"]
        session = _get_session_store(COMMUNITY)[session_id]

        request = _request(_resume(client, session_id, _code_result(_call_id(1))))
        assert (session.pending_call.runs_before, session.pending_call.code_runs_before) == (1, 1)

        request = _request(_resume(client, session_id, _decision(_call_id(2))))
        assert request["tool"] == EXECUTE_CODE
        assert (session.pending_call.runs_before, session.pending_call.code_runs_before) == (1, 1)
        assert rig.analysis.calls == 3


class TestThePlanFirstPrompt:
    """The community's plan-first text reaches the model exactly when it can follow it."""

    def test_an_analysis_turn_that_declares_the_tool_gets_it(self, client, monkeypatch) -> None:
        rig = _rig(monkeypatch, [say("ok")])

        _chat(client)

        assert PLAN_TEXT in rig.prompts[0]

    def test_an_undeclared_request_does_not(self, client, monkeypatch) -> None:
        rig = _rig(monkeypatch, [say("ok")])

        _chat(client, declared=[EXECUTE_CODE, "get_full_output"])

        assert PLAN_TEXT not in rig.prompts[0]

    def test_a_quick_turn_does_not(self, client, monkeypatch) -> None:
        rig = _rig(monkeypatch, [say("ok")])

        _chat(client, message="find datasets with face stimuli")

        assert rig.quick.calls == 1
        assert PLAN_TEXT not in rig.prompts[0]

    def test_the_kill_switch_withdraws_it(self, client, monkeypatch) -> None:
        monkeypatch.setenv(CLIENT_TOOL_KILL_SWITCH_ENV, "1")
        rig = _rig(monkeypatch, [say("ok")])

        _chat(client)

        assert PLAN_TEXT not in rig.prompts[0]

    def test_a_resume_keeps_it(self, client, monkeypatch) -> None:
        rig, request = _parked_session(client, monkeypatch)

        _resume(client, request["session_id"], _decision(request["call_id"]))

        assert len(rig.prompts) == 2
        assert all(PLAN_TEXT in prompt for prompt in rig.prompts)

    def test_a_quick_session_that_hands_over_gets_it_on_the_new_analysis_session(
        self, client, monkeypatch
    ) -> None:
        """How NEMAR reaches the analysis model in practice: a few quick turns, then a
        request that hands the conversation to a new session. The declared tools are
        carried through that hand-off, so the tool is bound and the text sent there."""
        earlier = ChatSession("sess-quick", COMMUNITY)
        earlier.add_user_message("find datasets with face stimuli")
        earlier.add_assistant_message("The closest match is on002718, 18 participants.")
        _get_session_store(COMMUNITY)[earlier.session_id] = earlier
        rig = _rig(monkeypatch, [say("ok")])

        events = _chat_in(client, "sess-quick", ANALYSIS_REQUEST)

        handover = next(e for e in events if e.get("stage") == "started")
        assert handover["session_id"] != "sess-quick"
        assert PROPOSE_PLAN in rig.analysis.bound_tool_names
        assert PLAN_TEXT in rig.prompts[-1]
        assert PLAN_TEXT not in rig.prompts[0], "the quick turn that wrote the summary got none"
