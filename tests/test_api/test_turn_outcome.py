"""The bookkeeping that reads how model runs ended must never disturb a stream."""

import logging

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.api.turn_outcome import ModelRuns, cut_off_reply


class _Explodes:
    """A model run whose metadata cannot be read."""

    @property
    def response_metadata(self) -> dict:
        raise RuntimeError("unreadable")


def test_a_run_that_cannot_be_read_is_logged_not_raised(caplog) -> None:
    caplog.set_level(logging.WARNING)
    runs = ModelRuns()

    runs.note(_Explodes())

    assert runs.truncated_by is None
    assert any("Could not read how a model run ended" in r.getMessage() for r in caplog.records)


def test_a_run_with_no_metadata_is_a_finished_run() -> None:
    runs = ModelRuns()

    runs.note(None)
    runs.note(object())

    assert runs.runs == 2 and runs.truncated_by is None


def test_only_the_last_turn_of_a_history_is_this_requests() -> None:
    cut = AIMessage(content="old", response_metadata={"stopReason": "max_tokens"})
    done = AIMessage(content="new", response_metadata={"stopReason": "end_turn"})

    earlier = ModelRuns.from_messages([HumanMessage(content="q1"), cut, HumanMessage(content="q2")])
    this_turn = ModelRuns.from_messages(
        [HumanMessage(content="q1"), cut, HumanMessage(content="q2"), done]
    )
    cut_this_turn = ModelRuns.from_messages(
        [HumanMessage(content="q1"), done, HumanMessage(content="q2"), cut]
    )

    assert earlier.runs == 0 and earlier.truncated_by is None
    assert this_turn.runs == 1 and this_turn.truncated_by is None
    assert cut_this_turn.truncated_by == "max_tokens"


def test_tool_messages_are_not_runs() -> None:
    runs = ModelRuns.from_messages(
        [
            HumanMessage(content="q"),
            AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
            ToolMessage(content="result", tool_call_id="1"),
            AIMessage(content="answer", response_metadata={"finish_reason": "length"}),
        ]
    )

    assert runs.runs == 2 and runs.truncated_by == "length"


def test_a_reply_that_finished_is_left_alone() -> None:
    assert (
        cut_off_reply(
            ModelRuns(runs=1),
            reply_text="",
            code_ran=False,
            community_id="c",
            model="m",
            endpoint="/c/chat",
            request_id=None,
        )
        is None
    )
