"""How a request's model runs ended, and what they reported about their cost.

A reply can go wrong without raising. The model stops at its output limit and the
stream ends normally with whatever fitted, which on a reasoning model can be nothing
(ADR 0014 records the budget); it declines to answer, or writes nothing for some other
reason its stop reason names; or the provider never says how many tokens a run used, and
the cost row is empty or an estimate. None of these is an exception, so none reaches the
error handlers: this module is where the router reads them off the model's own end-of-run
message, says so in the log once per request, and decides what the reader is told.

``ModelRuns`` is filled from each finished model run (an ``on_chat_model_end`` event's
output when streaming, the messages of the final state when not).
``reply_problem`` and ``ModelRuns.warn_about_usage`` are called once, when the request
is finished.
"""

import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.core.services.model_outcome import (
    CONTEXT_WINDOW_STOP_REASON,
    DECLINED_STOP_REASONS,
    MALFORMED_STOP_REASONS,
    USAGE_ESTIMATED_KEY,
    truncation_reason,
)
from src.core.services.model_outcome import stop_reason as stop_reason_of

logger = logging.getLogger(__name__)

#: Told to the reader when the model stopped at its output limit with nothing to show: no
#: text and no code run. It is an error, since the reply is not there.
NO_ANSWER_MESSAGE = (
    "The assistant ran out of room to write before it produced an answer, so there is "
    "nothing to show. Try again, or ask a narrower question."
)

#: Told to the reader when what they were shown stopped short. It is a warning, since
#: the text is there.
CUT_OFF_MESSAGE = (
    "This answer was cut off because the assistant reached its length limit. "
    "Ask it to continue, or ask a narrower question."
)

#: The same two for a conversation that filled the model's context window. Asking again or
#: asking it to continue adds to what is already too long, so neither is offered.
CONTEXT_FULL_NO_ANSWER_MESSAGE = (
    "This conversation has grown past what the assistant can read at once, so it could "
    "not answer. Start a new conversation."
)
CONTEXT_FULL_CUT_OFF_MESSAGE = (
    "This answer stopped short because the conversation has grown past what the "
    "assistant can read at once. Start a new conversation to continue."
)

#: Told to the reader when the conversation is nearing the token budget the agent trims
#: to. On its own it is a warning on a reply that is otherwise fine.
LONG_CONVERSATION_MESSAGE = (
    "Conversation is getting long. Consider starting a new chat for best results."
)

#: The one warning a reply gets when it was cut off at its output limit and the
#: conversation is also getting long. The widget keeps a single warning element, so two
#: warnings in a row would show only the second, and a cut-off answer is likeliest in
#: exactly the conversations that are long.
CUT_OFF_LONG_MESSAGE = (
    "This answer was cut off because the assistant reached its length limit, and the "
    "conversation is getting long. Start a new chat and ask a narrower question."
)

#: ``code`` of a ``warning`` event, so a client can act on the kind of warning without
#: reading the text: mark a cut-off reply in place, say.
WARNING_CUT_OFF = "cut_off"
WARNING_LONG_CONVERSATION = "long_conversation"

#: Told to the reader when the model wrote nothing for a reason that is not running out of
#: room. Each is an error: no text and no code run, so there is no reply to show.
DECLINED_MESSAGE = "The assistant declined to answer this request. Try rephrasing your question."
MALFORMED_MESSAGE = "The assistant's reply was malformed, so there is nothing to show. Try again."
EMPTY_MESSAGE = (
    "The assistant finished without writing an answer. Try again, or rephrase your question."
)


def _reports_usage(message: Any) -> bool:
    """Whether a finished model run says how many tokens it used."""
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        return False
    return (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0) > 0


