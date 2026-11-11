"""The widget's reseed limits agree with the server's (E1.6, #659).

The widget sends `prior_turns` and the server bounds them, and the two sets of numbers live
in two languages. `frontend/test-widget-reseed.js` holds the widget to its own constants;
this reads them from the widget source and holds them to the server's, so a change on one
side that the other does not follow fails here instead of as a 422 on every request of a
restored chat, or as a widget that stops reseeding early.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.agents.model_routing import (
    OMITTED_TURNS_MARKER,
    RESEED_TURN_MAX_CHARS,
    RESEED_TURNS_MAX,
    SUMMARY_INPUT_MAX_CHARS,
)
from src.api.routers.community import SESSION_TTL_HOURS, ChatRequest, ChatSession

WIDGET = Path(__file__).resolve().parents[2] / "frontend" / "osa-chat-widget.js"


def widget_constant(name: str) -> int:
    match = re.search(rf"const {name} = (\d+);", WIDGET.read_text(encoding="utf-8"))
    assert match, f"the widget no longer declares {name}"
    return int(match.group(1))


class TestTheWidgetStaysWithinTheServer:
    def test_it_never_sends_more_turns_than_the_server_accepts(self) -> None:
        assert widget_constant("RESEED_MAX_TURNS") <= RESEED_TURNS_MAX

    def test_it_never_sends_a_longer_turn_than_the_server_accepts(self) -> None:
        assert widget_constant("RESEED_TURN_MAX_CHARS") <= RESEED_TURN_MAX_CHARS

    def test_its_total_is_what_the_server_keeps(self) -> None:
        assert widget_constant("RESEED_TOTAL_CHARS") <= SUMMARY_INPUT_MAX_CHARS

    def test_it_stops_resending_when_the_server_stops_holding_the_session(self) -> None:
        assert widget_constant("SESSION_TTL_HOURS") == SESSION_TTL_HOURS


class TestTheLargestBodyTheWidgetBuilds:
    @pytest.fixture
    def body(self) -> dict:
        turns = widget_constant("RESEED_MAX_TURNS")
        chars = widget_constant("RESEED_TURN_MAX_CHARS")
        overhead = widget_constant("RESEED_TURN_OVERHEAD")
        budget = widget_constant("RESEED_TOTAL_CHARS")
        # What `selectReseedTurns` keeps of full turns: the newest ones that fit.
        kept = min(turns, budget // (chars + overhead))
        prior = [
            {"role": "user" if i % 2 == 0 else "assistant", "text": "x" * chars}
            for i in range(kept)
        ]
        return {"message": "again", "prior_turns": prior, "mode": "analysis"}

    def test_is_a_valid_chat_request(self, body) -> None:
        request = ChatRequest.model_validate(body)

        assert request.prior_turns is not None
        assert len(request.prior_turns) == len(body["prior_turns"])

    def test_is_seeded_whole_with_no_turn_dropped(self, body) -> None:
        request = ChatRequest.model_validate(body)
        session = ChatSession("s", "nemar")

        session.reseed(request.prior_turns or [], request.message)

        assert session.reseeded_as is not None
        assert len(session.reseeded_as.turns) == len(body["prior_turns"])
        assert OMITTED_TURNS_MARKER not in session.reseeded_as.stored
