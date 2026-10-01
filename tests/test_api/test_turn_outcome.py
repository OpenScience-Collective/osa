"""The bookkeeping that reads how model runs ended must never disturb a stream."""

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.api.turn_outcome import (
    CONTEXT_FULL_CUT_OFF_MESSAGE,
    CONTEXT_FULL_NO_ANSWER_MESSAGE,
    CUT_OFF_LONG_MESSAGE,
    CUT_OFF_MESSAGE,
    DECLINED_MESSAGE,
    EMPTY_MESSAGE,
    LONG_CONVERSATION_MESSAGE,
    MALFORMED_MESSAGE,
    NO_ANSWER_MESSAGE,
    ModelRuns,
    current_turn,
    reply_problem,
    warning_event,
)
from src.core.services.model_outcome import (
    CONTEXT_WINDOW_STOP_REASON,
    DECLINED_STOP_REASONS,
    MALFORMED_STOP_REASONS,
    TRUNCATING_STOP_REASONS,
)


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


def test_the_current_turn_is_what_follows_the_last_human_message() -> None:
    earlier = AIMessage(content="old")
    tool = ToolMessage(content="result", tool_call_id="1")
    new = AIMessage(content="new")

    assert current_turn([HumanMessage(content="q1"), earlier]) == [earlier]
    assert current_turn([HumanMessage(content="q1"), earlier, HumanMessage(content="q2")]) == []
    assert current_turn(
        [HumanMessage(content="q1"), earlier, HumanMessage(content="q2"), new, tool]
    ) == [
        new,
        tool,
    ]
    assert current_turn([]) == []


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


def _problem(runs: ModelRuns, reply_text: str = "", *, code_ran: bool = False):
    return reply_problem(
        runs,
        reply_text=reply_text,
        code_ran=code_ran,
        community_id="c",
        model="m",
        endpoint="/c/chat",
        request_id=None,
    )


def _ended_on(reason: str | None) -> ModelRuns:
    runs = ModelRuns()
    runs.note(AIMessage(content="", response_metadata={"stopReason": reason} if reason else {}))
    return runs


def test_a_reply_that_finished_with_text_is_left_alone() -> None:
    assert _problem(_ended_on("end_turn"), "An answer.") is None
    assert _problem(_ended_on("refusal"), "I can help with part of that.") is None


def test_a_reply_that_ran_code_is_left_alone_even_with_no_text() -> None:
    assert _problem(_ended_on("end_turn"), code_ran=True) is None


class TestAnEmptyReply:
    """No text, no code, no parked call: an error whatever the stop reason says, since the
    widget drops a reply with nothing in it and the reader would be told nothing."""

    @pytest.mark.parametrize("reason", sorted(DECLINED_STOP_REASONS))
    def test_one_the_model_declined_says_so(self, reason: str) -> None:
        problem = _problem(_ended_on(reason))

        assert problem.event == "error"
        assert problem.message == DECLINED_MESSAGE
        assert problem.reason == reason and reason in problem.summary

    @pytest.mark.parametrize("reason", sorted(MALFORMED_STOP_REASONS))
    def test_a_malformed_one_says_so(self, reason: str) -> None:
        problem = _problem(_ended_on(reason))

        assert (problem.event, problem.message) == ("error", MALFORMED_MESSAGE)

    @pytest.mark.parametrize("reason", ["end_turn", "stop", "tool_use", "something_new", None])
    def test_any_other_is_still_an_error(self, reason: str | None) -> None:
        problem = _problem(_ended_on(reason))

        assert (problem.event, problem.message) == ("error", EMPTY_MESSAGE)
        assert problem.reason == reason
        assert (reason or "none") in problem.summary

    def test_no_model_run_at_all_is_one_too(self) -> None:
        assert _problem(ModelRuns()).message == EMPTY_MESSAGE

    def test_whitespace_is_nothing(self) -> None:
        assert _problem(_ended_on("end_turn"), " \n\n").event == "error"

    @pytest.mark.parametrize("reason", sorted(TRUNCATING_STOP_REASONS))
    def test_one_that_hit_a_limit_keeps_its_own_message(self, reason: str) -> None:
        runs = ModelRuns()
        runs.note(AIMessage(content="", response_metadata={"finish_reason": reason}))

        problem = _problem(runs)

        expected = (
            CONTEXT_FULL_NO_ANSWER_MESSAGE
            if reason == CONTEXT_WINDOW_STOP_REASON
            else NO_ANSWER_MESSAGE
        )
        assert (problem.event, problem.message) == ("error", expected)