@dataclass
class ModelRuns:
    """What a request's model runs said about how they ended and what they used.

    A turn can run the model several times (every tool call is another run). The reader's
    answer is the last run's, so ``truncated_by`` is the last run's; the counts cover all.

    Attributes:
        runs: Model runs that finished.
        without_usage: Runs that reported no tokens, so the cost row leaves them out.
        estimated_usage: Runs whose usage is LiteLLM's estimate, not the provider's.
        truncated_by: The stop reason of the last run if it was cut off, else None.
        stop_reason: The stop reason of the last run, whatever it is, or None when the
            run gave none.
    """

    runs: int = 0
    without_usage: int = 0
    estimated_usage: int = 0
    truncated_by: str | None = None
    stop_reason: str | None = None

    def note(self, message: Any) -> None:
        """Record one finished model run. Never raises: this is bookkeeping, and a
        stream must not fail over it."""
        try:
            self.runs += 1
            metadata = getattr(message, "response_metadata", None)
            self.truncated_by = truncation_reason(metadata)
            self.stop_reason = stop_reason_of(metadata)
            if not _reports_usage(message):
                self.without_usage += 1
            elif isinstance(metadata, Mapping) and metadata.get(USAGE_ESTIMATED_KEY):
                self.estimated_usage += 1
        except Exception:
            logger.warning("Could not read how a model run ended", exc_info=True)

    @classmethod
    def from_messages(cls, messages: Iterable[BaseMessage]) -> "ModelRuns":
        """The runs of the last turn in a finished graph state, for a request that was
        not streamed: the assistant messages after the last human one (earlier ones are
        history, and were not this request's runs)."""
        turn: list[BaseMessage] = []
        for message in messages:
            if isinstance(message, HumanMessage):
                turn = []
            else:
                turn.append(message)
        runs = cls()
        for message in turn:
            if isinstance(message, AIMessage):
                runs.note(message)
        return runs

    def warn_about_usage(
        self, *, community_id: str, model: str | None, endpoint: str, request_id: str | None
    ) -> None:
        """Say once, at the end of a request, that its cost row is incomplete.

        A run with no reported tokens adds nothing to the row (all of them: a NULL cost),
        and an estimated one prices without the cache and reasoning counts. Neither
        raises, so without this nothing shows it.
        """
        if not (self.without_usage or self.estimated_usage):
            return
        if self.without_usage == self.runs:
            consequence = "missing (NULL)"
        elif self.without_usage:
            consequence = "too low, since those runs are left out"
        else:
            consequence = "approximate, since the estimate has no cache or reasoning counts"
        logger.warning(
            "Token usage is incomplete for %s (community=%s, model=%s, request_id=%s): "
            "%d of %d model runs reported none and %d used LiteLLM's estimate, so the "
            "cost recorded for this request is %s",
            endpoint,
            community_id,
            model,
            request_id,
            self.without_usage,
            self.runs,
            self.estimated_usage,
            consequence,
            extra={
                "community_id": community_id,
                "model": model,
                "request_id": request_id,
                "endpoint": endpoint,
                "model_runs": self.runs,
                "runs_without_usage": self.without_usage,
                "runs_with_estimated_usage": self.estimated_usage,
            },
        )


@dataclass(frozen=True)
class ReplyProblem:
    """A reply that reached the reader short or empty, and how to tell them.

    Attributes:
        event: ``error`` when there is no answer to show, ``warning`` when there is one
            and it stopped short. Both are event types the widget and the CLI handle.
        message: What the reader is told.
        reason: The stop reason the provider gave, or None when it gave none.
        summary: What the metrics row says, for an operator reading it.
        error_id: For an error, the id its log line carries, so a reader's report finds it.
            None for a warning.
    """

    event: Literal["error", "warning"]
    message: str
    reason: str | None
    summary: str
    error_id: str | None = None


def _cut_off(reason: str, *, has_answer: bool) -> tuple[ReplyProblem, str]:
    """The problem of a reply the model stopped at a limit, and what the log calls it."""
    if reason == CONTEXT_WINDOW_STOP_REASON:
        message = CONTEXT_FULL_CUT_OFF_MESSAGE if has_answer else CONTEXT_FULL_NO_ANSWER_MESSAGE
        cause = "the conversation exceeded the model's context window"
        headline = "Model reply was cut off because the conversation filled the context window"
    else:
        message = CUT_OFF_MESSAGE if has_answer else NO_ANSWER_MESSAGE
        cause = "the model reached its output limit"
        headline = "Model reply was cut off at its output limit"
    problem = ReplyProblem(
        event="warning" if has_answer else "error",
        message=message,
        reason=reason,
        summary=f"{cause} with no answer (stop reason {reason})",
    )
    return problem, headline


def _empty(reason: str | None) -> tuple[ReplyProblem, str]:
    """The problem of a reply with no text, no code and no parked call that was not cut off:
    the model declined, wrote something malformed, or simply ended with nothing."""
    if reason in DECLINED_STOP_REASONS:
        message, cause = DECLINED_MESSAGE, "the model declined to answer"
    elif reason in MALFORMED_STOP_REASONS:
        message, cause = MALFORMED_MESSAGE, "the model's reply was malformed"
    else:
        message, cause = EMPTY_MESSAGE, "the model wrote no answer"
    problem = ReplyProblem(
        event="error",
        message=message,
        reason=reason,
        summary=f"{cause} (stop reason {reason or 'none'})",
    )
    return problem, "Model reply was empty"


