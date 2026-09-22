"""The two-run continuation, driven end to end through the real graph.

What is real here: the community config and its validator, `build_client_tools`, the
`CommunityAssistant`, the compiled LangGraph graph, the routing decision, the
`client_tools` node, the shape of `astream_events`, `_stream_chat_response`'s SSE
assembly, and the session store.

What stands in: the chat model, whose behavior is not under test, and the router's
`create_community_assistant`, which builds a real LLM client from settings this suite
has no keys for. `.rules/testing_guidelines.md` permits both, and names the second as
needing a companion test closer to the wire, which
`tests/test_integration/test_client_tool_round_trip.py` provides.

Deliberately NOT stood in: the graph and its events. An earlier draft replayed canned
LangGraph events, which would have asserted the shape of the fixture rather than the
shape langgraph actually emits. The one thing this phase most needs to be true is that
the root graph's `on_chain_end` carries `pending_client_call`, and a canned event
proves nothing about that.
"""

import json
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from src.api.routers.community import (
    AssistantWithMetrics,
    ChatSession,
    _stream_chat_response,
)
from src.api.tool_results import ClientToolResult, ToolResultImage
from src.assistants.community import CommunityAssistant
from src.core.config.community import CommunityConfig
from tests.helpers.chat_models import (
    ScriptedChatModel,
    multi_tool_call_response,
    tool_call_response,
)
from tests.helpers.images import bar_chart_png

CALL_ID = "toolu_01aaaaaaaaaaaaaaaaaaaaaa"
SECOND_CALL_ID = "toolu_01bbbbbbbbbbbbbbbbbbbbbb"
COMMUNITY = "browsertest"


@tool
def lookup_docs(query: str) -> str:
    """Look something up. A real, server-executed tool, for the mixed-batch test."""
    return f"documentation for {query}"


def _config(**overrides: Any) -> CommunityConfig:
    fields: dict[str, Any] = {
        "id": COMMUNITY,
        "name": "Browser Test",
        "description": "A community that runs code in the browser",
        "extensions": {
            "client_tools": [
                {
                    "name": "execute_code",
                    "runtime": "python",
                    "requires_permission": True,
                    "description": "Run Python in the user's browser.",
                }
            ]
        },
        "runtime": {
            "python": {
                "pyodide_version": "314.0.6",
                "lockfile": "runtime/browsertest-pyodide-lock.json",
                "limits": {},
            }
        },
    }
    fields.update(overrides)
    return CommunityConfig(**fields)


def _assistant(
    responses: list, *, declared: set[str] | None = None, server_tools: list | None = None
) -> tuple[CommunityAssistant, ScriptedChatModel]:
    model = ScriptedChatModel(responses=responses)
    assistant = CommunityAssistant(
        model=model,
        config=_config(),
        preload_docs=False,
        additional_tools=server_tools or [],
        declared_client_tools={"execute_code"} if declared is None else declared,
    )
    return assistant, model


def _awm(assistant: CommunityAssistant) -> AssistantWithMetrics:
    return AssistantWithMetrics(
        assistant=assistant, model="claude-haiku-4-5", key_source="platform"
    )


async def _collect(agen) -> list[dict]:
    events = []
    async for line in agen:
        assert line.startswith("data: ")
        events.append(json.loads(line[len("data: ") :]))
    return events


async def _run(session: ChatSession, assistant: CommunityAssistant, **kwargs) -> list[dict]:
    with patch(
        "src.api.routers.community.create_community_assistant", return_value=_awm(assistant)
    ):
        return await _collect(_stream_chat_response(COMMUNITY, session, None, None, None, **kwargs))


def _session() -> ChatSession:
    session = ChatSession("sess-1", COMMUNITY)
    session.add_user_message("Plot the alpha power.")
    return session


def _names(events: list[dict]) -> list[str]:
    return [event["event"] for event in events]


class TestTheToolReachesTheModel:
    def test_a_declared_client_tool_is_bound(self) -> None:
        assistant, model = _assistant([AIMessage(content="hi")])

        assert "execute_code" in model.bound_tool_names

    def test_an_undeclared_client_tool_is_never_bound(self) -> None:
        """Structural, not checked: the model cannot call what it cannot see, so the
        server cannot ask a client to run something it has no executor for."""
        _, model = _assistant([AIMessage(content="hi")], declared=set())

        assert "execute_code" not in model.bound_tool_names

    def test_declaring_a_different_tool_binds_nothing(self) -> None:
        _, model = _assistant([AIMessage(content="hi")], declared={"render_html"})

        assert "execute_code" not in model.bound_tool_names


