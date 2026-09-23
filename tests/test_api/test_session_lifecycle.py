"""Eviction, overlapping turns, and the collisions that have no symptom.

Each of these was found by review rather than by a failing test, and each shares a
shape: the wrong behavior produces no error. A session is evicted and the work running
on someone's machine simply vanishes; two turns interleave and one is silently
discarded; a name collision routes a server tool's calls to a browser that cannot run
them and the turn just hangs.
"""

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from src.api.routers.community import (
    TURN_CLAIM_TTL_SECONDS,
    ChatSession,
    _evict_lru_session,
    _get_session_store,
)
from src.api.tool_results import PendingClientCall
from src.assistants.community import CommunityAssistant
from src.core.config.community import CommunityConfig
from tests.helpers.chat_models import ScriptedChatModel

COMMUNITY = "lifecycletest"
CALL_ID = "toolu_01aaaaaaaaaaaaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _clean_store():
    _get_session_store(COMMUNITY).clear()
    yield
    _get_session_store(COMMUNITY).clear()


def _pending() -> PendingClientCall:
    return PendingClientCall.from_state(
        {"call_id": CALL_ID, "tool": "execute_code", "args": {}, "requires_permission": True}
    )


def _session(session_id: str, *, age_s: float, parked: bool = False) -> ChatSession:
    store = _get_session_store(COMMUNITY)
    session = ChatSession(session_id, COMMUNITY)
    if parked:
        session.set_pending_call(_pending())
    session.last_active = datetime.now(UTC) - timedelta(seconds=age_s)
    store[session_id] = session
    return session


class TestEvictionPrefersIdleSessions:
    def test_it_spares_a_parked_call_even_when_that_session_is_oldest(self) -> None:
        """A person reading code before approving it does not touch `last_active`, so
        the session waiting on them is exactly the one a naive rule evicts first. Their
        resume would then 404 with the running work gone and no way back."""
        _session("parked", age_s=600, parked=True)
        _session("idle", age_s=10)

        _evict_lru_session(COMMUNITY)

        store = _get_session_store(COMMUNITY)
        assert "parked" in store, "the session mid-browser-call was evicted"
        assert "idle" not in store

    def test_it_still_evicts_the_oldest_among_idle_sessions(self) -> None:
        _session("older", age_s=600)
        _session("newer", age_s=10)

        _evict_lru_session(COMMUNITY)

        assert "older" not in _get_session_store(COMMUNITY)

    def test_it_evicts_a_parked_session_when_every_session_is_parked(self) -> None:
        """Sparing them unconditionally would let the store grow without bound, which
        is the memory exhaustion the cap exists to prevent. Last resort, not never."""
        _session("a", age_s=600, parked=True)
        _session("b", age_s=10, parked=True)

        _evict_lru_session(COMMUNITY)

        assert len(_get_session_store(COMMUNITY)) == 1
        assert "a" not in _get_session_store(COMMUNITY)

    def test_an_empty_store_is_not_an_error(self) -> None:
        _evict_lru_session(COMMUNITY)


class TestOverlappingTurns:
    def test_a_second_turn_is_refused_while_one_is_running(self) -> None:
        session = ChatSession("s", COMMUNITY)

        assert session.begin_turn() is True
        assert session.begin_turn() is False

    def test_the_claim_is_released_when_the_turn_ends(self) -> None:
        session = ChatSession("s", COMMUNITY)
        session.begin_turn()

        session.end_turn()

        assert session.begin_turn() is True

    def test_a_stale_claim_heals_itself(self) -> None:
        """A generator that is never closed, which a dropped connection causes, would
        otherwise wedge the session until its 24-hour TTL. That trades a rare lost turn
        for a permanently broken conversation, which is the worse failure."""
        session = ChatSession("s", COMMUNITY)
        session.begin_turn()
        session.turn_started_at = datetime.now(UTC) - timedelta(seconds=TURN_CLAIM_TTL_SECONDS + 1)

        assert session.begin_turn() is True

    def test_claiming_does_not_await(self) -> None:
        """Same reasoning as claim_pending_call: with no locking anywhere, a
        check-then-set is atomic only while no await sits between the two."""
        import inspect

        assert not inspect.iscoroutinefunction(ChatSession.begin_turn)


@tool
def execute_code(code: str) -> str:
    """A SERVER tool that happens to share a name with the client tool below."""
    return code


class TestNameCollisions:
    def test_a_client_tool_may_not_shadow_a_server_tool(self) -> None:
        """The one configuration mistake here with no symptom.

        The agent partitions tools by name, so a shared name drops the server tool from
        the node that executes it and parks the model's calls to it for a browser that
        has no executor for that name. The turn hangs rather than failing.
        """
        config = CommunityConfig(
            id=COMMUNITY,
            name="Lifecycle Test",
            description="collision",
            extensions={
                "client_tools": [
                    {"name": "execute_code", "runtime": "python", "description": "Run it."}
                ]
            },
            runtime={"python": {"pyodide_version": "314.0.6", "lockfile": "runtime/l.json"}},
        )

        with pytest.raises(ValueError, match="already belong to server-executed tools"):
            CommunityAssistant(
                model=ScriptedChatModel(responses=[AIMessage(content="hi")]),
                config=config,
                preload_docs=False,
                additional_tools=[execute_code],
                declared_client_tools={"execute_code"},
            )

    def test_no_collision_is_the_ordinary_case(self) -> None:
        config = CommunityConfig(
            id=COMMUNITY,
            name="Lifecycle Test",
            description="no collision",
            extensions={
                "client_tools": [
                    {"name": "execute_code", "runtime": "python", "description": "Run it."}
                ]
            },
            runtime={"python": {"pyodide_version": "314.0.6", "lockfile": "runtime/l.json"}},
        )

        assistant = CommunityAssistant(
            model=ScriptedChatModel(responses=[AIMessage(content="hi")]),
            config=config,
            preload_docs=False,
            declared_client_tools={"execute_code"},
        )

        assert "execute_code" in assistant.client_tool_names
