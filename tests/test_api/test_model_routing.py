"""Quick and analysis model routing, and the handover between them, through the real app.

`POST /{community}/chat` and `/chat/resume` are driven through the ASGI app, with the real
router, the real `create_community_assistant` (it builds the real assistant, its real tool
binding and its real prompt), the real graph, the real session store and the real SSE
assembly. The model is the only stand-in: `create_anthropic_llm` is patched to hand out a
`ScriptedChatModel` per offered model, which is the division `.rules/testing_guidelines.md`
draws. `create_community_assistant` itself is wrapped at the boundary
`test_chat_resume_endpoint.py` patches, so each test can read the tier and the
instructions it was called with, and then calls through.

The companion test closer to the wire is `tests/test_integration/test_model_routing_llm.py`,
which runs the same turns against the real models.
"""

import json
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage

import src.api.routers.community as community_module
from src.api.routers.community import (
    ChatSession,
    _get_session_store,
    create_community_router,
)
from src.api.tool_results import PendingClientCall
from src.assistants.registry import registry
from src.core.config.community import (
    ClientToolConfig,
    CommunityConfig,
    ExtensionsConfig,
    PythonRuntimeConfig,
    RuntimeConfig,
)
from src.core.services.anthropic_models import HAIKU, SONNET
from tests.helpers.chat_models import (
    StreamingScriptedChatModel,
    tool_call_response,
)
from tests.helpers.provider_replies import text_chunk

COMMUNITY = "routingtest"
PLAIN = "routingplain"
ORIGIN = "https://routing.example"
BYOK = {"X-Anthropic-API-Key": "sk-ant-routing-test-key"}
DATASET = "on002718"
EXECUTE_CODE = "execute_code"
EXECUTE_CALL_ID = "toolu_01cccccccccccccccccccccc"


def _config(community_id: str = COMMUNITY, *, routed: bool = True) -> CommunityConfig:
    routing = {"quick_model": "haiku", "analysis_model": "sonnet"} if routed else {}
    return CommunityConfig(
        id=community_id,
        name="Routing Test",
        description="Drives quick and analysis routing",
        extensions=ExtensionsConfig(
            client_tools=[
                ClientToolConfig(name="execute_code", runtime="python", description="Run code.")
            ]
        ),
        runtime=RuntimeConfig(
            python=PythonRuntimeConfig(pyodide_version="314.0.6", lockfile="runtime/l.json")
        ),
        cors_origins=[ORIGIN],
        **routing,
    )


def say(text: str) -> list[AIMessageChunk]:
    """One model run that streams `text`, as a provider's does."""
    return [text_chunk(text)]


def call_start_analysis(call_id: str = "toolu_01dddddddddddddddddddddd") -> list[AIMessageChunk]:
    """One model run that calls `start_analysis`, streamed as a provider streams a call."""
    return [
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "start_analysis",
                    "args": json.dumps({"reason": "needs a plot"}),
                    "id": call_id,
                    "index": 0,
                }
            ],
        )
    ]


@dataclass
class Rig:
    """The two models, and what `create_community_assistant` was called with."""

    quick: StreamingScriptedChatModel
    analysis: StreamingScriptedChatModel
    calls: list[dict] = field(default_factory=list)
    assistants: list = field(default_factory=list)

    @property
    def tiers(self) -> list:
        return [call.get("tier") for call in self.calls]


@pytest.fixture(autouse=True)
def _communities():
    for community_id, routed in ((COMMUNITY, True), (PLAIN, False)):
        registry.register_from_config(_config(community_id, routed=routed))
        _get_session_store(community_id).clear()
    yield
    for community_id in (COMMUNITY, PLAIN):
        registry._assistants.pop(community_id, None)
        _get_session_store(community_id).clear()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_community_router(COMMUNITY))
    app.include_router(create_community_router(PLAIN))
    return TestClient(app)


def _rig(monkeypatch, *, quick: list, analysis: list | None = None) -> Rig:
    rig = Rig(
        quick=StreamingScriptedChatModel(chunk_script=quick),
        analysis=StreamingScriptedChatModel(chunk_script=analysis or [say("Plotting it now.")]),
    )
    by_model = {HAIKU: rig.quick, SONNET: rig.analysis}
    real_create = community_module.create_community_assistant

    monkeypatch.setattr(
        community_module, "create_anthropic_llm", lambda *, model, **_kw: by_model[model]
    )

    def spy(*args, **kwargs):
        rig.calls.append(kwargs)
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


