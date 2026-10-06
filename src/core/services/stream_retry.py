"""Running a graph's event stream again when its model call failed fast, before the reader
saw anything of it.

Bedrock sometimes ends a stream almost at once with no ``messageStop`` event, or drops the
connection part way (issue #578). botocore retries a request that fails before the
response begins (``retries`` in ``bedrock_llm._bedrock_client``), but not a stream that
fails after, so these reached the reader as an error although a second call can succeed.
Every provider's stream goes through this helper; ``classify_model_error`` decides what is
worth a second try.

A second try is safe only while nothing has happened that the first one cannot undo. The
reader has seen no text, reasoning signal, tool call or tool result, and no model call has
finished, so the run has changed nothing that matters: ``BaseAgent.build_graph`` compiles
with no checkpointer, ``_stream_chat_response`` seeds the state from a copy of the
session's messages and writes the reply back only after the stream ends (``/ask`` has no
session), and the user's message is already in the state. Past that point a failure ends
the stream with the error event, because running the turn again would repeat what the
reader already saw.

Only a failure that arrives fast, and of a kind ``ModelFailure.worth_retrying_now``
allows, is retried, and only once.
"""

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from src.core.services.model_errors import classify_model_error

logger = logging.getLogger(__name__)

#: How long to wait before the second try, on average, so a failure has a moment to clear.
#: Each wait is between half and one and a half times this, so requests that failed
#: together do not retry together.
RETRY_DELAY_SECONDS = 1.0

#: A failure that took longer than this to arrive is not retried: the reader has waited
#: that long already, and a second try could take as long again.
RETRY_WINDOW_SECONDS = 10.0

#: Events that mean a tool has started or finished, or a model call has completed. A
#: second try would repeat them, whether or not the reader was shown anything for them.
_PROGRESS_EVENTS = frozenset({"on_tool_start", "on_tool_end", "on_chat_model_end"})


@dataclass
class RetryOutcome:
    """What the helper did, for the caller to read when the stream ends or fails.

    Attributes:
        retried: True once the model call was tried a second time.
    """

    retried: bool = False


def _made_progress(event: Mapping[str, Any]) -> bool:
    """Whether this event is output a retry would repeat: a finished model call, a tool
    call or result, or a model chunk that carries text, reasoning or a tool call. A chunk
    with none of those, such as the one that opens a Bedrock stream, does not count. A
    chunk of a kind this does not recognize does, because the reader may have been shown
    it and a retry would show it twice."""
    kind = event.get("event")
    if kind in _PROGRESS_EVENTS:
        return True
    if kind == "on_chat_model_stream":
        chunk = event.get("data", {}).get("chunk")
        if not isinstance(chunk, AIMessageChunk):
            return True
        return bool(chunk.content or chunk.tool_call_chunks)
    return False


async def astream_events_with_retry(
    graph: CompiledStateGraph,
    state: dict[str, Any],
    config: dict[str, Any],
    *,
    community_id: str,
    model: str | None,
    endpoint: str,
    request_id: str | None,
    session_id: str | None = None,
    outcome: RetryOutcome,
) -> AsyncIterator[Any]:
    """Stream ``graph.astream_events``, once more if the first try fails fast before any output.

    Args:
        graph: The compiled agent graph.
        state: The turn's starting state. Treated as read-only: a run only assigns ids to
            messages that lack one, and its other writes (the agent node's tool-call list)
            come after a model call has finished, which ends the window for a retry.
        config: The run's config (callbacks and the like), reused for the second try.
        community_id: For the log.
        model: The model the request runs, for the log.
        endpoint: The endpoint, for the log.
        request_id: The request's id, for the log, which is how it is found next to the
            error the reader gets if the second try fails too.
        session_id: The chat session, for the log. None for ``/ask``, which has none.
        outcome: A fresh one per stream, which this sets when it makes the second try, and
            which the caller reads when it reports a failure that survived it.

    Yields:
        The graph's events, the same ones ``astream_events`` yields. After a retry the
        second try's events follow the first's: the first try's start events are never
        matched by an end event, and nothing marks the restart.

    Raises:
        Whatever the run raised, when it is not worth retrying, when output had already
        been made, when it took too long to arrive, or when the second try failed too.
    """
    run_config = cast("RunnableConfig", config)
    while True:
        made_progress = False
        started = time.monotonic()
        try:
            async with aclosing(
                cast("Any", graph.astream_events(state, version="v2", config=run_config))
            ) as events:
                async for event in events:
                    made_progress = made_progress or _made_progress(event)
                    yield event
            if outcome.retried:
                logger.info(
                    "The second try of %s succeeded (community=%s, model=%s, request_id=%s)",
                    endpoint,
                    community_id,
                    model,
                    request_id,
                )
            return
        except Exception as error:
            failure = classify_model_error(error)
            if (
                outcome.retried
                or made_progress
                or time.monotonic() - started > RETRY_WINDOW_SECONDS
                or not failure.worth_retrying_now
            ):
                raise
            outcome.retried = True
            logger.warning(
                "Retrying %s once after a model failure that had shown the reader nothing "
                "(community=%s, model=%s, request_id=%s, session=%s): %s [%s]: %s",
                endpoint,
                community_id,
                model,
                request_id,
                session_id,
                failure.detail,
                failure.kind,
                error,
                extra={
                    "community_id": community_id,
                    "model": model,
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "error_type": type(error).__name__,
                    "failure_kind": failure.kind,
                    "retry": True,
                },
            )
        # Reached only for a failure being retried: success returned above and every other
        # failure re-raised.
        delay = RETRY_DELAY_SECONDS * random.uniform(0.5, 1.5)  # noqa: S311
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            logger.info(
                "The reader left before the second try of %s (community=%s, request_id=%s)",
                endpoint,
                community_id,
                request_id,
            )
            raise
