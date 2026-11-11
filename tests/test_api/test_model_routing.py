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

import asyncio
import json
from dataclasses import dataclass, field

import anthropic
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

import src.agents.model_routing as model_routing
import src.api.routers.community as community_module
from src.api.handover import stream_handover
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
from src.metrics.db import init_metrics_db, metrics_connection
from src.metrics.middleware import MetricsMiddleware
from src.tools.client_tools import CLIENT_TOOL_KILL_SWITCH_ENV
from tests.helpers.chat_models import (
    FailingThenSayingChatModel,
    ScriptedChatModel,
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


def _rig(monkeypatch, *, quick: list, analysis: list | None = None, quick_model=None) -> Rig:
    """`quick_model` stands in for the quick model when a test needs its own behavior."""
    rig = Rig(
        quick=quick_model or StreamingScriptedChatModel(chunk_script=quick),
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
        # Where the events sit in the stream: the old session's event first, the three
        # stages, then the new session's own event, and `done` last.
        at = {
            "session": next(i for i, e in enumerate(events) if e["event"] == "session"),
            "summarizing": next(i for i, e in enumerate(events) if e.get("stage") == "summarizing"),
            "summarized": next(i for i, e in enumerate(events) if e.get("stage") == "summarized"),
            "started": next(i for i, e in enumerate(events) if e.get("stage") == "started"),
            "new session": max(i for i, e in enumerate(events) if e["event"] == "session"),
            "done": next(i for i, e in enumerate(events) if e["event"] == "done"),
        }
        assert list(at.values()) == sorted(set(at.values())), at
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


class TestAFailingSummarizer:
    """The summary call fails: the handover still happens, on the reader's own words."""

    def _failing(self, count: int) -> FailingThenSayingChatModel:
        request = httpx2.Request("POST", "https://api.anthropic.com")
        return FailingThenSayingChatModel(
            failures=[
                anthropic.InternalServerError(
                    "overloaded", response=httpx2.Response(529, request=request), body=None
                )
                for _ in range(count)
            ],
            responses=[AIMessage(content=SUMMARY)],
        )

    def test_a_failure_that_survives_the_retry_degrades_and_the_turn_is_answered(
        self, client, monkeypatch
    ) -> None:
        monkeypatch.setattr(model_routing, "SUMMARY_RETRY_DELAY_SECONDS", 0)
        _earlier_conversation()
        quick = self._failing(model_routing.SUMMARY_ATTEMPTS)
        rig = _rig(monkeypatch, quick=[], quick_model=quick)

        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        assert "error" not in _names(events)
        assert _stages(events) == ["summarizing", "summarized", "started"]
        summarized = next(e for e in events if e.get("stage") == "summarized")
        assert summarized["degraded"] is True
        assert "face stimuli" in summarized["summary"]
        assert quick.attempts == model_routing.SUMMARY_ATTEMPTS
        done = _done(events)
        assert done["model"] == SONNET
        human = [m for m in rig.analysis.seen_message_lists[0] if isinstance(m, HumanMessage)]
        assert "face stimuli" in human[0].content

    def test_a_failure_that_clears_on_the_retry_is_an_ordinary_handover(
        self, client, monkeypatch
    ) -> None:
        monkeypatch.setattr(model_routing, "SUMMARY_RETRY_DELAY_SECONDS", 0)
        _earlier_conversation()
        quick = self._failing(1)
        _rig(monkeypatch, quick=[], quick_model=quick)

        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        summarized = next(e for e in events if e.get("stage") == "summarized")
        assert summarized["summary"] == SUMMARY
        assert "degraded" not in summarized
        assert quick.attempts == 2


def _usage(input_tokens: int, output_tokens: int) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def say_with_usage(text: str, input_tokens: int, output_tokens: int) -> list[AIMessageChunk]:
    return [
        AIMessageChunk(
            content=[{"type": "text", "text": text, "index": 0}],
            usage_metadata=_usage(input_tokens, output_tokens),
        )
    ]


def call_start_analysis_with_usage(input_tokens: int, output_tokens: int) -> list[AIMessageChunk]:
    chunk = call_start_analysis()[0]
    chunk.usage_metadata = _usage(input_tokens, output_tokens)
    return [chunk]


def _request_rows() -> list[dict]:
    with metrics_connection() as conn:
        conn.row_factory = lambda cursor, row: dict(
            zip([c[0] for c in cursor.description], row, strict=True)
        )
        # The metrics middleware writes a stub row (no model, no tokens) under the same
        # request id when a streamed response starts, before the handler's own row (a
        # pre-existing behavior of every streamed request, tracked in its own issue). Only
        # the rows the chat path writes are counted here.
        return conn.execute(
            "SELECT * FROM request_log WHERE model IS NOT NULL ORDER BY id"
        ).fetchall()


@pytest.fixture
def metered_client(tmp_path, monkeypatch) -> TestClient:
    """The app with the metrics middleware, which gives a request the id `done` reports."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    init_metrics_db()
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)
    app.include_router(create_community_router(COMMUNITY))
    return TestClient(app)


class TestHandoverMetrics:
    """One row per request id, and the quick model's spend is on its own row."""

    def test_a_keyword_handover_logs_the_summary_on_a_quick_row(
        self, metered_client, monkeypatch
    ) -> None:
        _earlier_conversation()
        _rig(
            monkeypatch,
            quick=[say_with_usage(SUMMARY, 300, 40)],
            analysis=[say_with_usage("Plotting.", 1000, 200)],
        )

        done = _done(_chat(metered_client, "plot the ERP", session_id="sess-quick"))

        rows = _request_rows()
        by_id = {r["request_id"]: r for r in rows}
        assert len(by_id) == len(rows), "no two rows share a request id"
        analysis = by_id[done["request_id"]]
        quick = by_id[f"{done['request_id']}:quick"]
        assert analysis["model"] == SONNET
        assert (analysis["input_tokens"], analysis["output_tokens"]) == (1000, 200)
        assert quick["model"] == HAIKU
        assert (quick["input_tokens"], quick["output_tokens"]) == (300, 40)
        assert quick["estimated_cost"] is not None

    def test_a_start_analysis_handover_adds_the_quick_run_and_the_summary_into_one_row(
        self, metered_client, monkeypatch
    ) -> None:
        _earlier_conversation()
        _rig(
            monkeypatch,
            quick=[call_start_analysis_with_usage(100, 10), say_with_usage(SUMMARY, 300, 40)],
            analysis=[say_with_usage("Plotting.", 1000, 200)],
        )

        done = _done(
            _chat(metered_client, "could you look at the alpha band?", session_id="sess-quick")
        )

        by_id = {r["request_id"]: r for r in _request_rows()}
        assert set(by_id) == {done["request_id"], f"{done['request_id']}:quick"}
        quick = by_id[f"{done['request_id']}:quick"]
        assert (quick["input_tokens"], quick["output_tokens"]) == (400, 50)
        assert "start_analysis" in quick["tools_called"]
        assert by_id[done["request_id"]]["model"] == SONNET

    def test_a_first_message_start_analysis_logs_two_rows_with_distinct_ids(
        self, metered_client, monkeypatch
    ) -> None:
        _rig(
            monkeypatch,
            quick=[call_start_analysis_with_usage(100, 10)],
            analysis=[say_with_usage("Plotting.", 1000, 200)],
        )

        done = _done(
            _chat(metered_client, "could you look at the alpha band?", session_id="s-first")
        )

        by_id = {r["request_id"]: r for r in _request_rows()}
        assert set(by_id) == {done["request_id"], f"{done['request_id']}:quick"}
        assert by_id[f"{done['request_id']}:quick"]["model"] == HAIKU
        assert by_id[done["request_id"]]["model"] == SONNET

    def test_a_plain_quick_turn_logs_one_row(self, metered_client, monkeypatch) -> None:
        _rig(monkeypatch, quick=[say_with_usage("Hello.", 50, 5)])

        done = _done(_chat(metered_client, "find datasets with face stimuli"))

        assert [r["request_id"] for r in _request_rows()] == [done["request_id"]]

    def test_a_degraded_summary_logs_no_tokens_for_it(self, metered_client, monkeypatch) -> None:
        monkeypatch.setattr(model_routing, "SUMMARY_RETRY_DELAY_SECONDS", 0)
        _earlier_conversation()
        request = httpx2.Request("POST", "https://api.anthropic.com")
        failing = FailingThenSayingChatModel(
            failures=[
                anthropic.InternalServerError(
                    "overloaded", response=httpx2.Response(529, request=request), body=None
                )
                for _ in range(model_routing.SUMMARY_ATTEMPTS)
            ],
            responses=[AIMessage(content=SUMMARY)],
        )
        _rig(
            monkeypatch,
            quick=[],
            quick_model=failing,
            analysis=[say_with_usage("Plotting.", 1000, 200)],
        )

        done = _done(_chat(metered_client, "plot the ERP", session_id="sess-quick"))

        by_id = {r["request_id"]: r for r in _request_rows()}
        assert by_id[done["request_id"]]["input_tokens"] == 1000
        assert f"{done['request_id']}:quick" not in by_id


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

    def test_the_call_is_not_announced_as_a_tool_call_event(self, client, monkeypatch) -> None:
        _earlier_conversation()
        _rig(monkeypatch, quick=[self._call(), say(SUMMARY)])

        events = _chat(client, "could you look at the alpha band instead?", session_id="sess-quick")

        assert [e for e in events if e["event"] in ("tool_call", "tool_start", "tool_end")] == []

    def test_text_the_quick_model_wrote_before_the_call_is_not_the_reply_or_the_seed(
        self, client, monkeypatch
    ) -> None:
        _earlier_conversation()
        preamble = "Let me hand this to the analysis assistant."
        rig = _rig(
            monkeypatch,
            quick=[[text_chunk(preamble), call_start_analysis()[0]], say(SUMMARY)],
            analysis=[say("Here is the plot.")],
        )

        events = _chat(client, "could you look at the alpha band instead?", session_id="sess-quick")

        # It was streamed before the handover, as any quick text is, and no further.
        names = _names(events)
        content_at = next(i for i, e in enumerate(events) if e["event"] == "content")
        assert content_at < names.index("handover")
        done = _done(events)
        assert done["content"] == "Here is the plot."
        assert done["model"] == SONNET
        human = [m for m in rig.analysis.seen_message_lists[0] if isinstance(m, HumanMessage)]
        assert preamble not in human[0].content
        assert "tool_request" not in names

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


class TestTurnClaims:
    """A handover never leaves a session claimed, and never leaves one unclaimed in a window."""

    def _claims(self, *session_ids: str) -> dict[str, object]:
        store = _get_session_store(COMMUNITY)
        return {sid: store[sid].turn_started_at for sid in session_ids}

    def test_a_keyword_handover_releases_both_sessions_and_a_follow_up_succeeds(
        self, client, monkeypatch
    ) -> None:
        _earlier_conversation()
        rig = _rig(monkeypatch, quick=[say(SUMMARY), say("Still here.")])
        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")
        new_id = _done(events)["session_id"]

        assert self._claims("sess-quick", new_id) == {"sess-quick": None, new_id: None}

        on_new = _chat(client, "which dataset were we discussing?", session_id=new_id)
        assert "error" not in _names(on_new)
        assert _done(on_new)["session_id"] == new_id
        on_old = _chat(client, "what else is there?", session_id="sess-quick")
        assert "error" not in _names(on_old)
        assert _done(on_old)["mode"] == "quick"
        assert rig.tiers[-1] == "quick"
        assert self._claims("sess-quick", new_id) == {"sess-quick": None, new_id: None}

    def test_a_start_analysis_handover_releases_both_sessions(self, client, monkeypatch) -> None:
        _earlier_conversation()
        _rig(monkeypatch, quick=[call_start_analysis(), say(SUMMARY)])

        events = _chat(client, "could you look at the alpha band instead?", session_id="sess-quick")
        new_id = _done(events)["session_id"]

        assert new_id != "sess-quick"
        assert self._claims("sess-quick", new_id) == {"sess-quick": None, new_id: None}
        followup = _chat(client, "and the beta band?", session_id=new_id)
        assert "error" not in _names(followup)
        assert _done(followup)["session_id"] == new_id

    def test_a_first_message_start_analysis_releases_its_session_and_a_follow_up_succeeds(
        self, client, monkeypatch
    ) -> None:
        _rig(monkeypatch, quick=[call_start_analysis()], analysis=[say("One."), say("Two.")])

        events = _chat(client, "could you look at the alpha band instead?", session_id="s-1")

        assert _done(events)["session_id"] == "s-1"
        assert _names(events).count("error") == 0
        assert self._claims("s-1") == {"s-1": None}
        followup = _chat(client, "and the beta band?", session_id="s-1")
        assert "error" not in _names(followup)
        assert _done(followup)["mode"] == "analysis"
        assert self._claims("s-1") == {"s-1": None}

    def test_a_failed_analysis_run_still_releases_the_new_session(
        self, client, monkeypatch
    ) -> None:
        _earlier_conversation()
        rig = _rig(monkeypatch, quick=[say(SUMMARY)])

        def fail(*_args, **_kwargs):
            raise RuntimeError("the analysis model is down")

        monkeypatch.setattr(rig.analysis, "_stream", fail)
        monkeypatch.setattr(rig.analysis, "_generate", fail)
        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        assert "error" in _names(events)
        started = next(e for e in events if e.get("stage") == "started")
        assert self._claims("sess-quick", started["session_id"]) == {
            "sess-quick": None,
            started["session_id"]: None,
        }

    def test_the_new_session_is_claimed_before_its_id_is_sent_and_until_the_run_ends(self) -> None:
        """stream_handover hands the analysis run a session that is already claimed."""
        opened: list[ChatSession] = []
        claimed_when_started: list[bool] = []

        def open_session() -> ChatSession:
            opened.append(ChatSession("sess-new", COMMUNITY))
            return opened[0]

        async def run_analysis(target: ChatSession):
            claimed_when_started.append(target.turn_started_at is not None)
            # The window the finding is about: the run's first event goes out here.
            yield "data: first\n\n"
            claimed_when_started.append(target.turn_started_at is not None)
            yield "data: second\n\n"

        async def drive() -> list[str]:
            lines = []
            async for line in stream_handover(
                summarizer=ScriptedChatModel(responses=[AIMessage(content=SUMMARY)]),
                history=[HumanMessage(content="hi")],
                reader_message="plot it",
                open_session=open_session,
                analysis_model=SONNET,
                run_analysis=run_analysis,
            ):
                lines.append(line)
                if '"started"' in line:
                    claimed_when_started.append(opened[0].turn_started_at is not None)
            return lines

        lines = asyncio.run(drive())

        assert claimed_when_started == [True, True, True]
        assert lines[-2:] == ["data: first\n\n", "data: second\n\n"]
        assert opened[0].turn_started_at is None

    def test_a_client_that_leaves_mid_handover_still_releases_the_new_session(self) -> None:
        opened: list[ChatSession] = []

        def open_session() -> ChatSession:
            opened.append(ChatSession("sess-new", COMMUNITY))
            return opened[0]

        async def run_analysis(_target: ChatSession):
            yield "data: first\n\n"
            yield "data: second\n\n"

        async def drive() -> None:
            stream = stream_handover(
                summarizer=ScriptedChatModel(responses=[AIMessage(content=SUMMARY)]),
                history=[HumanMessage(content="hi")],
                reader_message="plot it",
                open_session=open_session,
                analysis_model=SONNET,
                run_analysis=run_analysis,
            )
            async for line in stream:
                if '"started"' in line:
                    break
            await stream.aclose()

        asyncio.run(drive())

        assert opened[0].turn_started_at is None

    def test_a_session_that_is_already_claimed_is_refused_not_run(self) -> None:
        busy = ChatSession("sess-busy", COMMUNITY)
        assert busy.begin_turn()
        ran: list[bool] = []

        async def run_analysis(_target: ChatSession):
            ran.append(True)
            yield "data: never\n\n"

        async def drive() -> list[str]:
            return [
                line
                async for line in stream_handover(
                    summarizer=ScriptedChatModel(responses=[AIMessage(content=SUMMARY)]),
                    history=[HumanMessage(content="hi")],
                    reader_message="plot it",
                    open_session=lambda: busy,
                    analysis_model=SONNET,
                    run_analysis=run_analysis,
                )
            ]

        lines = asyncio.run(drive())

        assert ran == []
        assert '"event": "error"' in lines[-1]
        assert not any('"started"' in line for line in lines)
        # The other turn's claim is not released by the refused handover.
        assert busy.turn_started_at is not None


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
        assert EXECUTE_CODE not in rig.quick.bound_tool_names

    def test_the_client_tool_kill_switch_turns_routing_off(self, client, monkeypatch) -> None:
        monkeypatch.setenv(CLIENT_TOOL_KILL_SWITCH_ENV, "1")
        _earlier_conversation()
        rig = _rig(monkeypatch, quick=[say("Hi.")], analysis=[say("No.")])

        events = _chat(client, "plot the ERP for famous faces", session_id="sess-quick")

        assert "tier" not in rig.calls[0]
        assert _stages(events) == []
        assert "mode" not in _done(events)
        assert rig.analysis.calls == 0
        assert "start_analysis" not in rig.quick.bound_tool_names

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