def _chat(client, message: str, *, community: str = COMMUNITY, session_id=None, **body) -> list:
    payload = {"message": message, "client_tools": [EXECUTE_CODE, "get_full_output"], **body}
    if session_id:
        payload["session_id"] = session_id
    response = client.post(f"/{community}/chat", json=payload, headers={"Origin": ORIGIN, **BYOK})
    assert response.status_code == 200, response.text
    return _events(response)


def _earlier_conversation(community: str = COMMUNITY) -> ChatSession:
    """A quick conversation about a dataset, as the store would hold it."""
    session = ChatSession("sess-quick", community)
    session.add_user_message("find datasets with face stimuli")
    session.add_assistant_message(f"The closest match is {DATASET}, 18 participants.")
    _get_session_store(community)[session.session_id] = session
    return session


def _names(events: list) -> list[str]:
    return [e["event"] for e in events]


def _stages(events: list) -> list[str]:
    return [e["stage"] for e in events if e["event"] == "handover"]


def _done(events: list) -> dict:
    return next(e for e in events if e["event"] == "done")


SUMMARY = f"The reader searched for face stimuli and chose {DATASET}, with 18 participants."


class TestAQuickTurn:
    def test_binds_no_execute_code_and_does_bind_start_analysis(self, client, monkeypatch) -> None:
        rig = _rig(monkeypatch, quick=[say("Hello.")])

        events = _chat(client, "find datasets with face stimuli")

        bound = rig.quick.bound_tool_names
        assert "start_analysis" in bound
        assert EXECUTE_CODE not in bound
        assert "get_full_output" not in bound
        assert rig.tiers == ["quick"]
        assert _stages(events) == []

    def test_runs_on_the_quick_model_and_says_so(self, client, monkeypatch) -> None:
        rig = _rig(monkeypatch, quick=[say("Hello.")])

        done = _done(_chat(client, "find datasets with face stimuli"))

        assert done["model"] == HAIKU
        assert done["mode"] == "quick"
        assert rig.analysis.calls == 0

    def test_the_prompt_tells_it_to_call_start_analysis(self, client, monkeypatch) -> None:
        rig = _rig(monkeypatch, quick=[say("Hello.")])

        _chat(client, "find datasets with face stimuli")

        prompt = rig.assistants[0].assistant.system_prompt
        assert "start_analysis" in prompt

    def test_an_analysis_turn_binds_execute_code_and_has_no_such_prompt(
        self, client, monkeypatch
    ) -> None:
        rig = _rig(monkeypatch, quick=[say("unused")])

        _chat(client, "plot the ERP for famous faces")

        assert EXECUTE_CODE in rig.analysis.bound_tool_names
        assert "start_analysis" not in rig.analysis.bound_tool_names
        assert "start_analysis" not in rig.assistants[0].assistant.system_prompt


