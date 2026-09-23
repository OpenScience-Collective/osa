"""Dropping old messages without breaking the ones that are left.

`ChatSession` caps how many messages it keeps. Before browser execution the cap was
harmless: messages alternated human and assistant, so cutting anywhere left a valid
list. A tool round trip is not like that. An assistant message carrying `tool_calls` and
the results answering it are one indivisible unit to the provider, and a list holding
either half alone is rejected outright rather than degraded, so a careless cut does not
lose a turn, it makes every later turn fail.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.api.routers.community import (
    MAX_MESSAGES_PER_SESSION,
    ChatSession,
    _trim_preserving_tool_turns,
)


def _turn(index: int, *, with_tools: bool = False) -> list:
    """One conversational turn, optionally one that ran a browser tool."""
    call_id = f"call_{index}"
    if not with_tools:
        return [HumanMessage(content=f"q{index}"), AIMessage(content=f"a{index}")]
    return [
        HumanMessage(content=f"q{index}"),
        AIMessage(
            content="",
            tool_calls=[{"name": "execute_code", "args": {}, "id": call_id, "type": "tool_call"}],
        ),
        ToolMessage(content="result", tool_call_id=call_id),
        AIMessage(content=f"a{index}"),
    ]


def _conversation(turns: int, *, with_tools: bool = False) -> list:
    messages = []
    for index in range(turns):
        messages.extend(_turn(index, with_tools=with_tools))
    return messages


def _is_valid(messages: list) -> bool:
    """Every tool call answered, and no result without its call.

    This is the provider's rule, restated. It is the only property that matters here.
    """
    called = {call["id"] for m in messages if isinstance(m, AIMessage) for call in m.tool_calls}
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return called == answered


class TestTrimming:
    def test_a_short_conversation_is_untouched(self) -> None:
        messages = _conversation(3)

        assert _trim_preserving_tool_turns(messages, 100) == messages

    def test_it_cuts_down_to_the_cap_when_it_safely_can(self) -> None:
        messages = _conversation(50)

        trimmed = _trim_preserving_tool_turns(messages, 20)

        assert len(trimmed) <= 20

    def test_it_cuts_at_a_human_message(self) -> None:
        """The only index where no tool round trip is in flight."""
        trimmed = _trim_preserving_tool_turns(_conversation(50, with_tools=True), 20)

        assert isinstance(trimmed[0], HumanMessage)

    def test_it_stays_valid_at_every_cut_point(self) -> None:
        """The property the whole function exists for, over every cap rather than one.

        A single cap proves little: a browser turn is four messages, so three caps in
        four happen to land on an index where a naive cut is already safe. The first
        version of this test picked such a cap and passed while measuring nothing.
        """
        messages = _conversation(50, with_tools=True)
        assert _is_valid(messages), "fixture is already invalid"

        for limit in range(4, len(messages)):
            assert _is_valid(_trim_preserving_tool_turns(messages, limit)), (
                f"trimming to {limit} orphaned a tool call"
            )

    def test_a_naive_cut_breaks_it_at_some_of_those_points(self) -> None:
        """Proves the function is doing something.

        Without this, a trimmer that returned its input unchanged would satisfy every
        assertion above. This asserts the hazard is real at the same cut points the
        test above walks, so the two together say the function both acts and is needed.
        """
        messages = _conversation(50, with_tools=True)

        broken = [
            limit
            for limit in range(4, len(messages))
            if not _is_valid(messages[len(messages) - limit :])
        ]

        assert broken, "no cap in the range exercises the hazard; the fixture is wrong"

    def test_it_keeps_the_most_recent_turns(self) -> None:
        """Dropping from the front, not the back: the recent context is the useful one."""
        trimmed = _trim_preserving_tool_turns(_conversation(50), 10)

        assert trimmed[-1].content == "a49"

    def test_it_keeps_everything_rather_than_orphan_a_call(self) -> None:
        """When no safe boundary exists after the cut, exceeding a memory cap is
        recoverable and sending a list the provider refuses is not."""
        call_id = "call_long"
        messages = [
            HumanMessage(content="go"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "execute_code", "args": {}, "id": call_id, "type": "tool_call"}
                ],
            ),
            *[ToolMessage(content=f"r{i}", tool_call_id=call_id) for i in range(20)],
        ]

        trimmed = _trim_preserving_tool_turns(messages, 5)

        assert trimmed == messages


class TestReplaceHistory:
    def test_it_adopts_the_runs_final_message_list(self) -> None:
        session = ChatSession("s", "c")
        messages = _conversation(2, with_tools=True)

        session.replace_history(messages)

        assert session.messages == messages

    def test_it_applies_the_cap(self) -> None:
        session = ChatSession("s", "c")

        session.replace_history(_conversation(MAX_MESSAGES_PER_SESSION, with_tools=True))

        assert len(session.messages) <= MAX_MESSAGES_PER_SESSION
        assert _is_valid(session.messages)

    def test_the_raised_cap_holds_a_useful_number_of_browser_turns(self) -> None:
        """A browser turn is four messages, so the old cap of 100 was about 25 turns.
        Asserted so a future reduction has to face the arithmetic rather than looking
        like a tidy-up."""
        assert MAX_MESSAGES_PER_SESSION // 4 >= 50

    def test_it_scrubs_images_when_it_also_trims(self) -> None:
        """Trimming and scrubbing are separate passes, and both must run when a run
        is over the cap and carries an image, rather than one standing in for the
        other."""
        import base64

        from tests.helpers.images import tiny_png

        png = base64.b64encode(tiny_png(width=4, height=3)).decode()
        image_turn = [
            HumanMessage(content="overview"),
            AIMessage(
                content="",
                tool_calls=[{"name": "render", "args": {}, "id": "call_img", "type": "tool_call"}],
            ),
            ToolMessage(
                content=[
                    {"type": "text", "text": "overview"},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": png},
                    },
                ],
                tool_call_id="call_img",
            ),
            AIMessage(content="Here it is."),
        ]
        messages = _conversation(MAX_MESSAGES_PER_SESSION, with_tools=True) + image_turn
        session = ChatSession("s", "c")

        session.replace_history(messages)

        assert len(session.messages) <= MAX_MESSAGES_PER_SESSION
        kept = next(
            m
            for m in session.messages
            if isinstance(m, ToolMessage) and m.tool_call_id == "call_img"
        )
        assert kept.content[1] == {
            "type": "text",
            "text": "[image: 4x3 image/png, not retained in history]",
        }
        assert png not in str([m.content for m in session.messages])