def reply_problem(
    runs: ModelRuns,
    *,
    reply_text: str,
    code_ran: bool,
    community_id: str,
    model: str | None,
    endpoint: str,
    request_id: str | None,
) -> ReplyProblem | None:
    """Decide what a reader is told about a reply that is short or empty.

    Two things make a reply a problem. Its last model run hit a limit (the output budget or
    the context window): a warning when there is text to show, an error when there is not.
    Or it has no text, ran no code and parked no call, whatever the stop reason says: the
    widget drops a bubble with nothing in it, so the reader would see neither an answer nor
    a reason. The caller handles a parked call before it gets here.

    Logs a warning (community, model, request id, the provider's stop reason) whenever
    it finds one, and returns what to send, or None for a reply that is fine.

    Args:
        runs: The request's model runs.
        reply_text: The text the reader was shown for this turn.
        code_ran: Whether the reply already ran code the widget keeps. A reply that ran
            such code is kept by the widget even with no text, so it is not an empty one.
        community_id: For the log.
        model: The model that ran, for the log.
        endpoint: The endpoint, for the log.
        request_id: The request's id, for the log and for finding it in the metrics.
    """
    has_answer = bool(reply_text.strip()) or code_ran
    if runs.truncated_by is not None:
        problem, headline = _cut_off(runs.truncated_by, has_answer=has_answer)
    elif has_answer:
        return None
    else:
        problem, headline = _empty(runs.stop_reason)
    if problem.event == "error":
        problem = replace(problem, error_id=str(uuid.uuid4()))
    logger.warning(
        "%s for %s (community=%s, model=%s, request_id=%s, stop_reason=%s, error_id=%s): %s",
        headline,
        endpoint,
        community_id,
        model,
        request_id,
        problem.reason,
        problem.error_id,
        "the reader got no answer" if problem.event == "error" else "the answer stops short",
        extra={
            "community_id": community_id,
            "model": model,
            "request_id": request_id,
            "endpoint": endpoint,
            "stop_reason": problem.reason,
            "error_id": problem.error_id,
            "answer_chars": len(reply_text.strip()),
            "reader_told": problem.event,
        },
    )
    return problem


def error_event(problem: ReplyProblem, *, request_id: str | None) -> dict[str, Any]:
    """The ``error`` event for a reply that has nothing to show.

    Carries the ``request_id`` (the key of the request's row in the metrics, where the
    502 is recorded) and the ``error_id`` (the key of its log line), so a reader's report
    can be tied to both. ``message`` is what to show; the ids are for a report.
    """
    return {
        "event": "error",
        "message": problem.message,
        "error_id": problem.error_id,
        "request_id": request_id,
    }


def warning_event(
    problem: ReplyProblem | None, *, conversation_is_long: bool
) -> dict[str, Any] | None:
    """The one ``warning`` event a finished reply gets, or None when it needs none.

    A reply can need two: it was cut off (``problem``, a warning-level one), and the
    conversation is getting long. The widget keeps a single warning element, so a second
    event overwrites the first before it can be read. One event carries both instead.

    Args:
        problem: What ``reply_problem`` found, or None. An error-level one is not a warning
            and is ignored here: the caller ends the stream on it.
        conversation_is_long: Whether the conversation is near the token budget.

    Returns:
        ``{"event": "warning", "message": ..., "code": ...}``. ``code`` names the kind
        (``cut_off`` or ``long_conversation``); when both apply it is ``cut_off``, the one
        about the reply itself, and ``codes`` lists both. ``message`` is what the reader is
        told, as before; a client that ignores ``code`` and ``codes`` works unchanged.
    """
    cut_off = problem if problem is not None and problem.event == "warning" else None
    if cut_off is not None and conversation_is_long:
        return {
            "event": "warning",
            # A conversation that overflowed the context window already says to start a
            # new one; the long-conversation note would repeat it.
            "message": (
                cut_off.message
                if cut_off.reason == CONTEXT_WINDOW_STOP_REASON
                else CUT_OFF_LONG_MESSAGE
            ),
            "code": WARNING_CUT_OFF,
            "codes": [WARNING_CUT_OFF, WARNING_LONG_CONVERSATION],
        }
    if cut_off is not None:
        return {"event": "warning", "message": cut_off.message, "code": WARNING_CUT_OFF}
    if conversation_is_long:
        return {
            "event": "warning",
            "message": LONG_CONVERSATION_MESSAGE,
            "code": WARNING_LONG_CONVERSATION,
        }
    return None
