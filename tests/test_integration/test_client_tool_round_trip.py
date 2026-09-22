"""The browser tool round trip against a real model, over real HTTP.

The companion `.rules/testing_guidelines.md` asks for. `tests/test_api/test_client_tool_streaming.py`
drives the same code with the router's `create_community_assistant` patched, which is
one layer above the HTTP boundary; the rule is that such a test needs a sibling closer
to the wire covering the same path. This is it.

What it proves that the offline tests cannot: that a real provider accepts the message
list this phase builds. That is the assumption everything else rests on, and it is not
observable offline, because the constraint being tested is the provider's own. Every
`tool_use` block must be answered by a matching `tool_result`, and a message list that
breaks that rule is rejected outright rather than degraded, so a session that produced
one would fail on every later turn rather than just the turn that caused it.

Real, paid API calls. Prompts are kept tiny so a run costs cents.
"""

import base64

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.api.config import get_settings
from src.api.tool_results import (
    ClientToolResult,
    ToolResultImage,
    build_history_tool_message,
    build_live_tool_message,
    build_unanswered_tool_message,
)
from src.core.services.anthropic_llm import create_anthropic_llm
from tests.helpers.images import BAR_FIXTURES, bar_chart_png, tallest_and_shortest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm,
    pytest.mark.skipif(
        not get_settings().anthropic_api_key,
        reason="ANTHROPIC_API_KEY is not configured (server mode requires it)",
    ),
]

CALL_ID = "toolu_01aaaaaaaaaaaaaaaaaaaaaa"
SECOND_CALL_ID = "toolu_01bbbbbbbbbbbbbbbbbbbbbb"


def _llm():
    return create_anthropic_llm(model="claude-haiku-4-5", thinking=None, settings=get_settings())


def _call(call_id: str = CALL_ID, code: str = "print(1)") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "execute_code", "args": {"code": code}, "id": call_id, "type": "tool_call"}
        ],
    )


def _text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


class TestTheProviderAcceptsWhatWeBuild:
    def test_a_completed_browser_turn_is_accepted(self) -> None:
        result = ClientToolResult(call_id=CALL_ID, summary="alpha peak at 10.2 Hz", stdout="1")

        reply = _llm().invoke(
            [
                SystemMessage(content="Answer in one short sentence."),
                HumanMessage(content="What was the alpha peak?"),
                _call(),
                build_live_tool_message(result),
            ]
        )

        assert _text(reply).strip()

    def test_the_history_form_is_accepted_too(self) -> None:
        """Stored history is what run 3 sends, so the placeholder form has to be a
        legal message list in its own right, not only the live form."""
        png = base64.b64encode(bar_chart_png(BAR_FIXTURES["tallest_third"])).decode()
        result = ClientToolResult(
            call_id=CALL_ID,
            summary="a bar chart",
            images=[ToolResultImage(mime="image/png", data_base64=png, width=640, height=480)],
        )

        reply = _llm().invoke(
            [
                SystemMessage(content="Answer in one short sentence."),
                HumanMessage(content="What did you plot?"),
                _call(),
                build_history_tool_message(result),
                HumanMessage(content="Thanks. Say OK."),
            ]
        )

        assert _text(reply).strip()

    def test_an_abandoned_call_leaves_a_usable_session(self) -> None:
        """The repair in `ChatSession.abandon_pending_call`, checked against the only
        authority that matters. Without the synthetic result the provider rejects this
        list, and the session is dead rather than merely missing a turn.
        """
        reply = _llm().invoke(
            [
                SystemMessage(content="Answer in one short sentence."),
                HumanMessage(content="Plot something."),
                _call(),
                build_unanswered_tool_message(CALL_ID, "the conversation moved on"),
                HumanMessage(content="Never mind. Say OK."),
            ]
        )

        assert _text(reply).strip()

    def test_a_refused_second_call_is_accepted(self) -> None:
        """One browser execution per turn means the second call gets a written refusal.
        Both results must be present or the batch is rejected."""
        batch = AIMessage(
            content="",
            tool_calls=[
                {"name": "execute_code", "args": {"code": "a"}, "id": CALL_ID, "type": "tool_call"},
                {
                    "name": "execute_code",
                    "args": {"code": "b"},
                    "id": SECOND_CALL_ID,
                    "type": "tool_call",
                },
            ],
        )

        reply = _llm().invoke(
            [
                SystemMessage(content="Answer in one short sentence."),
                HumanMessage(content="Run two things."),
                batch,
                build_live_tool_message(ClientToolResult(call_id=CALL_ID, summary="ran a")),
                build_unanswered_tool_message(SECOND_CALL_ID, "only one run per turn"),
                HumanMessage(content="Say OK."),
            ]
        )

        assert _text(reply).strip()


class TestTheModelReadsTheResult:
    def test_it_answers_from_the_figure_the_browser_drew(self) -> None:
        """End to end, for real: a plot drawn where the code ran reaches the model and
        is read. `tests/test_core/test_tool_result_image_transport.py` proved the block
        survives the payload builder; this proves the model uses it."""
        heights = BAR_FIXTURES["tallest_third"]
        tallest, _ = tallest_and_shortest(heights)
        png = base64.b64encode(bar_chart_png(heights)).decode()
        result = ClientToolResult(
            call_id=CALL_ID,
            summary="a bar chart with four bars",
            images=[ToolResultImage(mime="image/png", data_base64=png, width=640, height=480)],
        )

        reply = _llm().invoke(
            [
                SystemMessage(content="Answer with a single digit and nothing else."),
                HumanMessage(content="Counting from the left starting at 1, which bar is tallest?"),
                _call(code="plot()"),
                build_live_tool_message(result),
            ]
        )

        assert str(tallest) in _text(reply)

    def test_the_summary_survives_the_fencing(self) -> None:
        """Fencing the output as data must not make it unreadable to the model, which
        would trade one failure for another."""
        result = ClientToolResult(call_id=CALL_ID, summary="the alpha peak is at 10.2 Hz")

        reply = _llm().invoke(
            [
                SystemMessage(content="Answer with the number only."),
                HumanMessage(content="What frequency was the alpha peak?"),
                _call(),
                build_live_tool_message(result),
            ]
        )

        assert "10.2" in _text(reply)