class TestRunOneEndsOnTheCall:
    @pytest.mark.asyncio
    async def test_it_emits_tool_request_and_no_done(self) -> None:
        """`done` means the turn finished. This turn is waiting on a browser, so a
        client that saw `done` would render a reply that has not been written."""
        assistant, _ = _assistant([tool_call_response("execute_code", {"code": "x"}, CALL_ID)])

        events = await _run(_session(), assistant, declared_client_tools={"execute_code"})

        assert "tool_request" in _names(events)
        assert "done" not in _names(events)

    @pytest.mark.asyncio
    async def test_the_request_carries_the_providers_own_call_id(self) -> None:
        assistant, _ = _assistant([tool_call_response("execute_code", {"code": "x"}, CALL_ID)])

        events = await _run(_session(), assistant, declared_client_tools={"execute_code"})

        request = next(e for e in events if e["event"] == "tool_request")
        assert request["call_id"] == CALL_ID
        assert request["tool"] == "execute_code"
        assert request["args"] == {"code": "x"}
        assert request["requires_permission"] is True

    @pytest.mark.asyncio
    async def test_the_session_parks_the_call_and_keeps_the_assistant_message(self) -> None:
        """The assistant message carrying tool_calls has to survive the turn boundary,
        or run 2 has nothing to send."""
        session = _session()
        assistant, _ = _assistant([tool_call_response("execute_code", {"code": "x"}, CALL_ID)])

        await _run(session, assistant, declared_client_tools={"execute_code"})

        assert session.pending_call is not None
        assert session.pending_call.call_id == CALL_ID
        assert any(isinstance(m, AIMessage) and m.tool_calls for m in session.messages), (
            "the tool call was dropped at the turn boundary"
        )


class TestBatches:
    @pytest.mark.asyncio
    async def test_a_server_call_in_the_same_batch_is_answered(self) -> None:
        """Every tool_use needs a matching tool_result or the provider refuses the
        whole message list."""
        session = _session()
        assistant, _ = _assistant(
            [
                multi_tool_call_response(
                    [
                        ("lookup_docs", {"query": "alpha"}, "call_server"),
                        ("execute_code", {"code": "x"}, CALL_ID),
                    ]
                )
            ],
            server_tools=[lookup_docs],
        )

        events = await _run(session, assistant, declared_client_tools={"execute_code"})

        assert _names(events).count("tool_request") == 1
        answered = {m.tool_call_id for m in session.messages if isinstance(m, ToolMessage)}
        assert "call_server" in answered

    @pytest.mark.asyncio
    async def test_a_second_browser_call_is_refused_in_writing(self) -> None:
        """One browser execution per turn. The second still needs a tool_result, and
        saying why in it is how the model learns the constraint."""
        session = _session()
        assistant, _ = _assistant(
            [
                multi_tool_call_response(
                    [
                        ("execute_code", {"code": "first"}, CALL_ID),
                        ("execute_code", {"code": "second"}, SECOND_CALL_ID),
                    ]
                )
            ]
        )

        events = await _run(session, assistant, declared_client_tools={"execute_code"})

        assert _names(events).count("tool_request") == 1
        assert session.pending_call.call_id == CALL_ID
        refused = [
            m
            for m in session.messages
            if isinstance(m, ToolMessage) and m.tool_call_id == SECOND_CALL_ID
        ]
        assert len(refused) == 1, "the second call was left without a result"

    @pytest.mark.asyncio
    async def test_every_tool_call_ends_up_answered_or_pending(self) -> None:
        """The invariant the provider actually enforces, asserted directly rather than
        via the individual cases above."""
        session = _session()
        assistant, _ = _assistant(
            [
                multi_tool_call_response(
                    [
                        ("lookup_docs", {"query": "a"}, "call_server"),
                        ("execute_code", {"code": "x"}, CALL_ID),
                        ("execute_code", {"code": "y"}, SECOND_CALL_ID),
                    ]
                )
            ],
            server_tools=[lookup_docs],
        )

        await _run(session, assistant, declared_client_tools={"execute_code"})

        called = {
            call["id"]
            for m in session.messages
            if isinstance(m, AIMessage)
            for call in m.tool_calls
        }
        answered = {m.tool_call_id for m in session.messages if isinstance(m, ToolMessage)}
        pending = {session.pending_call.call_id}

        assert called == answered | pending


class TestNothingHappensWithoutAClientTool:
    @pytest.mark.asyncio
    async def test_an_ordinary_turn_still_ends_in_done(self) -> None:
        """Every community ships with client_tools unset, so this is the path that
        actually runs in production for this phase."""
        session = _session()
        assistant, _ = _assistant([AIMessage(content="Alpha power is 10.2 Hz.")], declared=set())

        events = await _run(session, assistant)

        assert "done" in _names(events)
        assert "tool_request" not in _names(events)
        assert session.pending_call is None


