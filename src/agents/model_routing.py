"""Quick and analysis model routing, and the handover that moves a conversation between them.

NEMAR answers most turns on a small, fast model; only code, plots and analysis need a
larger one. A community opts in with the pair of keys `quick_model` and `analysis_model`.

Switching models inside one server session is not an option: the conversation holds the
quick model's thinking blocks, which the analysis model's checks reject (issue #522). So
a conversation reaches the analysis model through a handover. The quick model compacts the
chat into a short summary, a NEW session is seeded with that summary and the reader's
message, and the analysis model answers on it. The old session is left as it was, valid
and still on the quick model.

This module holds the decisions and the text: which turns are routed, which tier a turn
takes, the prompt the quick model is given, the summary prompt and the limit enforced on
what comes back. The streaming and the events are in `src.api.handover`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.agents.content import extract_text
from src.core.config.community import EXECUTE_CODE_TOOL_NAME, START_ANALYSIS_TOOL_NAME
from src.tools.client_tools import client_tools_disabled

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from src.core.config.community import CommunityConfig

Tier = Literal["quick", "analysis"]
"""Which of a community's two models a turn runs on. A session's `mode` is one of these."""

DEFAULT_TIER: Tier = "quick"

SUMMARY_MAX_WORDS = 150
"""The most words a handover summary may have. Enforced by `limit_words`, not just asked for."""

SUMMARY_INPUT_MAX_CHARS = 12_000
"""How much of the earlier conversation the summarizer reads, newest text kept.

A conversation can run to hundreds of messages; a summary of 150 words needs the recent
turns and the dataset the reader settled on, not the whole record.
"""

# Whole words only, so "plot" does not match "plotted" by accident and "run" does not match
# "trunk". The inflections a request actually takes ("plots", "running", "analyzing") are
# written out rather than matched by a prefix, which would catch "fitness" and "runner".
_ANALYSIS_WORDS = (
    r"runs?|ran|running|"
    r"plots?|plotted|plotting|"
    r"analy[sz]e[sd]?|analy[sz]ing|"
    r"compute[sd]?|computing|"
    r"execute[sd]?|executing|"
    r"spectrum|spectra|"
    r"erp\s+images?|"
    r"filter(?:s|ed|ing)?|"
    r"fit|fits|fitted|fitting"
)
_ANALYSIS_PATTERN = re.compile(rf"\b(?:{_ANALYSIS_WORDS})\b", re.IGNORECASE)


def wants_analysis(message: str) -> bool:
    """Whether a message asks to run code, plot or analyze, judged by its words alone.

    A keyword check, deliberately cheap and deliberately generous: a false positive costs
    one handover to the larger model, and a false negative is caught by the quick model,
    which has `start_analysis` for exactly the request this misses.
    """
    return _ANALYSIS_PATTERN.search(message) is not None


QUICK_MODE_INSTRUCTIONS = f"""\
## Running code and plots

You cannot run code, make plots or analyze recordings in this conversation, and you must
not write a recipe for the reader to run themselves. When the reader asks you to run
something, plot something, analyze a recording or compute a result, call the
`{START_ANALYSIS_TOOL_NAME}` tool and write nothing else: calling it ends your turn, and a
different assistant, which can run code in the reader's browser, answers them. For
everything else (finding datasets, describing them, explaining concepts and tools),
answer as you normally do."""
"""The prompt section a quick turn gets, passed as `additional_instructions`.

