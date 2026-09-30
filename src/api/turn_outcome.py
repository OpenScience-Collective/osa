"""How a request's model runs ended, and what they reported about their cost.

A reply can go wrong without raising. The model stops at its output limit and the
stream ends normally with whatever fitted, which on a reasoning model can be nothing
(ADR 0014 records the budget); or the provider never says how many tokens a run used,
and the cost row is empty or an estimate. Neither is an exception, so neither reaches
the error handlers: this module is where the router reads them off the model's own
end-of-run message, says so in the log once per request, and, for a reply that was cut
off, decides what the reader is told.

``ModelRuns`` is filled from each finished model run (an ``on_chat_model_end`` event's
output when streaming, the messages of the final state when not).
``cut_off_reply`` and ``ModelRuns.warn_about_usage`` are called once, when the request
is finished.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.core.services.model_outcome import USAGE_ESTIMATED_KEY, truncation_reason

logger = logging.getLogger(__name__)

#: Told to the reader when the model stopped at its limit with nothing to show: no text
#: and no code run. It is an error, since the reply is not there.
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
    """

    runs: int = 0
    without_usage: int = 0
    estimated_usage: int = 0
    truncated_by: str | None = None

    def note(self, message: Any) -> None:
        """Record one finished model run. Never raises: this is bookkeeping, and a
        stream must not fail over it."""
        try:
            self.runs += 1
            metadata = getattr(message, "response_metadata", None)
            self.truncated_by = truncation_reason(metadata)
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
class CutOff:
    """A reply the model stopped at its limit, and how to tell the reader.

    Attributes:
        event: ``error`` when there is no answer to show, ``warning`` when there is one
            and it stopped short. Both are event types the widget and the CLI handle.
        message: What the reader is told.
        reason: The stop reason the provider gave.
    """

    event: Literal["error", "warning"]
    message: str
    reason: str

    @property
    def summary(self) -> str:
        """What the metrics row says, for an operator reading it."""
        return f"the model reached its output limit with no answer (stop reason {self.reason})"


def cut_off_reply(
    runs: ModelRuns,
    *,
    reply_text: str,
    code_ran: bool,
    community_id: str,
    model: str | None,
    endpoint: str,
    request_id: str | None,
) -> CutOff | None:
    """Decide what a reader is told about a reply whose last model run hit its limit.

    Logs a warning (community, model, request id, the provider's stop reason) whenever
    the last run was cut off, and returns what to send, or None for a reply that ended
    on its own.

    Args:
        runs: The request's model runs.
        reply_text: The text the reader was shown for this turn.
        code_ran: Whether the reply already ran code in the browser. A reply that ran
            code is kept by the widget even with no text, so it is not an empty one.
        community_id: For the log.
        model: The model that ran, for the log.
        endpoint: The endpoint, for the log.
        request_id: The request's id, for the log and for finding it in the metrics.
    """
    reason = runs.truncated_by
    if reason is None:
        return None
    has_answer = bool(reply_text.strip()) or code_ran
    outcome = CutOff(
        event="warning" if has_answer else "error",
        message=CUT_OFF_MESSAGE if has_answer else NO_ANSWER_MESSAGE,
        reason=reason,
    )
    logger.warning(
        "Model reply was cut off at its output limit for %s (community=%s, model=%s, "
        "request_id=%s, stop_reason=%s): %s",
        endpoint,
        community_id,
        model,
        request_id,
        reason,
        "the reader got no answer" if outcome.event == "error" else "the answer stops short",
        extra={
            "community_id": community_id,
            "model": model,
            "request_id": request_id,
            "endpoint": endpoint,
            "stop_reason": reason,
            "answer_chars": len(reply_text.strip()),
            "reader_told": outcome.event,
        },
    )
    return outcome