class TestClaimingIsOnceOnly:
    def test_a_replayed_call_id_is_refused(self) -> None:
        session = _session()
        session.set_pending_call(_pending())

        assert session.claim_pending_call(CALL_ID) is not None
        assert session.claim_pending_call(CALL_ID) is None

    def test_a_different_call_id_is_refused(self) -> None:
        session = _session()
        session.set_pending_call(_pending())

        assert session.claim_pending_call(SECOND_CALL_ID) is None

    def test_claiming_does_not_await(self) -> None:
        """The concurrency design in one assertion.

        The session store has no locking, so the only thing keeping check-then-clear
        atomic is that no await appears between them. A coroutine function here would
        reintroduce a window in which one result is accepted twice.
        """
        import inspect

        assert not inspect.iscoroutinefunction(ChatSession.claim_pending_call)

    def test_an_expired_call_is_repaired_not_merely_refused(self) -> None:
        """Leaving a stale call parked would keep an unanswered tool_call in history,
        and that is what makes a session unusable rather than merely stale."""
        from datetime import UTC, datetime, timedelta

        from src.api.tool_results import PENDING_CALL_TTL_SECONDS, PendingClientCall

        session = _session()
        session.messages.append(
            AIMessage(content="", tool_calls=[{"name": "execute_code", "args": {}, "id": CALL_ID}])
        )
        session.set_pending_call(
            PendingClientCall(
                call_id=CALL_ID,
                tool="execute_code",
                args={},
                requires_permission=True,
                created_at=datetime.now(UTC) - timedelta(seconds=PENDING_CALL_TTL_SECONDS + 1),
            )
        )

        assert session.claim_pending_call(CALL_ID) is None
        assert session.pending_call is None
        assert any(
            isinstance(m, ToolMessage) and m.tool_call_id == CALL_ID for m in session.messages
        ), "history was left with a tool call nothing answers"


class TestAbandonment:
    def test_moving_on_leaves_a_message_list_the_provider_accepts(self) -> None:
        session = _session()
        session.messages.append(
            AIMessage(content="", tool_calls=[{"name": "execute_code", "args": {}, "id": CALL_ID}])
        )
        session.set_pending_call(_pending())

        assert session.abandon_pending_call("the conversation moved on") is True

        called = {
            call["id"]
            for m in session.messages
            if isinstance(m, AIMessage)
            for call in m.tool_calls
        }
        answered = {m.tool_call_id for m in session.messages if isinstance(m, ToolMessage)}
        assert called == answered

    def test_abandoning_nothing_is_not_an_error(self) -> None:
        assert _session().abandon_pending_call("nothing to do") is False


class TestRunTwo:
    @pytest.mark.asyncio
    async def test_the_result_reaches_the_model_with_its_image(self) -> None:
        """The point of the whole phase: a plot drawn in the browser reaches the model."""
        import base64

        png = base64.b64encode(bar_chart_png([0.35, 0.60, 1.0, 0.12])).decode()
        session = _session()
        session.messages.append(
            AIMessage(content="", tool_calls=[{"name": "execute_code", "args": {}, "id": CALL_ID}])
        )
        result = ClientToolResult(
            call_id=CALL_ID,
            summary="peak 10.2 Hz",
            images=[ToolResultImage(mime="image/png", data_base64=png, width=640, height=480)],
        )
        from src.api.tool_results import build_history_tool_message, build_live_tool_message

        live = [*session.messages, build_live_tool_message(result)]
        session.messages.append(build_history_tool_message(result))

        assistant, model = _assistant([AIMessage(content="The peak is at 10.2 Hz.")])
        await _run(
            session,
            assistant,
            declared_client_tools={"execute_code"},
            initial_messages=live,
        )

        sent = model.seen_message_lists[-1]
        tool_messages = [m for m in sent if isinstance(m, ToolMessage)]
        assert tool_messages, "run 2 sent no tool result at all"
        blocks = tool_messages[-1].content
        assert any(b.get("type") == "image" for b in blocks), "the image did not reach the model"

    @pytest.mark.asyncio
    async def test_the_stored_history_keeps_no_image_bytes(self) -> None:
        import base64

        png = base64.b64encode(bar_chart_png([0.35, 0.60, 1.0, 0.12])).decode()
        session = _session()
        result = ClientToolResult(
            call_id=CALL_ID,
            images=[ToolResultImage(mime="image/png", data_base64=png, width=640, height=480)],
        )
        from src.api.tool_results import build_history_tool_message

        session.messages.append(build_history_tool_message(result))

        assert png not in json.dumps([str(m.content) for m in session.messages])


def _pending():
    from datetime import UTC, datetime

    from src.api.tool_results import PendingClientCall

    return PendingClientCall(
        call_id=CALL_ID,
        tool="execute_code",
        args={"code": "x"},
        requires_permission=True,
        created_at=datetime.now(UTC),
    )