Not part of the YAML prompt: it must exist exactly when `start_analysis` is bound, and a
prompt that told the model to call a tool it was not given would send it to invent one. It
also overrides the YAML's "give the recipe" line, which is right when no model can run
code and wrong when one can.
"""

SUMMARY_SYSTEM_PROMPT = f"""\
You compact a conversation between a researcher and an assistant so that a second
assistant can carry it on. Write a summary of at most {SUMMARY_MAX_WORDS} words, as plain
prose with no headings and no lists. Keep every dataset identifier the researcher chose or
discussed exactly as written, and keep the researcher's goal. Leave out search result
lists, tool output, links and pleasantries. Write only the summary."""


def routing_applies(
    config: CommunityConfig | None,
    declared_client_tools: set[str] | None,
    requested_model: str | None,
) -> bool:
    """Whether a request's turns are routed between the quick and the analysis model.

    All of: the community configures both models; the request declares `execute_code`,
    because a client that cannot run code gets nothing from the analysis model; the
    request names no `model`, because a reader's own choice wins; and the client-tool kill
    switch is off, because with it on no tier could be given the tool that justifies it.
    """
    if config is None or not (config.quick_model and config.analysis_model):
        return False
    if requested_model:
        return False
    if EXECUTE_CODE_TOOL_NAME not in (declared_client_tools or set()):
        return False
    return not client_tools_disabled()


def tier_for_turn(
    config: CommunityConfig | None,
    declared_client_tools: set[str] | None,
    requested_model: str | None,
    *,
    session_mode: Tier,
    message: str | None,
    resuming: bool = False,
) -> Tier | None:
    """The tier a turn runs on, or None when the request is not routed.

    A resume always runs on its session's tier: it continues a turn that is already on a
    model, and a different one would meet that model's tool call in history. Otherwise a
    turn is analysis when the session already is (sticky, until a new session starts), or
    the message asks for it, and quick when neither does. A quick turn can still become an
    analysis one when the quick model calls `start_analysis`; that is decided in the stream.
    """
    if not routing_applies(config, declared_client_tools, requested_model):
        return None
    if resuming:
        return session_mode
    if session_mode == "analysis" or (message is not None and wants_analysis(message)):
        return "analysis"
    return "quick"


def reader_turn(messages: Sequence[BaseMessage]) -> tuple[str, list[BaseMessage]]:
    """Split a session's messages into the reader's latest message and what came before.

    The latest message is the last `HumanMessage` (a tool result is not one). Everything
    before it is the earlier conversation a handover summarizes; it is empty on a first
    message, which needs no summary.
    """
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return message_text(messages[index]), list(messages[:index])
    return "", list(messages)


def message_text(message: BaseMessage) -> str:
    """The text of a message and nothing else: no tool call, tool result, image or thinking."""
    return extract_text(message.content).strip()


def history_transcript(
    messages: Iterable[BaseMessage], max_chars: int = SUMMARY_INPUT_MAX_CHARS
) -> str:
    """The earlier conversation as plain text, built from Human and AI text only.

    Tool messages, tool calls and images are left out: the summarizer needs what was said,
    and the machine output between the lines is what would crowd the limit and, in the case
    of a figure, cannot be sent as text at all. The newest text is kept when it has to be cut.
    """
    lines: list[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            speaker = "Researcher"
        elif isinstance(message, AIMessage):
            speaker = "Assistant"
        else:
            continue
        text = message_text(message)
        if text:
            lines.append(f"{speaker}: {text}")
    transcript = "\n\n".join(lines)
    return transcript[-max_chars:] if len(transcript) > max_chars else transcript


def count_words(text: str) -> int:
    """Words in `text`, split on whitespace, which is also how `limit_words` counts."""
    return len(text.split())


_SENTENCE_END = re.compile(r"[.!?][\"')\]]*(?=\s|$)")


def limit_words(text: str, max_words: int = SUMMARY_MAX_WORDS) -> str:
    """At most `max_words` words of `text`, cut whole.

    Words are never split, so an identifier in the kept part stays intact. A cut ends at
    the last sentence boundary when that keeps most of the allowance, and otherwise at the
    word limit with an ellipsis attached to the last word, which adds no word.
    """
    text = text.strip()
    words = list(re.finditer(r"\S+", text))
    if len(words) <= max_words:
        return text
    kept = text[: words[max_words - 1].end()]
    ends = [m.end() for m in _SENTENCE_END.finditer(kept)]
    if ends and count_words(kept[: ends[-1]]) >= max_words * 0.6:
        return kept[: ends[-1]].strip()
    return kept.rstrip(" ,;:-") + "…"


async def summarize_history(model: BaseChatModel, history: Sequence[BaseMessage]) -> str:
    """The quick model's summary of the earlier conversation, limited to `SUMMARY_MAX_WORDS`.

    A model that writes nothing leaves the handover with no context at all, which is worse
    than a plain one, so the reader's own words stand in: the tail of what they said.
    """
    transcript = history_transcript(history)
    response = await model.ainvoke(
        [SystemMessage(content=SUMMARY_SYSTEM_PROMPT), HumanMessage(content=transcript)]
    )
    summary = limit_words(extract_text(response.content))
    if summary:
        return summary
    researcher_lines = [
        message_text(m) for m in history if isinstance(m, HumanMessage) and message_text(m)
    ]
    return limit_words(" ".join(researcher_lines[-3:]))


def seeded_message(summary: str, reader_message: str) -> str:
    """The one message a handover session starts from: the summary, then the reader's message."""
    return (
        "Summary of our conversation so far, written by the quick-answer assistant:\n"
        f"{summary}\n\n"
        f"My new message:\n{reader_message}"
    )