class TestAKeywordHandover:
    def test_hands_over_with_events_in_order(self, client, monkeypatch) -> None:
        _earlier_conversation()
        rig = _rig(monkeypatch, quick=[say(SUMMARY)])

        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        assert _stages(events) == ["summarizing", "summarized", "started"]
        order = [i for i, e in enumerate(events) if e["event"] == "handover"]
        assert order == sorted(order)
        assert rig.tiers[0] == "quick"
        assert rig.tiers[-1] == "analysis"

    def test_summarized_carries_the_summary_and_its_size(self, client, monkeypatch) -> None:
        _earlier_conversation()
        _rig(monkeypatch, quick=[say(SUMMARY)])

        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        summarized = next(e for e in events if e.get("stage") == "summarized")
        assert summarized["summary"] == SUMMARY
        assert summarized["words"] == len(SUMMARY.split())
        assert summarized["messages"] == 2
        assert DATASET in summarized["summary"]

    def test_the_summary_is_limited_in_code(self, client, monkeypatch) -> None:
        _earlier_conversation()
        _rig(monkeypatch, quick=[say(" ".join(["word"] * 400))])

        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        summarized = next(e for e in events if e.get("stage") == "summarized")
        assert summarized["words"] <= 150
        assert len(summarized["summary"].split()) <= 150

    def test_the_new_sessions_first_call_sees_the_summary_and_the_message(
        self, client, monkeypatch
    ) -> None:
        _earlier_conversation()
        rig = _rig(monkeypatch, quick=[say(SUMMARY)])

        _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        first = rig.analysis.seen_message_lists[0]
        human = [m for m in first if isinstance(m, HumanMessage)]
        assert len(human) == 1, "the new session is seeded with exactly one message"
        assert DATASET in human[0].content
        assert "plot the ERP for famous faces" in human[0].content
        # And nothing of the old conversation beyond the summary.
        assert "closest match" not in human[0].content

    def test_started_names_the_new_session_and_the_analysis_model(
        self, client, monkeypatch
    ) -> None:
        _earlier_conversation()
        _rig(monkeypatch, quick=[say(SUMMARY)])

        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        started = next(e for e in events if e.get("stage") == "started")
        assert started["session_id"] != "sess-quick"
        assert started["model"] == SONNET
        sessions = [e["session_id"] for e in events if e["event"] == "session"]
        assert sessions == ["sess-quick", started["session_id"]]
        done = _done(events)
        assert done["session_id"] == started["session_id"]

    def test_done_carries_the_analysis_model_and_a_mode(self, client, monkeypatch) -> None:
        _earlier_conversation()
        _rig(monkeypatch, quick=[say(SUMMARY)])

        done = _done(_chat(client, "plot the ERP for famous faces", session_id="sess-quick"))

        assert done["model"] == SONNET
        assert done["mode"] == "analysis"

    def test_the_old_session_is_left_valid_and_quick(self, client, monkeypatch) -> None:
        old = _earlier_conversation()
        _rig(monkeypatch, quick=[say(SUMMARY)])

        _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        assert old.mode == "quick"
        assert old.pending_call is None
        assert not any(isinstance(m, ToolMessage) for m in old.messages)
        assert not any(getattr(m, "tool_calls", None) for m in old.messages)

    def test_the_session_stays_analysis_afterwards(self, client, monkeypatch) -> None:
        _earlier_conversation()
        rig = _rig(monkeypatch, quick=[say(SUMMARY)])
        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")
        new_id = _done(events)["session_id"]
        assert _get_session_store(COMMUNITY)[new_id].mode == "analysis"
        quick_calls_before = rig.quick.calls

        # No keyword: a question. It still goes to the analysis model, with no handover.
        followup = _chat(client, "which dataset were we discussing?", session_id=new_id)

        assert _stages(followup) == []
        assert _done(followup)["mode"] == "analysis"
        assert _done(followup)["session_id"] == new_id
        assert rig.quick.calls == quick_calls_before


class TestAFirstMessage:
    def test_a_keyword_first_message_flips_its_own_session(self, client, monkeypatch) -> None:
        rig = _rig(monkeypatch, quick=[say("unused")])

        events = _chat(client, "plot the ERP for famous faces", session_id="sess-first")

        assert _stages(events) == []
        done = _done(events)
        assert done["session_id"] == "sess-first"
        assert done["mode"] == "analysis"
        assert done["model"] == SONNET
        assert _get_session_store(COMMUNITY)["sess-first"].mode == "analysis"
        assert rig.quick.calls == 0


class TestStartAnalysis:
    def _call(self):
        return call_start_analysis()

    def test_a_start_analysis_call_hands_over(self, client, monkeypatch) -> None:
        _earlier_conversation()
        rig = _rig(monkeypatch, quick=[self._call(), say(SUMMARY)])

        # Not a keyword message: the quick model decides.
        events = _chat(client, "could you look at the alpha band instead?", session_id="sess-quick")

        assert _stages(events) == ["summarizing", "summarized", "started"]
        assert "tool_request" not in _names(events)
        done = _done(events)
        assert done["mode"] == "analysis"
        assert done["model"] == SONNET
        assert done["session_id"] != "sess-quick"
        assert rig.tiers == ["quick", "analysis"]

    def test_the_summary_is_written_after_the_call_and_reaches_the_analysis_model(
        self, client, monkeypatch
    ) -> None:
        _earlier_conversation()
        rig = _rig(monkeypatch, quick=[self._call(), say(SUMMARY)])

        _chat(client, "could you look at the alpha band instead?", session_id="sess-quick")

        human = [m for m in rig.analysis.seen_message_lists[0] if isinstance(m, HumanMessage)]
        assert DATASET in human[0].content
        assert "alpha band" in human[0].content

    def test_the_old_session_holds_no_dangling_call(self, client, monkeypatch) -> None:
        old = _earlier_conversation()
        _rig(monkeypatch, quick=[self._call(), say(SUMMARY)])

        _chat(client, "could you look at the alpha band instead?", session_id="sess-quick")

        assert old.pending_call is None
        assert not any(getattr(m, "tool_calls", None) for m in old.messages)
        assert old.mode == "quick"

    def test_on_a_first_message_the_same_session_runs_the_analysis(
        self, client, monkeypatch
    ) -> None:
        rig = _rig(monkeypatch, quick=[self._call()])

        events = _chat(client, "could you look at the alpha band instead?", session_id="s-1")

        assert _stages(events) == []
        done = _done(events)
        assert done["session_id"] == "s-1"
        assert done["mode"] == "analysis"
        assert _get_session_store(COMMUNITY)["s-1"].mode == "analysis"
        assert rig.tiers == ["quick", "analysis"]