class TestACutOffReplyWithText:
    @pytest.mark.parametrize("reason", sorted(TRUNCATING_STOP_REASONS))
    def test_is_a_warning_worded_for_what_ran_out(self, reason: str) -> None:
        runs = ModelRuns()
        runs.note(AIMessage(content="", response_metadata={"finish_reason": reason}))

        problem = _problem(runs, "An answer that stops sho")

        expected = (
            CONTEXT_FULL_CUT_OFF_MESSAGE
            if reason == CONTEXT_WINDOW_STOP_REASON
            else CUT_OFF_MESSAGE
        )
        assert (problem.event, problem.message) == ("warning", expected)


class TestWhatAFullContextWindowSays:
    """Asking again, or asking it to continue, adds to a conversation that is already too
    long, so the copy sends the reader to a new conversation instead."""

    @pytest.mark.parametrize(
        "message", [CONTEXT_FULL_NO_ANSWER_MESSAGE, CONTEXT_FULL_CUT_OFF_MESSAGE]
    )
    def test_it_does_not_offer_what_fails_again(self, message: str) -> None:
        lowered = message.lower()

        assert "new conversation" in lowered
        assert "try again" not in lowered
        assert "ask it to continue" not in lowered
        assert "narrower" not in lowered

    def test_the_output_limit_copy_still_offers_a_retry(self) -> None:
        assert "try again" in NO_ANSWER_MESSAGE.lower()
        assert "continue" in CUT_OFF_MESSAGE.lower()


def test_the_last_runs_stop_reason_is_kept_whatever_it_is() -> None:
    runs = ModelRuns()
    runs.note(AIMessage(content="", response_metadata={"stop_reason": "tool_use"}))
    runs.note(AIMessage(content="", response_metadata={"stop_reason": "refusal"}))

    assert runs.stop_reason == "refusal" and runs.truncated_by is None

    runs.note(AIMessage(content="", response_metadata={}))

    assert runs.stop_reason is None


class TestTheWarningEvent:
    """One event for a finished reply, whatever it has to say (release review, follow-up 1)."""

    @staticmethod
    def _cut_off(reason: str = "max_tokens"):
        runs = ModelRuns()
        runs.note(AIMessage(content="", response_metadata={"stopReason": reason}))
        return _problem(runs, "An answer that stops sho")

    def test_nothing_to_say_is_no_event(self) -> None:
        assert warning_event(None, conversation_is_long=False) is None

    def test_an_error_is_not_a_warning(self) -> None:
        error = _problem(_ended_on("refusal"))

        assert warning_event(error, conversation_is_long=False) is None

    def test_an_error_does_not_turn_a_long_conversation_into_a_cut_off_one(self) -> None:
        error = _problem(_ended_on("refusal"))

        event = warning_event(error, conversation_is_long=True)

        assert event == {
            "event": "warning",
            "message": LONG_CONVERSATION_MESSAGE,
            "code": "long_conversation",
        }

    def test_a_long_conversation_alone(self) -> None:
        assert warning_event(None, conversation_is_long=True) == {
            "event": "warning",
            "message": LONG_CONVERSATION_MESSAGE,
            "code": "long_conversation",
        }

    def test_a_cut_off_reply_alone(self) -> None:
        assert warning_event(self._cut_off(), conversation_is_long=False) == {
            "event": "warning",
            "message": CUT_OFF_MESSAGE,
            "code": "cut_off",
        }

    def test_both_are_one_event_that_says_both(self) -> None:
        event = warning_event(self._cut_off(), conversation_is_long=True)

        assert event == {
            "event": "warning",
            "message": CUT_OFF_LONG_MESSAGE,
            "code": "cut_off",
            "codes": ["cut_off", "long_conversation"],
        }

    def test_the_combined_message_holds_what_each_alone_would_have_said(self) -> None:
        lowered = CUT_OFF_LONG_MESSAGE.lower()

        assert "cut off" in lowered and "length limit" in lowered
        assert "conversation is getting long" in lowered and "new chat" in lowered

    def test_a_full_context_window_already_says_to_start_over(self) -> None:
        event = warning_event(self._cut_off(CONTEXT_WINDOW_STOP_REASON), conversation_is_long=True)

        assert event is not None
        assert event["message"] == CONTEXT_FULL_CUT_OFF_MESSAGE
        assert event["codes"] == ["cut_off", "long_conversation"]

    def test_it_is_always_json(self) -> None:
        import json

        for problem in (None, self._cut_off()):
            for long in (False, True):
                event = warning_event(problem, conversation_is_long=long)
                assert event is None or json.loads(json.dumps(event)) == event
