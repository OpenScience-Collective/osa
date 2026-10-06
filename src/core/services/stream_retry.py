"""Running a graph's event stream again when its model call failed before the reader saw
anything of it.

Bedrock sometimes closes a stream almost at once with no ``messageStop`` event (GPT-6
Luna did, and Bedrock's own metrics counted those calls as successes; issue 578), or
answers a throttle, or drops the connection. botocore retries a request that fails to
start, but not one whose stream was cut after the response began, so each of those
reached the reader as an error although the very next call would have worked.

A second try is safe only while nothing has happened that the first one cannot undo. The
reader has seen no text, no tool call and no tool result, and no model call has finished,
so the graph has changed nothing (it runs with no checkpointer, and the caller builds its
state from the session without writing to it until the reply is complete). Past that
point a failure ends the stream as before, because running the turn again would repeat
what the reader already saw.

Only failures that fail fast are retried, and only once (``ModelFailure.worth_retrying_now``):
a read timeout has spent the whole timeout already, and a second one would double the wait.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from src.core.services.model_errors import classify_model_error

logger = logging.getLogger(__name__)

#: How long to wait before the second try, so a throttle has a moment to clear.
RETRY_DELAY_SECONDS = 1.0

#: Events a model or tool run passes up once it has made progress that a second try would
#: repeat, whatever the reader was shown for it.
_PROGRESS_EVENTS = frozenset({"on_tool_start", "on_tool_end", "on_chat_model_end"})


def _made_progress(event: Mapping[str, Any]) -> bool:
    """Whether this event is output a retry would repeat: a finished model call, a tool
    call or result, or a model chunk that carries text, reasoning or a tool call. The
    first chunk of a stream, which carries only the message's start, does not count."""
    kind = event.get("event")
    if kind in _PROGRESS_EVENTS:
        return True
    if kind == "on_chat_model_stream":
        chunk = event.get("data", {}).get("chunk")
        return bool(getattr(chunk, "content", None) or getattr(chunk, "tool_call_chunks", None))
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
) -> AsyncIterator[Any]:
    """Stream ``graph.astream_events``, once more if the first try fails fast before any output.

    Args:
        graph: The compiled agent graph.
        state: The turn's starting state. Not changed by a run, so the second try starts
            from the same place.
        config: The run's config (callbacks and the like), reused for the second try.
        community_id: For the log.
        model: The model the request runs, for the log.
        endpoint: The endpoint, for the log.
        request_id: The request's id, for the log, which is how it is found next to the
            error the reader gets if the second try fails too.

    Yields:
        The graph's events, the same ones ``astream_events`` yields. After a retry the
        first try's events (which carried no output) are not repeated or undone.

    Raises:
        Whatever the run raised, when it is not worth retrying, when output had already
        been made, or when the second try failed too.
    """
    run_config = cast("RunnableConfig", config)
    retried = False
    while True:
        made_progress = False
        try:
            async for event in graph.astream_events(state, version="v2", config=run_config):
                made_progress = made_progress or _made_progress(event)
                yield event
            return
        except Exception as error:
            failure = classify_model_error(error)
            if retried or made_progress or not failure.worth_retrying_now:
                raise
            retried = True
            logger.warning(
                "Retrying %s once after a model failure that had shown the reader nothing "
                "(community=%s, model=%s, request_id=%s): %s",
                endpoint,
                community_id,
                model,
                request_id,
                failure.detail,
                extra={
                    "community_id": community_id,
                    "model": model,
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "failure_kind": failure.kind,
                    "retry": True,
                },
            )
        await asyncio.sleep(RETRY_DELAY_SECONDS)