def _parked_analysis_session(mode: str) -> ChatSession:
    session = ChatSession("sess-parked", COMMUNITY)
    session.mode = mode
    session.add_user_message("Plot the alpha power.")
    session.messages.append(
        tool_call_response(EXECUTE_CODE, {"code": "x", "description": "d"}, EXECUTE_CALL_ID)
    )
    session.set_pending_call(
        PendingClientCall.from_state(
            {
                "call_id": EXECUTE_CALL_ID,
                "tool": EXECUTE_CODE,
                "args": {"code": "x", "description": "d"},
                "requires_permission": True,
            }
        )
    )
    _get_session_store(COMMUNITY)["sess-parked"] = session
    return session


class TestResume:
    def _resume(self, client, **extra):
        body = {
            "session_id": "sess-parked",
            "result": {"call_id": EXECUTE_CALL_ID, "status": "ok", "summary": "peak 10 Hz"},
            "client_tools": [EXECUTE_CODE],
            **extra,
        }
        response = client.post(
            f"/{COMMUNITY}/chat/resume", json=body, headers={"Origin": ORIGIN, **BYOK}
        )
        assert response.status_code == 200, response.text
        return _events(response)

    def test_uses_the_sessions_tier(self, client, monkeypatch) -> None:
        _parked_analysis_session("analysis")
        rig = _rig(monkeypatch, quick=[say("unused")])

        done = _done(self._resume(client))

        assert rig.tiers == ["analysis"]
        assert done["model"] == SONNET
        assert done["mode"] == "analysis"
        assert rig.quick.calls == 0

    def test_a_quick_session_resumes_on_the_quick_model(self, client, monkeypatch) -> None:
        """The tier is the session's, not the message's: a resume carries no message."""
        _parked_analysis_session("quick")
        rig = _rig(monkeypatch, quick=[say("Done.")])

        done = _done(self._resume(client))

        assert rig.tiers == ["quick"]
        assert done["model"] == HAIKU
        assert done["mode"] == "quick"
        assert rig.analysis.calls == 0

    def test_a_resume_never_hands_over_whatever_the_history_says(self, client, monkeypatch) -> None:
        _parked_analysis_session("analysis")
        _rig(monkeypatch, quick=[say("unused")])

        events = self._resume(client)

        assert _stages(events) == []
        assert _done(events)["session_id"] == "sess-parked"


class TestRoutingIsOff:
    def test_an_explicit_model_wins(self, client, monkeypatch) -> None:
        rig = _rig(monkeypatch, quick=[say("Hi.")], analysis=[say("No.")])

        events = _chat(client, "plot the ERP for famous faces", model="haiku")

        assert rig.tiers == [None]
        assert "tier" not in rig.calls[0]
        done = _done(events)
        assert done["model"] == HAIKU
        assert "mode" not in done
        assert _stages(events) == []
        assert EXECUTE_CODE in rig.quick.bound_tool_names
        assert "start_analysis" not in rig.quick.bound_tool_names

    def test_a_request_that_does_not_declare_execute_code_is_not_routed(
        self, client, monkeypatch
    ) -> None:
        rig = _rig(monkeypatch, quick=[say("Hi.")])

        events = _chat(client, "plot the ERP for famous faces", client_tools=[])

        assert "tier" not in rig.calls[0]
        assert "mode" not in _done(events)
        assert _stages(events) == []
        assert "start_analysis" not in rig.quick.bound_tool_names
        assert rig.quick.bound_tool_names == [] or EXECUTE_CODE not in rig.quick.bound_tool_names

    def test_a_community_without_the_keys_behaves_as_it_did(self, client, monkeypatch) -> None:
        by_model = StreamingScriptedChatModel(chunk_script=[say("Hi.")])
        real_create = community_module.create_community_assistant
        seen: list[dict] = []
        monkeypatch.setattr(community_module, "create_anthropic_llm", lambda **_kw: by_model)

        def spy(*args, **kwargs):
            seen.append(kwargs)
            return real_create(*args, **kwargs)

        monkeypatch.setattr(community_module, "create_community_assistant", spy)

        events = _chat(client, "plot the ERP for famous faces", community=PLAIN)

        assert "tier" not in seen[0]
        assert "mode" not in _done(events)
        assert _stages(events) == []
        assert "start_analysis" not in by_model.bound_tool_names
        assert EXECUTE_CODE in by_model.bound_tool_names
