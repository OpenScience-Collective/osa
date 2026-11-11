"""The `handover` events of a turn that moves from the quick model to the analysis model.

The decisions and the summary text are in `src.agents.model_routing`; this is the
sequence the reader's client sees, in order:

    {"event": "handover", "stage": "summarizing"}
    {"event": "handover", "stage": "summarized", "messages": N, "words": W, "summary": "...",
     "degraded": true}     (`degraded` only when the summary is the reader's own words)
    {"event": "handover", "stage": "started", "session_id": "...", "model": "..."}

and then the analysis run on the new session, streamed through the ordinary path, so its
own `session` event and its `done.session_id` carry the new id. A client that predates
these events ignores them and adopts the new id from `session` and `done`, as it always
has.

The old session is not touched. It keeps the reader's message and whatever came before,
with no tool call left dangling, so it stays valid on the quick model.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import aclosing
from typing import TYPE_CHECKING, Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.agents.model_routing import (
    Summary,
    count_words,
    message_text,
    seeded_message,
    summarize_history,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from src.api.routers.community import ChatSession


_SESSION_BUSY = "The new analysis session is already in use; please try again."


def handover_sse(stage: str, **fields: Any) -> str:
    """One `handover` event, framed as a Server-Sent Event."""
    return f"data: {json.dumps({'event': 'handover', 'stage': stage, **fields})}\n\n"


def conversation_size(history: Sequence[BaseMessage]) -> int:
    """How many messages of the earlier conversation the summary stands for.

    Counted as the reader sees a conversation: their messages and the assistant's replies,
    not the tool calls and results in between.
    """
    return sum(1 for m in history if m.type in ("human", "ai") and message_text(m))


async def stream_handover(
    *,
    summarizer: BaseChatModel,
    history: Sequence[BaseMessage],
    reader_message: str,
    open_session: Callable[[], ChatSession],
    analysis_model: str,
    run_analysis: Callable[[ChatSession], AsyncGenerator[str, None]],
    summarizer_config: Any | None = None,
    record_summary: Callable[[Summary], None] | None = None,
) -> AsyncIterator[str]:
    """Summarize `history`, open a new analysis session from it, and stream the run on it.

    Args:
        summarizer: The quick model, without tools.
        history: The session's messages before the reader's latest one. Not empty: a first
            message needs no summary and flips its own session instead.
        reader_message: The reader's latest message, which the analysis model answers.
        open_session: Makes the new session (`get_or_create_session(community_id, None)`).
            It must return a fresh, unclaimed session; a session that another turn holds
            is refused with an `error` event.
        analysis_model: The model the analysis run uses, for the `started` event.
        run_analysis: Streams the analysis turn on the new session, which already holds
            its one seeded message and whose turn is already claimed.
        summarizer_config: The runnable config (LangFuse callbacks) the summary call runs
            under, so it is traced with the request.
        record_summary: Called with the `Summary` as soon as it is written, so the caller
            can account for the summarizer's tokens.
    """
    yield handover_sse("summarizing")

    summary = await summarize_history(summarizer, history, summarizer_config)
    if record_summary is not None:
        record_summary(summary)
    yield handover_sse(
        "summarized",
        messages=conversation_size(history),
        words=count_words(summary.text),
        summary=summary.text,
        **({"degraded": True} if summary.degraded else {}),
    )

    session = open_session()
    session.mode = "analysis"
    session.messages = [HumanMessage(content=seeded_message(summary.text, reader_message))]
    # The new session's turn is claimed here, before its id is sent, and handed to the
    # analysis run, which does not claim again: it sends its own `session` event first,
    # and a claim made after that would leave the session open to a second request for as
    # long as the client takes to read the event. Released however this ends, including
    # a client that leaves before the run starts.
    if not session.begin_turn():
        yield f"data: {json.dumps({'event': 'error', 'message': _SESSION_BUSY})}\n\n"
        return
    try:
        yield handover_sse("started", session_id=session.session_id, model=analysis_model)
        async with aclosing(run_analysis(session)) as stream:
            async for line in stream:
                yield line
    finally:
        session.end_turn()
