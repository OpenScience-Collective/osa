"""Generic community assistant API router factory.

Creates parameterized routers for any registered community.
Each community gets endpoints like /{community_id}/ask, /{community_id}/chat, etc.
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from collections.abc import AsyncGenerator, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.agents.base import (
    CLIENT_TOOLS_NODE,
    DEFAULT_MAX_CONVERSATION_TOKENS,
    count_conversation_tokens,
)
from src.agents.content import (
    CitationAssembler,
    CitationMark,
    ContentBlock,
    classify_content_blocks_with_indices,
    encode_citation_markers,
    normalize_citation_markers,
)
from src.api.config import Settings, get_settings
from src.api.routers.health import compute_community_health
from src.api.security import (
    AuthScope,
    ByokCredential,
    RequireAdminAuth,
    RequireAuth,
    RequireScopedAuth,
    anthropic_key_header,
    openrouter_key_header,
    resolve_byok,
)
from src.api.tool_results import (
    ClientToolResult,
    PendingClientCall,
    build_history_tool_message,
    build_live_tool_message,
    build_unanswered_tool_message,
    scrub_stored_images,
)
from src.assistants import registry
from src.assistants.community import CommunityAssistant
from src.assistants.community import PageContext as AgentPageContext
from src.assistants.registry import AssistantInfo
from src.core.config.community import (
    MAX_DECLARED_CLIENT_TOOLS,
    ClientToolConfig,
    ClientToolRuntime,
    CommunityConfig,
    RuntimeConfig,
    WidgetConfig,
)
from src.core.config.runtime_lock import (
    RuntimeLockError,
    RuntimeLockOverlay,
    load_runtime_lock,
    runtime_wheel,
)
from src.core.limits import MAX_BROWSER_RUNS_PER_REPLY
from src.core.services.anthropic_llm import OFFERED_MODELS, create_anthropic_llm, normalize_model
from src.core.services.litellm_llm import DEFAULT_MODEL as OPENROUTER_DEFAULT_MODEL
from src.core.services.litellm_llm import DEFAULT_PROVIDER as OPENROUTER_DEFAULT_PROVIDER
from src.core.services.litellm_llm import create_openrouter_llm, to_openrouter_model
from src.knowledge.search import FAQResult, get_citation_stats, list_faq_entries
from src.metrics.cost import COST_BLOCK_THRESHOLD, COST_WARN_THRESHOLD, MODEL_PRICING, estimate_cost
from src.metrics.db import (
    RequestLogEntry,
    extract_token_usage,
    extract_tool_names,
    log_request,
    metrics_connection,
    now_iso,
    resolve_cache_creation_tokens,
)
from src.metrics.queries import (
    get_community_summary,
    get_public_community_summary,
    get_public_usage_stats,
    get_quality_metrics,
    get_quality_summary,
    get_usage_stats,
)
from src.tools.client_tools import client_tools_disabled

logger = logging.getLogger(__name__)

# A wheel's name is its identity (see get_runtime_wheel), so the bytes at a
# given URL never change: cacheable for a year, immutable. The worker
# (workers/osa-worker/index.js, IMMUTABLE) and the browser-harness test server
# (frontend/browser-harness/serve.js) each hold their own copy of this exact
# string, since neither can import Python; a drift test
# (tests/test_api/test_runtime_wheel_cache_control.py) keeps all three equal.
RUNTIME_WHEEL_CACHE_CONTROL = "public, max-age=31536000, immutable"

# ---------------------------------------------------------------------------
# Models (shared across all community routers)
# ---------------------------------------------------------------------------

# Built from OFFERED_MODELS rather than spelled out, so a third offered model
# cannot leave this description (which is what /docs and the API reference
# show) naming two. See _select_model for the rule it describes: on the Claude
# Platform any offered model is allowed from any caller, because the platform
# runs only these two and neither can be used to run up an unbounded bill.
MODEL_OVERRIDE_DESCRIPTION = (
    "Optional model override: "
    + " or ".join(f"'{model}'" for model in sorted(OFFERED_MODELS))
    + ", or a legacy alias of either. Any other id requires your own OpenRouter "
    "key via the X-OpenRouter-Key header."
)


class ChatMessage(BaseModel):
    """A single chat message."""

    role: Literal["user", "assistant"] = Field(..., description="Message role")
    content: str = Field(..., description="Message content")


class PageContext(BaseModel):
    """Context about the page where the widget is embedded."""

    url: str | None = Field(
        default=None,
        description="URL of the page where the assistant is embedded",
        max_length=2048,
    )
    title: str | None = Field(
        default=None,
        description="Title of the page where the assistant is embedded",
        max_length=500,
    )
    widget_instructions: str | None = Field(
        default=None,
        description="Per-page instructions for the assistant set by the widget embedder",
        max_length=2000,
    )

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, url: str | None) -> str | None:
        """Ensure URL has valid scheme if provided."""
        if url is None:
            return url
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return url


class ChatRequest(BaseModel):
    """Request body for chat endpoint."""

    message: str = Field(..., description="User message", min_length=1)
    session_id: str | None = Field(
        default=None,
        description="Session ID for conversation continuity. If not provided, a new session is created.",
    )
    stream: bool = Field(default=True, description="Whether to stream the response")
    model: str | None = Field(default=None, description=MODEL_OVERRIDE_DESCRIPTION)
    page_context: PageContext | None = Field(
        default=None,
        description="Optional context about the page where the widget is embedded",
    )
    client_tools: list[str] = Field(
        default_factory=list,
        max_length=MAX_DECLARED_CLIENT_TOOLS,
        description=(
            "Names of client-executed tools this caller can actually run. The "
            "assistant binds only tools that are both configured on the community and "
            "declared here, so a caller that declares nothing gets the ordinary "
            "server-only behavior. Declaring is what keeps an old cached widget from "
            "being sent a tool_request it cannot answer, which would hang its reply "
            "rather than fail it."
        ),
    )


class ResumeRequest(BaseModel):
    """Run 2 of a browser-execution turn: the result of a call run 1 asked for."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=256)
    result: ClientToolResult
    model: str | None = Field(default=None, description=MODEL_OVERRIDE_DESCRIPTION)
    page_context: PageContext | None = None
    client_tools: list[str] = Field(default_factory=list, max_length=MAX_DECLARED_CLIENT_TOOLS)


class AskRequest(BaseModel):
    """Request body for single question (ask) endpoint."""

    question: str = Field(..., description="Question to ask", min_length=1)
    stream: bool = Field(default=False, description="Whether to stream the response")
    page_context: PageContext | None = Field(
        default=None,
        description="Optional context about the page where the widget is embedded",
    )
    model: str | None = Field(default=None, description=MODEL_OVERRIDE_DESCRIPTION)


class ToolCallInfo(BaseModel):
    """Information about a tool call made during response generation."""

    name: str = Field(..., description="Tool name")
    args: dict = Field(default_factory=dict, description="Tool arguments")


class CitationInfo(BaseModel):
    """One inline citation: the [n] marker in the answer, and its source.

    Only ever populated on the Anthropic path when the model actually cited
    something (see src/tools/citations.py and src/agents/content.py's
    CitationTracker). Empty on the OpenRouter path and whenever the model
    cited nothing, so the field is always present on the response and never
    lies about what was cited.
    """

    marker: int = Field(..., description="The [n] used inline in the answer text")
    source: str = Field(..., description="Stable source identifier, typically a URL")
    title: str = Field(..., description="Human-readable title of the cited source")
    cited_text: str = Field(..., description="The exact span of source text the citation points at")


class ChatResponse(BaseModel):
    """Response body for chat/ask endpoints."""

    session_id: str = Field(..., description="Session ID for follow-up messages")
    message: ChatMessage = Field(..., description="Assistant response")
    tool_calls: list[ToolCallInfo] = Field(
        default_factory=list, description="Tools called during response generation"
    )
    citations: list[CitationInfo] = Field(
        default_factory=list,
        description="Inline citations backing the answer's [n] markers, in marker order",
    )
    request_id: str | None = Field(
        default=None,
        description="Per-request identifier the widget can attach to feedback",
    )
    model: str = Field(
        ...,
        description=(
            "The model that actually answered, after resolving requested/default/"
            "alias/cost-guard substitution. Model substitution is otherwise "
            "invisible to callers, detectable only in server logs."
        ),
    )


class AskResponse(BaseModel):
    """Response body for single question endpoint."""

    answer: str = Field(..., description="Assistant's answer")
    tool_calls: list[ToolCallInfo] = Field(
        default_factory=list, description="Tools called during response generation"
    )
    citations: list[CitationInfo] = Field(
        default_factory=list,
        description="Inline citations backing the answer's [n] markers, in marker order",
    )
    request_id: str | None = Field(
        default=None,
        description="Per-request identifier the widget can attach to feedback",
    )
    model: str = Field(
        ...,
        description=(
            "The model that actually answered, after resolving requested/default/"
            "alias/cost-guard substitution. Model substitution is otherwise "
            "invisible to callers, detectable only in server logs."
        ),
    )


class SessionInfo(BaseModel):
    """Information about a chat session."""

    session_id: str = Field(..., description="Unique session identifier", min_length=1)
    community_id: str = Field(..., description="Community this session belongs to", min_length=1)
    message_count: int = Field(..., description="Number of messages in session", ge=0)
    created_at: str = Field(..., description="ISO timestamp when session was created")
    last_active: str = Field(..., description="ISO timestamp of last activity")


class WidgetConfigResponse(BaseModel):
    """Widget display configuration returned to the frontend."""

    title: str = Field(..., description="Widget header title", min_length=1)
    initial_message: str | None = Field(default=None, description="Greeting message on open")
    placeholder: str = Field(..., description="Input placeholder text")
    suggested_questions: list[str] = Field(
        default_factory=list, description="Clickable suggestion buttons"
    )
    logo_url: str | None = Field(
        default=None, description="URL for community logo/icon in widget header"
    )
    theme_color: str | None = Field(default=None, description="Primary theme color as hex #RRGGBB")
    user_bubble_color: str | None = Field(
        default=None, description="The reader's own message bubbles, as hex #RRGGBB"
    )
    theme_text_color: str | None = Field(
        default=None,
        description="Text and icons drawn on theme_color surfaces (header, buttons), as hex #RRGGBB",
    )
    accent_color: str | None = Field(
        default=None,
        description="theme_color used as a foreground on the white panel (links, borders), as hex #RRGGBB",
    )
    user_bubble_text_color: str | None = Field(
        default=None, description="Text in the reader's own message bubbles, as hex #RRGGBB"
    )


class OfferedModelResponse(BaseModel):
    """A model offered by the platform, for widget model-menu display."""

    id: str = Field(..., description="First-party model identifier")
    label: str = Field(..., description="Human-readable display label")


class ClientToolInfo(BaseModel):
    """A client tool the widget may be asked to run, as the widget needs to know it.

    `runtime` is carried so the widget declares only the tools it has a runtime
    for. Phase 1's rule is that a caller declares what it can execute; a widget
    that declared every listed name would break that the day a second runtime
    kind is added.
    """

    name: str
    runtime: ClientToolRuntime
    requires_permission: bool


class CommunityConfigResponse(BaseModel):
    """Community configuration information."""

    id: str = Field(..., description="Community identifier")
    name: str = Field(..., description="Community display name")
    description: str = Field(..., description="Community description")
    default_model: str = Field(..., description="Default LLM model for this community")
    default_model_provider: str | None = Field(
        default=None, description="Default provider for model routing"
    )
    offered_models: list[OfferedModelResponse] = Field(
        ..., description="Models the platform offers, for the widget model menu"
    )
    widget: WidgetConfigResponse = Field(
        ..., description="Widget display configuration (title, placeholder, etc.)"
    )
    status: str = Field(..., description="Health status: healthy, degraded, or error")
    client_tools: list[ClientToolInfo] = Field(
        default_factory=list,
        description=(
            "Client tools this community configures, which the widget can offer to run. "
            "Empty when none are configured or the server's kill switch is set, and the "
            "widget loads no runtime at all in that case. get_full_output is not listed: "
            "it is derived, not configured, and bound beside any python tool."
        ),
    )
    runtime: RuntimeConfig | None = Field(
        default=None,
        description=(
            "The execution environment those tools run in, exactly as configured. None "
            "whenever client_tools is empty."
        ),
    )
    runtime_lock: RuntimeLockOverlay | None = Field(
        default=None,
        description=(
            "The Pyodide lock entries the runtime adds to its Pyodide version's own lock, "
            "verified against the wheels this server serves at runtime/{file_name}. None "
            "when the runtime names no lockfile, and whenever client_tools is empty."
        ),
    )


class FAQEntryResponse(BaseModel):
    """A single FAQ entry exposed via the public feed."""

    question: str = Field(..., description="Synthesized question")
    answer: str = Field(..., description="Synthesized answer")
    tags: list[str] = Field(default_factory=list, description="Keyword tags")
    category: str = Field(..., description="Entry category (how-to, troubleshooting, etc.)")
    quality_score: float = Field(..., description="LLM quality score (0.0-1.0)")
    message_count: int = Field(..., description="Number of source messages in the thread")
    first_message_date: str = Field(..., description="Date of the first message in the thread")
    thread_url: str = Field(..., description="URL of the source discussion thread")


class FAQFeedResponse(BaseModel):
    """Paginated public FAQ feed for a community."""

    community_id: str = Field(..., description="Community identifier")
    total: int = Field(..., description="Total entries matching the filters")
    limit: int = Field(..., description="Page size used for this response")
    offset: int = Field(..., description="Offset used for this response")
    entries: list[FAQEntryResponse] = Field(default_factory=list, description="FAQ entries")


class CitationsFeedResponse(BaseModel):
    """Public citation dashboard data for a community's canonical papers."""

    community_id: str = Field(..., description="Community identifier")
    total: int = Field(..., description="Total citing papers with a recorded canonical link")
    per_year: dict[str, int] = Field(
        default_factory=dict, description="Citing-paper count per year across all papers"
    )
    by_paper: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description="Stacked breakdown: canonical DOI -> year -> citing-paper count",
    )
    canonical_dois: list[str] = Field(
        default_factory=list, description="Canonical DOIs tracked for this community"
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Human-readable labels per canonical DOI (DOI -> label), when configured",
    )


# Matches bare email addresses so they can be stripped from the public feed.
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _redact_emails(text: str) -> str:
    """Replace any email address in ``text`` with a redaction marker.

    The FAQ feed is derived from public mailing-list content. The summarizer
    strips most personal data, but a handful of entries still embed addresses
    (mostly vendor support lines). A public JSON feed should not emit raw
    addresses, so they are redacted at serialization time.
    """
    return _EMAIL_PATTERN.sub("[email redacted]", text)


def _faq_result_to_response(entry: FAQResult) -> FAQEntryResponse:
    """Convert a knowledge-layer FAQResult into a public response model."""
    return FAQEntryResponse(
        question=_redact_emails(entry.question),
        answer=_redact_emails(entry.answer),
        tags=[_redact_emails(tag) for tag in entry.tags],
        category=entry.category,
        quality_score=entry.quality_score,
        message_count=entry.message_count,
        first_message_date=entry.first_message_date,
        thread_url=entry.thread_url,
    )


# ---------------------------------------------------------------------------
# Session Management (In-Memory, per-community isolation)
# ---------------------------------------------------------------------------

# Session limits and constraints
MAX_SESSIONS_PER_COMMUNITY = 1000  # Prevent memory exhaustion
SESSION_TTL_HOURS = 24  # Auto-delete inactive sessions after 24h

# A turn used to be two messages, one human and one assistant. A turn that runs code in
# the browser is three or four: the human message, the assistant message carrying the
# tool call, the tool result, and the assistant's reply to it. At the old cap of 100 a
# session that used the browser reached the ceiling in roughly 25 real turns, which is
# well inside a working session. Raised to keep the same conversational length available
# whether or not a community runs code. The token budget in `_prepare_messages` is what
# bounds context; this bounds memory.
MAX_MESSAGES_PER_SESSION = 300

MAX_MESSAGE_LENGTH = 10000  # Max characters per message a PERSON sends

#: How long one turn may hold a session before a later turn assumes it was abandoned.
#: Generous, because a turn spans a model response and any server tool calls it makes,
#: and the cost of being wrong in the strict direction is refusing a legitimate turn.
TURN_CLAIM_TTL_SECONDS = 300


def _trim_preserving_tool_turns(messages: Sequence[BaseMessage], limit: int) -> list[BaseMessage]:
    """Drop the oldest messages without ever orphaning a tool call or its result.

    Cutting at an arbitrary index is what makes this subtle. An assistant message
    carrying `tool_calls` and the tool results answering it are one indivisible unit to
    the provider: a result whose call is gone, or a call whose result is gone, is
    rejected outright rather than ignored. So the cut advances forward to the next human
    message, which is the only index where no tool round trip is in flight.

    If no such boundary exists after the naive cut, the whole list is kept. Exceeding a
    memory cap is recoverable; sending a message list the provider refuses is not.
    """
    if len(messages) <= limit:
        return list(messages)

    start = len(messages) - limit
    while start < len(messages) and not isinstance(messages[start], HumanMessage):
        start += 1

    if start >= len(messages):
        logger.warning(
            "Session history is %d messages, over the %d cap, but holds no turn "
            "boundary to cut at; keeping it whole rather than orphaning a tool call.",
            len(messages),
            limit,
            extra={"reason": "no_turn_boundary", "message_count": len(messages), "limit": limit},
        )
        return list(messages)

    return list(messages[start:])


class ChatSession:
    """A chat session with message history.

    Enforces constraints:
    - Max messages per session: 300 (see MAX_MESSAGES_PER_SESSION)
    - Max message length: 10,000 characters for human and assistant text
    - Tool results are capped separately by MAX_TOOL_RESULT_LENGTH, because machine
      output is not something a person typed and the smaller cap forbids every real
      figure (issue #422)
    - TTL: 24 hours from last activity

    A session also holds at most one `pending_call`: a browser execution that has been
    requested and not yet answered. See `claim_pending_call` for why the claim is
    deliberately synchronous.
    """

    def __init__(self, session_id: str, community_id: str) -> None:
        self.session_id = session_id
        self.community_id = community_id
        self.messages: list[BaseMessage] = []
        self.created_at = datetime.now(UTC)
        self.last_active = self.created_at
        self.pending_call: PendingClientCall | None = None
        self.turn_started_at: datetime | None = None

    def add_user_message(self, content: str) -> None:
        """Add a user message to history.

        Raises:
            ValueError: If message exceeds length limit or session at max messages.
        """
        if len(content) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Message too long ({len(content)} chars). Max: {MAX_MESSAGE_LENGTH}")
        if len(self.messages) >= MAX_MESSAGES_PER_SESSION:
            raise ValueError(
                f"Session has reached max messages ({MAX_MESSAGES_PER_SESSION}). "
                "Start a new session."
            )
        self.messages.append(HumanMessage(content=content))
        self.last_active = datetime.now(UTC)

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to history.

        Raises:
            ValueError: If message exceeds length limit or session at max messages.
        """
        if len(content) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Message too long ({len(content)} chars). Max: {MAX_MESSAGE_LENGTH}")
        if len(self.messages) >= MAX_MESSAGES_PER_SESSION:
            raise ValueError(
                f"Session has reached max messages ({MAX_MESSAGES_PER_SESSION}). "
                "Start a new session."
            )
        self.messages.append(AIMessage(content=content))
        self.last_active = datetime.now(UTC)

    # -- Browser-executed tool turns ---------------------------------------

    def replace_history(self, messages: Sequence[BaseMessage]) -> None:
        """Adopt the message list a graph run finished with.

        A run that ends on a browser call produces more than a final string: the
        assistant message carrying `tool_calls`, any server tool results from the same
        batch, and a refusal for any second browser call. Re-deriving those from the
        stream would mean reassembling what the graph already assembled correctly, so
        the final state is taken whole.

        The cap is enforced by trimming from the FRONT and never between an assistant
        message and the tool results that answer it, because a tool result whose call is
        gone, or a call whose result is gone, is rejected by the provider outright.

        Scrubbed for images before it is kept (`scrub_stored_images`). The graph's own
        state can still carry a LIVE, image-bearing tool message here: a run that parks
        a second browser call was seeded with `initial_messages`, which includes the
        first call's live `build_live_tool_message` result, and a real MCP tool result
        with an image is built directly by LangGraph's `ToolNode` rather than through
        `build_history_tool_message`. Neither of those two sources is a
        `ClientToolResult` this method can hand to `build_history_tool_message` itself,
        so the scrub runs on the adopted messages generically instead.
        """
        adopted = scrub_stored_images(list(messages))
        if len(adopted) > MAX_MESSAGES_PER_SESSION:
            adopted = _trim_preserving_tool_turns(adopted, MAX_MESSAGES_PER_SESSION)
        self.messages = adopted
        self.last_active = datetime.now(UTC)

    def begin_turn(self) -> bool:
        """Claim this session for one turn, or report that another already holds it.

        Two turns on one session interleave badly and silently. Each copies the message
        list, runs, and writes the whole list back, so whichever finishes last discards
        the other's messages entirely, along with any browser call the discarded turn
        had parked. Nothing raises; the conversation just loses a turn.

        Synchronous for the same reason `claim_pending_call` is: the session store has
        no locking, and a check-then-set with no `await` between the two cannot
        interleave under asyncio.

        The claim goes stale on its own. A generator that is never closed, which a
        dropped connection can cause, would otherwise wedge the session until its
        24-hour TTL, trading a rare lost turn for a permanently broken conversation.
        """
        now = datetime.now(UTC)
        if self.turn_started_at is not None:
            age = (now - self.turn_started_at).total_seconds()
            if age < TURN_CLAIM_TTL_SECONDS:
                return False
            logger.warning(
                "Session %s held a turn claim for %.0fs; treating it as abandoned",
                self.session_id,
                age,
                extra={"session_id": self.session_id, "reason": "stale_turn_claim"},
            )
        self.turn_started_at = now
        return True

    def end_turn(self) -> None:
        self.turn_started_at = None

    def set_pending_call(self, call: PendingClientCall) -> None:
        """Park a browser call, refusing to silently replace an unanswered one.

        A session holds at most one pending call, which the scalar field expresses.
        What the field cannot express is that an occupied slot must not be overwritten,
        and that is the dangerous half: clobbering leaves the first `call_id` referenced
        by an assistant message in stored history with nothing that will ever answer it,
        which the provider rejects on every later turn. There is no error today because
        the only two callers happen to clear the slot first.

        So the guard lives here rather than in the discipline of its call sites. A third
        entry point is exactly the thing that would get this wrong, and it would get it
        wrong silently.
        """
        if self.pending_call is not None and self.pending_call.call_id != call.call_id:
            self.abandon_pending_call("it was replaced by a newer browser call")
        self.pending_call = call
        self.last_active = datetime.now(UTC)

    def claim_pending_call(self, call_id: str) -> PendingClientCall | None:
        """Take the outstanding call named by `call_id`, or return None.

        DELIBERATELY SYNCHRONOUS, and it must stay that way. The session store is a
        plain module-global dict with no locking anywhere, so the only thing keeping a
        check-then-clear atomic is that no `await` appears between them: under asyncio a
        coroutine cannot be preempted mid-body. Making this `async` for symmetry with
        its callers would reintroduce a window in which the same result is accepted
        twice, and accepting once is the whole of the idempotency this transport needs
        in exchange for having no checkpointer.

        Returning None covers all three refusals a caller must treat alike: no call is
        outstanding, a different call is outstanding, or the call expired. The caller
        answers 409 and does not say which, because the distinction is only useful to
        someone guessing.
        """
        pending = self.pending_call
        if pending is None:
            logger.info(
                "Browser result refused for session %s: no call is outstanding",
                self.session_id,
                extra={"session_id": self.session_id, "reason": "none_outstanding"},
            )
            return None
        if pending.is_expired():
            # Repair rather than merely refuse. Leaving a stale call parked would keep
            # the unanswered tool_call in history, and that is what makes a session
            # unusable, so the expiry path has to clear it here too.
            #
            # Logged at warning because this is the reliability signal for a feature
            # with no checkpointer: without it there is no way to measure how often
            # browser calls are timing out rather than being answered.
            logger.warning(
                "Browser call %s on session %s expired before it was answered",
                pending.call_id,
                self.session_id,
                extra={"session_id": self.session_id, "reason": "expired"},
            )
            self.abandon_pending_call("it was not answered in time")
            return None
        if pending.call_id != call_id:
            logger.info(
                "Browser result refused for session %s: a different call is outstanding",
                self.session_id,
                extra={"session_id": self.session_id, "reason": "id_mismatch"},
            )
            return None
        self.pending_call = None
        return pending

    def abandon_pending_call(self, reason: str) -> bool:
        """Close out an unanswered browser call so the history stays valid.

        Called when the person moves on instead of answering, and when the call ages
        out. Without it the session holds an assistant message with `tool_calls` and no
        matching result, which the provider rejects, so every later turn fails rather
        than just the abandoned one.

        Returns whether there was anything to abandon.
        """
        pending = self.pending_call
        if pending is None:
            return False
        self.pending_call = None
        self.messages.append(build_unanswered_tool_message(pending.call_id, reason))
        self.last_active = datetime.now(UTC)
        return True

    def is_expired(self) -> bool:
        """Check if session has exceeded TTL."""
        age_hours = (datetime.now(UTC) - self.last_active).total_seconds() / 3600
        return age_hours > SESSION_TTL_HOURS

    def to_info(self) -> SessionInfo:
        """Convert to SessionInfo model."""
        return SessionInfo(
            session_id=self.session_id,
            community_id=self.community_id,
            message_count=len(self.messages),
            created_at=self.created_at.isoformat(),
            last_active=self.last_active.isoformat(),
        )


# Global session store: {community_id: {session_id: ChatSession}}
_community_sessions: dict[str, dict[str, ChatSession]] = {}


def _get_session_store(community_id: str) -> dict[str, ChatSession]:
    """Get or create session store for a community."""
    if community_id not in _community_sessions:
        _community_sessions[community_id] = {}
    return _community_sessions[community_id]


def _evict_expired_sessions(community_id: str) -> int:
    """Remove expired sessions from store. Returns count of evicted sessions."""
    store = _get_session_store(community_id)
    expired = [sid for sid, session in store.items() if session.is_expired()]
    for sid in expired:
        del store[sid]
    if expired:
        logger.info("Evicted %d expired sessions from community %s", len(expired), community_id)
    return len(expired)


def _evict_lru_session(community_id: str) -> None:
    """Remove the least-recently-used session, preferring one with nothing in flight.

    A session waiting on a browser call is mid-turn, and evicting it destroys work that
    is running on someone's machine right now: their `/chat/resume` then gets a bare 404
    with no way back, and the code they were shown is simply gone. The pending-call TTL
    is deliberately generous precisely because a person may take minutes to read code
    before approving it, and `last_active` does not move while they read, so a parked
    session is exactly the one a naive least-recently-used rule picks first.

    So parked sessions are evicted only when every session in the store is parked. That
    is a real possibility rather than a theoretical one, so it has to remain possible;
    it is just the last resort rather than the first choice. The design note lists this
    as a Phase 0 prerequisite ("session eviction can drop a parked call").
    """
    store = _get_session_store(community_id)
    if not store:
        return

    idle = [sid for sid, session in store.items() if session.pending_call is None]
    candidates = idle or list(store.keys())
    lru_id = min(candidates, key=lambda sid: store[sid].last_active)
    had_pending = store[lru_id].pending_call is not None
    del store[lru_id]

    logger.warning(
        "Evicted LRU session %s from community %s (limit: %d, had a parked browser call: %s)",
        lru_id,
        community_id,
        MAX_SESSIONS_PER_COMMUNITY,
        had_pending,
        extra={"community_id": community_id, "evicted_with_pending_call": had_pending},
    )


def get_or_create_session(community_id: str, session_id: str | None) -> ChatSession:
    """Get existing session or create a new one.

    Enforces session limits:
    - Evicts expired sessions (TTL)
    - Evicts LRU session if at capacity
    - Max sessions per community: 1000
    """
    store = _get_session_store(community_id)

    # Try to get existing session
    if session_id and session_id in store:
        session = store[session_id]
        # Check if expired
        if session.is_expired():
            del store[session_id]
            logger.info("Removed expired session %s", session_id)
        else:
            return session

    # Evict expired sessions before creating new one
    _evict_expired_sessions(community_id)

    # If at capacity, evict LRU
    if len(store) >= MAX_SESSIONS_PER_COMMUNITY:
        _evict_lru_session(community_id)

    # Create new session
    new_id = session_id or str(uuid.uuid4())
    session = ChatSession(new_id, community_id)
    store[new_id] = session
    return session


def get_session(community_id: str, session_id: str) -> ChatSession | None:
    """Get a session by ID, returns None if not found or expired."""
    store = _get_session_store(community_id)
    session = store.get(session_id)
    if session and session.is_expired():
        del store[session_id]
        logger.info("Removed expired session %s", session_id)
        return None
    return session


def delete_session(community_id: str, session_id: str) -> bool:
    """Delete a session. Returns True if deleted, False if not found."""
    store = _get_session_store(community_id)
    if session_id in store:
        del store[session_id]
        return True
    return False


def list_sessions(community_id: str) -> list[ChatSession]:
    """List all active (non-expired) sessions for a community."""
    _evict_expired_sessions(community_id)
    store = _get_session_store(community_id)
    return list(store.values())


# ---------------------------------------------------------------------------
# Assistant Factory
# ---------------------------------------------------------------------------


def _match_wildcard_origin(pattern: str, origin: str) -> bool:
    """Check if an origin matches a wildcard pattern like 'https://*.example.com'.

    Converts '*' to a subdomain-safe regex and uses fullmatch.
    """
    escaped = re.escape(pattern)
    regex = escaped.replace(r"\*", r"[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?")
    return bool(re.fullmatch(regex, origin))


def _is_authorized_origin(origin: str | None, community_id: str) -> bool:
    """Check if Origin header matches allowed CORS origins.

    This determines if a request is coming from an authorized widget embed
    (vs CLI, unauthorized web page, or API client).

    Checks against:
    1. Platform default origins (demo.osc.earth, *-demo.osc.earth, and legacy pages.dev)
    2. Community-specific CORS origins from config

    Args:
        origin: Origin header from HTTP request (e.g., "https://hedtags.org")
        community_id: Community identifier

    Returns:
        True if origin matches platform defaults or community's cors_origins.
        Returns False if origin is None (CLI, mobile apps, browser extensions).
    """
    if not origin:
        return False

    # Platform default origins - always allowed for all communities
    platform_exact_origins = [
        "https://demo.osc.earth",
        "https://osa-demo.pages.dev",
    ]
    platform_wildcard_origins = [
        "https://*-demo.osc.earth",
        "https://*.osa-demo.pages.dev",
    ]

    # Check platform exact matches
    if origin in platform_exact_origins:
        return True

    # Check platform wildcard patterns
    for allowed in platform_wildcard_origins:
        if _match_wildcard_origin(allowed, origin):
            return True

    # Check community-specific origins
    community_info = registry.get(community_id)
    if not community_info or not community_info.community_config:
        return False

    cors_origins = community_info.community_config.cors_origins
    if not cors_origins:
        return False

    for allowed in cors_origins:
        if "*" not in allowed:
            if origin == allowed:
                return True
        elif _match_wildcard_origin(allowed, origin):
            return True

    return False


@dataclass(frozen=True)
class ProviderChoice:
    """Resolved LLM provider, API key, and key source for a request.

    Attributes:
        provider: Which LLM backend to build ("anthropic" or "openrouter").
        api_key: The key to use, or None to let the provider layer read its
            own server-mode credentials from Settings (only possible for
            "anthropic": create_anthropic_llm's server mode reads
            ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_WORKSPACE_ID
            itself, which is required to hit the Claude Platform on AWS
            endpoint rather than the first-party api.anthropic.com).
        key_source: "byok", "community", or "platform".
    """

    provider: Literal["anthropic", "openrouter"]
    api_key: str | None
    key_source: Literal["byok", "community", "platform"]

    def __post_init__(self) -> None:
        """Enforce the invariant every current construction site already
        follows (_platform_choice, _resolve_provider): api_key is None only
        for Anthropic server mode, and every other combination carries a
        non-empty key. Nothing today constructs an out-of-line instance,
        but a future call site that got this wrong would otherwise run on
        the platform's own key with cost enforcement disabled
        (_check_model_cost skips it whenever key_source == "byok", and
        create_anthropic_llm / create_openrouter_llm both treat a falsy key
        as "use server credentials instead")."""
        if self.api_key is None:
            if self.key_source != "platform" or self.provider != "anthropic":
                raise ValueError(
                    "api_key=None is only valid for provider='anthropic' with "
                    f"key_source='platform' (server mode); got provider="
                    f"{self.provider!r}, key_source={self.key_source!r}"
                )
        elif not self.api_key:
            raise ValueError("api_key must not be an empty string; use None for server mode")

    @property
    def takes_native_blocks(self) -> bool:
        """Whether this path has been shown to accept Anthropic's native content
        blocks: search_result citations and image blocks alike. The one place that
        answers it, so citations, MCP images and browser figures cannot disagree."""
        return self.provider == "anthropic"


def _platform_choice(settings: Settings) -> ProviderChoice:
    """Fall back to the platform's own key, preferring Anthropic.

    Phase 2 flips platform-key routing to the Claude Platform on AWS: when
    no BYOK or community key applies, an authorized request uses the
    platform's Anthropic key (server mode, api_key=None so the provider
    layer reads the AWS endpoint/workspace from Settings). OpenRouter is a
    fallback only for deployments that have not configured an Anthropic key.

    Raises:
        HTTPException(500): If neither platform key is configured.
    """
    if settings.anthropic_api_key:
        return ProviderChoice(provider="anthropic", api_key=None, key_source="platform")
    if settings.openrouter_api_key:
        # Falling back this far means ANTHROPIC_API_KEY is unset or empty, so
        # the migration this epic exists for is silently not in effect for
        # this deployment. A DEBUG-level default would leave that invisible;
        # a dedicated `platform_provider` setting was considered instead but
        # rejected -- inferring from key presence plus a loud warning is
        # enough for now, and a provider toggle would reintroduce the
        # configuration ambiguity this epic is removing.
        logger.warning(
            "ANTHROPIC_API_KEY is not configured; platform-funded requests are "
            "falling back to OpenRouter and are NOT running on the Claude "
            "Platform on AWS. Set ANTHROPIC_API_KEY to fix this.",
            extra={"provider": "openrouter", "key_source": "platform"},
        )
        return ProviderChoice(
            provider="openrouter", api_key=settings.openrouter_api_key, key_source="platform"
        )
    raise HTTPException(
        status_code=500,
        detail="No API key configured for this community. Please contact support.",
    )


def _resolve_provider(
    community_id: str,
    byok: ByokCredential | None,
    origin: str | None,
) -> ProviderChoice:
    """Resolve which LLM provider, API key, and key source to use.

    **Authorization Logic:**
    1. If BYOK provided → use it (always allowed), for whichever provider
       the caller's header selected (see ``resolve_byok``).
    2. If origin matches community CORS → allow fallback to a community key
       (Anthropic env var checked before OpenRouter's), then the platform key.
    3. Otherwise → reject (CLI or unauthorized origin must provide BYOK).

    Args:
        community_id: Community identifier.
        byok: Caller-supplied credential, if any (see ``resolve_byok``).
        origin: Origin header from the HTTP request.

    Returns:
        The resolved ProviderChoice.

    Raises:
        HTTPException(403): If origin is not authorized and BYOK is not provided.
        HTTPException(500): If no platform API key is configured and no other key is available.
    """
    # Case 1: BYOK provided - always allowed
    if byok is not None:
        logger.debug(
            "Using BYOK (%s) for community %s",
            byok.provider,
            community_id,
            extra={"community_id": community_id, "key_source": "byok", "provider": byok.provider},
        )
        return ProviderChoice(provider=byok.provider, api_key=byok.key, key_source="byok")

    # Case 2: Check if origin is authorized for platform key usage
    if not _is_authorized_origin(origin, community_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "API key required. Please provide your Anthropic API key via the "
                "X-Anthropic-API-Key header, or your OpenRouter API key via the "
                "X-OpenRouter-Key header. Get an Anthropic key at: "
                "https://platform.claude.com/settings/keys, or an OpenRouter key at: "
                "https://openrouter.ai/keys"
            ),
        )

    # Origin is authorized - allow fallback to community/platform keys
    settings = get_settings()
    community_info = registry.get(community_id)

    if community_info and community_info.community_config:
        config = community_info.community_config

        anthropic_env_var = config.anthropic_api_key_env_var
        if anthropic_env_var:
            community_key = os.getenv(anthropic_env_var)
            if community_key:
                logger.info(
                    "Using community-specific Anthropic API key from %s for %s",
                    anthropic_env_var,
                    community_id,
                    extra={
                        "community_id": community_id,
                        "key_source": "community",
                        "provider": "anthropic",
                        "env_var": anthropic_env_var,
                    },
                )
                return ProviderChoice(
                    provider="anthropic", api_key=community_key, key_source="community"
                )
            logger.error(
                "Community %s configured to use %s but env var not set, falling back to "
                "the platform key. This may incur unexpected costs. Set the environment "
                "variable to fix this.",
                community_id,
                anthropic_env_var,
                extra={
                    "community_id": community_id,
                    "key_source": "platform",
                    "configured_env_var": anthropic_env_var,
                    "env_var_missing": True,
                    "fallback_to_platform": True,
                    "origin": origin,
                },
            )
            return _platform_choice(settings)

        # Anthropic env var not configured for this community; a community
        # can still fund itself through OpenRouter instead.
        openrouter_env_var = config.openrouter_api_key_env_var
        if openrouter_env_var:
            community_key = os.getenv(openrouter_env_var)
            if community_key:
                logger.info(
                    "Using community-specific OpenRouter API key from %s for %s",
                    openrouter_env_var,
                    community_id,
                    extra={
                        "community_id": community_id,
                        "key_source": "community",
                        "provider": "openrouter",
                        "env_var": openrouter_env_var,
                    },
                )
                return ProviderChoice(
                    provider="openrouter", api_key=community_key, key_source="community"
                )
            logger.error(
                "Community %s configured to use %s but env var not set, falling back to "
                "the platform key. This may incur unexpected costs. Set the environment "
                "variable to fix this.",
                community_id,
                openrouter_env_var,
                extra={
                    "community_id": community_id,
                    "key_source": "platform",
                    "configured_env_var": openrouter_env_var,
                    "env_var_missing": True,
                    "fallback_to_platform": True,
                    "origin": origin,
                },
            )
            return _platform_choice(settings)

    return _platform_choice(settings)


def _to_openrouter_model_via_canonical(model: str) -> str | None:
    """Map a model id to its OpenRouter slug, canonicalizing aliases first.

    ``to_openrouter_model`` only recognizes the two canonical first-party ids
    in ``OPENROUTER_MODEL_IDS`` ("claude-haiku-4-5", "claude-sonnet-5"), not
    the bare legacy aliases in ``MODEL_ALIASES`` (e.g. "claude-haiku-4.5",
    "claude-sonnet-4.5"). Passing one of those straight to
    ``to_openrouter_model`` returns None and falls through to the emergency
    default -- the exact model-family substitution this migration set out to
    eliminate. Resolving through ``normalize_model`` first fixes that, since
    it knows every alias; a value that is not a recognized Anthropic id at
    all (an existing OpenRouter slug, or garbage) raises ``ValueError``
    there, so the raw value is passed through unchanged to
    ``to_openrouter_model``, which still handles "already a slug" and
    "unmappable" correctly.
    """
    try:
        canonical = normalize_model(model)
    except ValueError:
        canonical = model
    return to_openrouter_model(canonical)


def _select_model(
    community_info: AssistantInfo,
    requested_model: str | None,
    provider: Literal["anthropic", "openrouter"],
    has_byok: bool,
) -> tuple[str, str | None]:
    """Select the model (and, on OpenRouter, its provider-routing hint).

    **Anthropic:** the requested model, or else the community/platform
    default, is normalized against the offered Claude models (see
    ``normalize_model``). An id that is not offered is rejected with 400
    regardless of key source, since the Claude Platform on AWS only ever
    runs the two offered models -- there is no cost-abuse risk in letting
    any request pick either one. ``default_model_provider`` is ignored
    here: it is OpenRouter-only routing.

    **OpenRouter** (reached via BYOK, or a community's own funded
    OpenRouter key -- see ``_resolve_provider``): unchanged from before
    Phase 2 -- a custom model requires BYOK, otherwise the community or
    platform default (and its provider-routing hint) is used.

    Args:
        community_info: Community information from registry.
        requested_model: User-requested model from the request body.
        provider: The provider resolved by ``_resolve_provider``.
        has_byok: Whether the caller provided their own API key.

    Returns:
        Tuple of (model, provider_routing_hint). The routing hint is always
        None on the Anthropic path.

    Raises:
        HTTPException(400): On the Anthropic path, if the resolved model is
            not one of the offered models.
        HTTPException(403): On the OpenRouter path, if a custom model is
            requested without BYOK.
    """
    settings = get_settings()

    # Determine the default model for this community
    default_model = settings.default_model
    default_provider = settings.default_model_provider
    if community_info.community_config and community_info.community_config.default_model:
        default_model = community_info.community_config.default_model
        default_provider = community_info.community_config.default_model_provider

    if provider == "anthropic":
        try:
            resolved_model = normalize_model(requested_model or default_model)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{e} Provide your own OpenRouter API key via the X-OpenRouter-Key "
                    "header to use other models."
                ),
            ) from e
        return (resolved_model, None)

    # OpenRouter path: if user requests a custom model, require BYOK
    if requested_model and requested_model != default_model:
        if not has_byok:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Custom model '{requested_model}' requires your own API key. "
                    "Please provide your OpenRouter API key via the X-OpenRouter-Key header. "
                    "Get your key at: https://openrouter.ai/keys"
                ),
            )
        # User has BYOK, allow custom model. A caller may name an offered
        # model by its first-party id or a legacy alias (e.g.
        # "claude-sonnet-5" or "claude-sonnet-4.5"), neither of which is a
        # valid OpenRouter slug, so map it across; anything else passes
        # through untouched. Provider routing is left to OpenRouter, which
        # auto-selects the Anthropic provider for anthropic/* models.
        return (_to_openrouter_model_via_canonical(requested_model) or requested_model, None)

    if default_model and "/" not in default_model:
        # Phase 2 (issue #362) made every community and platform
        # default_model a bare first-party Anthropic id such as
        # "claude-haiku-4-5" (or a legacy alias of one), which is not a valid
        # OpenRouter slug. Map it to the same model's OpenRouter slug so a
        # request funded by an OpenRouter key still answers with the model
        # the community chose. Switching to OpenRouter's own default here
        # instead would silently change model family based on which key paid
        # for the request.
        mapped = _to_openrouter_model_via_canonical(default_model)
        if mapped:
            # Provider routing is left to OpenRouter, which auto-selects the
            # Anthropic provider for anthropic/* models.
            return (mapped, None)
        # An unmappable bare id means a default was configured that is
        # neither an offered model (or alias of one) nor an OpenRouter slug.
        # Falling back to the factory default keeps the request serviceable,
        # but it is a misconfiguration worth seeing in the logs, and naming
        # the community is what makes it actionable.
        logger.error(
            "Community %s: default model %r is neither an offered Anthropic "
            "model nor an OpenRouter slug; falling back to %s for this "
            "OpenRouter-funded request",
            community_info.id,
            default_model,
            OPENROUTER_DEFAULT_MODEL,
            extra={"community_id": community_info.id},
        )
        return (OPENROUTER_DEFAULT_MODEL, OPENROUTER_DEFAULT_PROVIDER)

    # Use community or platform default
    return (default_model, default_provider)


def _check_model_cost(model: str, key_source: Literal["byok", "community", "platform"]) -> None:
    """Check if a model's cost exceeds platform thresholds.

    Only enforced when using platform or community API keys (not BYOK).
    Logs a warning for moderately expensive models and blocks very expensive ones.

    Args:
        model: Model identifier (e.g., "openai/gpt-4o").
        key_source: One of "byok", "community", or "platform".

    Raises:
        HTTPException(403): If model cost exceeds the block threshold.
    """
    if key_source == "byok":
        return

    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        logger.error(
            "Model %s not in pricing table; blocking on platform/community key. "
            "Add this model to MODEL_PRICING in src/metrics/cost.py.",
            model,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"Model '{model}' is not in the approved pricing list and cannot be used "
                "with platform or community keys. To use this model, provide your own "
                "API key via the X-OpenRouter-Key header."
            ),
        )
    input_rate = pricing.input_per_1m

    if input_rate >= COST_BLOCK_THRESHOLD:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Model '{model}' costs ${input_rate:.2f}/1M input tokens, "
                f"which exceeds the platform limit of ${COST_BLOCK_THRESHOLD:.2f}/1M. "
                "To use expensive models, provide your own API key via the "
                "X-OpenRouter-Key header. Get a key at: https://openrouter.ai/keys"
            ),
        )

    if input_rate >= COST_WARN_THRESHOLD:
        logger.warning(
            "Model %s costs $%.2f/1M input tokens (warn threshold: $%.2f)",
            model,
            input_rate,
            COST_WARN_THRESHOLD,
        )


def _derive_user_id(token: str) -> str:
    """Derive a stable user ID from API token for cache optimization.

    Uses PBKDF2 to create a stable, anonymous identifier from the token.
    Each unique token gets its own cache lane in OpenRouter.

    Based on HEDit's implementation for consistency across projects.

    Args:
        token: OpenRouter API token (already a secret, not user password)

    Returns:
        16-character hexadecimal cache ID
    """
    # PBKDF2 is a computationally expensive KDF that satisfies CodeQL
    # Using minimal iterations (1000) since input is already high-entropy
    salt = b"osa-cache-id-v1"
    derived = hashlib.pbkdf2_hmac("sha256", token.encode(), salt, iterations=1000, dklen=8)
    return derived.hex()


def _get_cache_user_id(community_id: str, api_key: str | None, user_id: str | None) -> str:
    """Determine the user_id for prompt caching optimization.

    For BYOK users (bring your own key), we derive a stable hash from their API
    key so they get their own cache lane. If they provide an explicit user_id,
    that takes precedence over the derived ID.

    For platform/widget users (using our API key), we use a consistent user_id
    per community so all users benefit from cached system prompts. This is
    important because the system prompt with preloaded docs is large, and
    caching it across users significantly reduces costs and latency.
    Note: user_id parameter is ignored for platform users to ensure cache sharing.

    Args:
        community_id: The community identifier
        api_key: User's API key if BYOK, None for platform users
        user_id: User-provided user_id (only used for BYOK users; ignored for platform users)

    Returns:
        User ID for OpenRouter sticky routing
    """
    if api_key:
        # BYOK user: use their explicit ID or derive from their API key
        return user_id or _derive_user_id(api_key)
    # Platform/widget user: shared ID per community for prompt caching
    return f"{community_id}_widget"


@dataclass
class AssistantWithMetrics:
    """Community assistant bundled with metadata for metrics logging."""

    assistant: CommunityAssistant
    model: str
    key_source: Literal["byok", "community", "platform"]
    langfuse_config: dict | None = None
    langfuse_trace_id: str | None = None


def create_community_assistant(
    community_id: str,
    byok: ByokCredential | None = None,
    origin: str | None = None,
    user_id: str | None = None,
    requested_model: str | None = None,
    preload_docs: bool = True,
    page_context: PageContext | None = None,
    declared_client_tools: set[str] | None = None,
    browser_runs_left: int | None = None,
) -> AssistantWithMetrics:
    """Create a community assistant instance with authorization checks.

    **Authorization:**
    - If BYOK provided -> always allowed, for whichever provider it selects
    - If origin matches community CORS -> can use community/platform keys
    - Otherwise -> rejects with 403 (CLI/unauthorized must provide BYOK)

    **Model Selection:**
    - Anthropic: any offered model may be requested; an unoffered model is a 400
    - OpenRouter: custom model requests require BYOK

    Args:
        community_id: The community identifier (e.g., "hed", "bids")
        byok: Caller-supplied credential, if any (see ``resolve_byok``)
        origin: Origin header from HTTP request (for CORS authorization)
        user_id: User ID for cache optimization (sticky routing, OpenRouter only)
        requested_model: Optional model override from request body
        preload_docs: Whether to preload documents
        page_context: Optional context about the page where the widget is embedded

    Returns:
        AssistantWithMetrics containing the assistant, resolved model, and key source.
        Access the assistant via .assistant attribute.

    Raises:
        ValueError: If community_id is not registered
        HTTPException(400): If an unoffered model is requested on the Anthropic path
        HTTPException(403): If authorization fails, or a custom model is requested
            on the OpenRouter path without BYOK
    """
    community_info = registry.get(community_id)
    if community_info is None:
        raise ValueError(f"Unknown community: {community_id}")

    settings = get_settings()

    # Select provider and API key with authorization checks
    provider_choice = _resolve_provider(community_id, byok, origin)
    logger.debug(
        "Using %s API key for provider %s",
        provider_choice.key_source,
        provider_choice.provider,
        extra={
            "community_id": community_id,
            "origin": origin,
            "key_source": provider_choice.key_source,
            "provider": provider_choice.provider,
        },
    )

    # Select model (provider-aware; checks BYOK requirement for OpenRouter custom models)
    selected_model, selected_provider = _select_model(
        community_info,
        requested_model,
        provider=provider_choice.provider,
        has_byok=provider_choice.key_source == "byok",
    )

    # Block expensive models on platform/community keys
    _check_model_cost(selected_model, provider_choice.key_source)

    logger.debug(
        "Using model %s",
        selected_model,
        extra={"community_id": community_id, "origin": origin, "model": selected_model},
    )

    if provider_choice.provider == "anthropic":
        # Prompt caching on this path is handled by the provider layer
        # (CachingChatAnthropic's cache_control breakpoints in
        # src/core/services/anthropic_llm.py), not by a per-user cache
        # lane, so there is no cache_user_id to compute here.
        model = create_anthropic_llm(
            model=selected_model,
            api_key=provider_choice.api_key,
            temperature=settings.llm_temperature,
        )
    else:
        # Determine user_id for prompt caching optimization
        cache_user_id = _get_cache_user_id(community_id, byok.key if byok else None, user_id)
        model = create_openrouter_llm(
            model=selected_model,
            api_key=provider_choice.api_key,
            temperature=settings.llm_temperature,
            provider=selected_provider,
            user_id=cache_user_id,
        )

    # Convert Pydantic PageContext to agent's dataclass PageContext
    agent_page_context = None
    if page_context:
        agent_page_context = AgentPageContext(
            url=page_context.url,
            title=page_context.title,
            widget_instructions=page_context.widget_instructions,
        )

    assistant = registry.create_assistant(
        community_id,
        model=model,
        preload_docs=preload_docs,
        page_context=agent_page_context,
        # Native search_result citations are Anthropic-only, and every
        # search result in a request must share one citations.enabled
        # setting; the provider choice is already fixed per request, so
        # this satisfies that constraint for free.
        citations=provider_choice.takes_native_blocks,
        # The same gate, for the image block an MCP tool result (nemar_render_overview)
        # can carry. See src.tools.mcp_client._content_of.
        allow_mcp_images=provider_choice.takes_native_blocks,
        # None on the /ask path, which has no session to park a browser call in and so
        # binds no client-executed tools at all: /ask is one request and one answer, so
        # nothing could carry a browser result back. Enforced by the default rather
        # than by a check.
        declared_client_tools=declared_client_tools,
        browser_runs_left=browser_runs_left,
    )

    # Wire LangFuse tracing if configured
    langfuse_config = None
    langfuse_trace_id = None
    try:
        from src.core.services.llm import get_llm_service
    except ImportError:
        logger.debug("LangFuse tracing not available (module not installed)")
    else:
        try:
            llm_service = get_llm_service(settings)
            trace_id = f"{community_id}-{uuid.uuid4().hex[:12]}"
            config = llm_service.get_config_with_tracing(trace_id=trace_id)
            if config.get("callbacks"):
                langfuse_config = config
                langfuse_trace_id = trace_id
        except (AttributeError, ValueError, RuntimeError, OSError, ImportError) as e:
            logger.warning(
                "LangFuse tracing setup failed for %s: %s, continuing without it",
                community_id,
                e,
                exc_info=True,
            )

    return AssistantWithMetrics(
        assistant=assistant,
        model=selected_model,
        key_source=provider_choice.key_source,
        langfuse_config=langfuse_config,
        langfuse_trace_id=langfuse_trace_id,
    )


@dataclass
class AgentResult:
    """Extracted response content and metrics from an agent invocation."""

    response_content: str
    tool_calls_info: list[ToolCallInfo]
    tools_called: list[str]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    citations: list[CitationInfo] = field(default_factory=list)


def _build_answer_with_citations(content: str | list[Any]) -> tuple[str, list[CitationInfo]]:
    """Assemble the answer text (with inline [n] markers) and its citation list.

    Walks the message content's text blocks in order (see
    ``classify_content_blocks_with_indices``): each block's own text is
    followed by protected marker tokens for whatever it cited, then the
    completed answer moves those markers to sentence ends.
    ``CitationAssembler`` also handles a citation-only delta that arrives
    before its matching text block. Numbering is delegated to the shared
    tracker so streaming and non-streaming responses number identically.

    Args:
        content: A final AIMessage's ``content`` (plain string, or a list
            of content blocks; see src/agents/content.py's module
            docstring).

    Returns:
        The answer text with inline markers, and the citation list in
        marker order (empty when nothing was cited).
    """
    assembler = CitationAssembler()
    parts: list[str] = []
    for block, block_index in classify_content_blocks_with_indices(content):
        if block.kind != "text":
            continue
        parts.append(block.text)
        marker_text, _new_marks = assembler.add_block(block, block_index)
        if marker_text:
            parts.append(encode_citation_markers(marker_text))

    assembler.finish_model_run()
    answer = normalize_citation_markers("".join(parts), assembler.marks)
    citations = [
        CitationInfo(marker=m.marker, source=m.source, title=m.title, cited_text=m.cited_text)
        for m in assembler.marks
    ]
    return answer, citations


def _build_citation_sse_events(
    assembler: CitationAssembler,
    block: ContentBlock,
    block_index: int | None,
) -> list[dict[str, Any]]:
    """Build the shared SSE events emitted for one citation-bearing block."""
    marker_text, new_marks = assembler.add_block(block, block_index)
    events: list[dict[str, Any]] = []
    # Announce the metadata before the marker so clients can link it
    # atomically when the inline token is rendered.
    events.extend(
        {
            "event": "citation",
            "marker": mark.marker,
            "source": mark.source,
            "title": mark.title,
            "cited_text": mark.cited_text,
        }
        for mark in new_marks
    )
    if marker_text:
        events.append({"event": "content", "content": marker_text})
    return events


def _extract_agent_result(result: dict) -> AgentResult:
    """Extract response content, tool calls, citations, and token usage.

    Consolidates the common post-invocation logic shared by ask and chat endpoints.
    """
    response_content = ""
    citations: list[CitationInfo] = []
    if result.get("messages"):
        last_msg = result["messages"][-1]
        if isinstance(last_msg, AIMessage):
            # last_msg.content is a plain string unless thinking is enabled or
            # tools are bound, in which case it is a list of typed content
            # blocks (see src/agents/content.py's module docstring).
            response_content, citations = _build_answer_with_citations(last_msg.content)

    tools_called = extract_tool_names(result)
    tool_calls_info = [
        ToolCallInfo(name=tc.get("name", ""), args=tc.get("args", {}))
        for tc in result.get("tool_calls", [])
    ]

    usage = extract_token_usage(result)
    return AgentResult(
        response_content=response_content,
        tool_calls_info=tool_calls_info,
        tools_called=tools_called,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        citations=citations,
    )


def _set_metrics_on_request(
    http_request: Request,
    awm: AssistantWithMetrics,
    agent_result: AgentResult,
) -> None:
    """Store agent metrics on request.state for the metrics middleware to log."""
    http_request.state.metrics_agent_data = {
        "model": awm.model,
        "key_source": awm.key_source,
        "input_tokens": agent_result.input_tokens,
        "output_tokens": agent_result.output_tokens,
        "total_tokens": agent_result.total_tokens,
        "estimated_cost": estimate_cost(
            awm.model,
            agent_result.input_tokens,
            agent_result.output_tokens,
            cache_read_tokens=agent_result.cache_read_tokens,
            cache_creation_tokens=agent_result.cache_creation_tokens,
        ),
        "tools_called": agent_result.tools_called,
        "tool_call_count": len(agent_result.tools_called),
        "langfuse_trace_id": awm.langfuse_trace_id,
        "stream": False,
    }


# ---------------------------------------------------------------------------
# Logo Helpers
# ---------------------------------------------------------------------------

_ASSISTANTS_DIR = Path(__file__).parent.parent.parent / "assistants"

_LOGO_MEDIA_TYPES: dict[str, str] = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def find_logo_file(community_id: str) -> Path | None:
    """Find a convention-based logo file in the community's folder.

    Looks for files named ``logo.*`` with a supported image extension
    (SVG, PNG, JPG, JPEG, WEBP) in ``src/assistants/{community_id}/``.
    Returns the first match or ``None``.  Priority follows the key
    order of ``_LOGO_MEDIA_TYPES``: SVG first, then PNG, then others.
    """
    community_dir = _ASSISTANTS_DIR / community_id
    try:
        if not community_dir.is_dir():
            return None
        for ext in _LOGO_MEDIA_TYPES:
            candidate = community_dir / f"logo{ext}"
            if candidate.is_file():
                return candidate
    except OSError:
        logger.warning(
            "Filesystem error checking logo for community %s at %s",
            community_id,
            community_dir,
            exc_info=True,
        )
    return None


def convention_logo_url(community_id: str, widget: WidgetConfig) -> str | None:
    """Return convention-based logo URL if no explicit logo is configured."""
    if not widget.logo_url and find_logo_file(community_id):
        return f"/{community_id}/logo"
    return None


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------


def _offered_client_tools(config: CommunityConfig | None) -> list[ClientToolConfig]:
    """The client tools a community offers right now: none under the kill switch.

    None, too, for an assistant registered without a YAML config, which configures
    nothing.
    """
    extensions = config.extensions if config is not None else None
    if extensions is None or client_tools_disabled():
        return []
    return list(extensions.client_tools)


def _runtime_lockfile(config: CommunityConfig) -> str | None:
    """The lock overlay the community's Python runtime names, or None."""
    python = config.runtime.python if config.runtime is not None else None
    return python.lockfile if python is not None else None


def _log_unusable_lock(community_id: str, err: RuntimeLockError) -> None:
    # Logged on every request that meets it: the verdict is cached, so this costs a
    # line and not a re-hash, and a broken deployment keeps saying so.
    logger.error("Community %s: its runtime lock overlay does not verify: %s", community_id, err)


def _client_tool_config(config: CommunityConfig | None) -> dict[str, Any]:
    """The client-tool part of the public config: what the widget needs to run them.

    Honors the kill switch here as well as at bind time. The widget decides from
    this response whether to download a Python runtime at all, so a switched-off
    feature must not still cost every visitor that download.

    Fails closed on a lock overlay that does not verify: no client tools, so the
    widget declares none and the model is never offered a tool whose runtime could
    not start. A test verifies every shipped community's overlay, so this is a
    deployment gone wrong rather than a state a reviewed config can reach.
    """
    none: dict[str, Any] = {"client_tools": [], "runtime": None, "runtime_lock": None}
    configured = _offered_client_tools(config)
    if config is None or not configured:
        return none
    lockfile = _runtime_lockfile(config)
    runtime_lock = None
    if lockfile is not None:
        try:
            runtime_lock = load_runtime_lock(_ASSISTANTS_DIR / config.id, lockfile)
        except RuntimeLockError as err:
            _log_unusable_lock(config.id, err)
            return none
    return {
        "client_tools": [
            ClientToolInfo(
                name=entry.name,
                runtime=entry.runtime,
                requires_permission=entry.requires_permission,
            )
            for entry in configured
        ],
        "runtime": config.runtime,
        "runtime_lock": runtime_lock,
    }


def create_community_router(community_id: str) -> APIRouter:
    """Create an API router for a community.

    Args:
        community_id: The community identifier (e.g., "hed", "bids")

    Returns:
        Configured APIRouter with all community endpoints

    Raises:
        ValueError: If community_id is not registered
    """
    info = registry.get(community_id)
    if info is None:
        raise ValueError(f"Unknown community: {community_id}")

    # Use display name if available, otherwise capitalize ID
    display_name = info.name or community_id.upper()
    router = APIRouter(prefix=f"/{community_id}", tags=[f"{display_name} Assistant"])

    # -----------------------------------------------------------------------
    # Endpoints
    # -----------------------------------------------------------------------

    @router.post(
        "/ask",
        response_model=AskResponse,
        responses={
            200: {"description": "Successful response"},
            400: {"description": "Invalid request"},
            500: {"description": "Internal server error"},
        },
    )
    async def ask(
        body: AskRequest,
        http_request: Request,
        _auth: RequireAuth,
        x_anthropic_key: Annotated[
            str | None, Header(alias=anthropic_key_header.model.name)
        ] = None,
        x_openrouter_key: Annotated[
            str | None, Header(alias=openrouter_key_header.model.name)
        ] = None,
        x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    ) -> AskResponse | StreamingResponse:
        """Ask a single question to the community assistant.

        This endpoint is for one-off questions without conversation history.
        For multi-turn conversations, use the /chat endpoint.

        **BYOK (Bring Your Own Key):**
        Pass your Anthropic API key in the `X-Anthropic-API-Key` header, or
        your OpenRouter API key in the `X-OpenRouter-Key` header. Anthropic
        wins if both are provided. Required for CLI usage and, on the
        OpenRouter path, for custom model requests.

        **Custom Models:**
        Specify a custom model via the `model` field in the request body.
        On the Anthropic path, any offered model may be requested; on the
        OpenRouter path, custom models require BYOK.

        **Cache Optimization:**
        Pass a stable user ID in the `X-User-ID` header for better cache hit rates.
        """
        # Extract origin for authorization
        origin = http_request.headers.get("origin")
        byok = resolve_byok(x_anthropic_key, x_openrouter_key)

        if body.stream:
            return StreamingResponse(
                _stream_ask_response(
                    community_id,
                    body.question,
                    byok,
                    origin,
                    x_user_id,
                    body.page_context,
                    body.model,
                    http_request=http_request,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        try:
            awm = create_community_assistant(
                community_id,
                byok=byok,
                origin=origin,
                user_id=x_user_id,
                requested_model=body.model,
                page_context=body.page_context,
            )
            messages = [HumanMessage(content=body.question)]
            result = await awm.assistant.ainvoke(messages, config=awm.langfuse_config)

            ar = _extract_agent_result(result)
            _set_metrics_on_request(http_request, awm, ar)

            return AskResponse(
                answer=ar.response_content,
                tool_calls=ar.tool_calls_info,
                citations=ar.citations,
                request_id=getattr(http_request.state, "request_id", None),
                model=awm.model,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                "Error in ask endpoint for community %s: %s",
                community_id,
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Internal server error. Please contact support if the issue persists.",
            ) from e

    @router.post(
        "/chat",
        response_model=ChatResponse,
        responses={
            200: {"description": "Successful response"},
            400: {"description": "Invalid request"},
            500: {"description": "Internal server error"},
        },
    )
    async def chat(
        body: ChatRequest,
        http_request: Request,
        _auth: RequireAuth,
        x_anthropic_key: Annotated[
            str | None, Header(alias=anthropic_key_header.model.name)
        ] = None,
        x_openrouter_key: Annotated[
            str | None, Header(alias=openrouter_key_header.model.name)
        ] = None,
        x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    ) -> ChatResponse | StreamingResponse:
        """Chat with the community assistant.

        Supports multi-turn conversations with session persistence.

        **BYOK (Bring Your Own Key):**
        Pass your Anthropic API key in the `X-Anthropic-API-Key` header, or
        your OpenRouter API key in the `X-OpenRouter-Key` header. Anthropic
        wins if both are provided. Required for CLI usage and, on the
        OpenRouter path, for custom model requests.

        **Custom Models:**
        Specify a custom model via the `model` field in the request body.
        On the Anthropic path, any offered model may be requested; on the
        OpenRouter path, custom models require BYOK.

        **Cache Optimization:**
        Pass a stable user ID in the `X-User-ID` header for better cache hit rates.
        """
        # Extract origin for authorization
        origin = http_request.headers.get("origin")
        byok = resolve_byok(x_anthropic_key, x_openrouter_key)

        session = get_or_create_session(community_id, body.session_id)
        user_id = x_user_id or session.session_id

        # The person moved on instead of answering a browser call. Close it out before
        # anything else: leaving an assistant message with tool_calls and no matching
        # result makes the provider reject EVERY later turn, not just this one.
        if session.abandon_pending_call("the conversation moved on before it was run"):
            logger.info("Abandoned an unanswered browser call on session %s", session.session_id)

        # Add user message with constraint validation
        try:
            session.add_user_message(body.message)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        if body.stream:
            return StreamingResponse(
                _stream_chat_response(
                    community_id,
                    session,
                    byok,
                    origin,
                    user_id,
                    body.model,
                    page_context=body.page_context,
                    http_request=http_request,
                    declared_client_tools=set(body.client_tools),
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Session-ID": session.session_id,
                },
            )

        try:
            awm = create_community_assistant(
                community_id,
                byok=byok,
                origin=origin,
                user_id=user_id,
                requested_model=body.model,
                page_context=body.page_context,
            )
            result = await awm.assistant.ainvoke(session.messages, config=awm.langfuse_config)

            ar = _extract_agent_result(result)
            _set_metrics_on_request(http_request, awm, ar)

            # Add assistant message with constraint validation
            try:
                session.add_assistant_message(ar.response_content)
            except ValueError as e:
                logger.error("Session limit exceeded: %s", e)
                raise HTTPException(
                    status_code=500,
                    detail="Session limit exceeded. Please start a new conversation.",
                ) from e

            return ChatResponse(
                session_id=session.session_id,
                message=ChatMessage(role="assistant", content=ar.response_content),
                tool_calls=ar.tool_calls_info,
                citations=ar.citations,
                request_id=getattr(http_request.state, "request_id", None),
                model=awm.model,
            )

        except ValueError as e:
            # Session limit errors
            raise HTTPException(status_code=400, detail=str(e)) from e
        except HTTPException:
            # Re-raise HTTP exceptions (including the ones we created above)
            raise
        except Exception as e:
            logger.error(
                "Error in chat endpoint for session %s (community: %s): %s",
                session.session_id,
                community_id,
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Internal server error. Please contact support if the issue persists.",
            ) from e

    @router.post(
        "/chat/resume",
        responses={
            200: {"description": "Run 2 of the turn, streamed"},
            404: {"description": "No such session"},
            409: {"description": "No such outstanding call for this session"},
        },
    )
    async def chat_resume(
        body: ResumeRequest,
        http_request: Request,
        _auth: RequireAuth,
        x_anthropic_key: Annotated[
            str | None, Header(alias=anthropic_key_header.model.name)
        ] = None,
        x_openrouter_key: Annotated[
            str | None, Header(alias=openrouter_key_header.model.name)
        ] = None,
        x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    ) -> StreamingResponse:
        """Continue a turn with the result of a tool the browser ran.

        This is run 2 of the two-run continuation. Run 1 ended with the assistant
        message carrying the tool call and a `tool_request` event; the browser executed
        with no request open; this call appends the result and continues. Nothing is
        resumed in the LangGraph sense, so there is no checkpointer and no parked
        connection: a continuation is just a new run over a longer message list.

        **Authorization is the `call_id`, not the session id.** A client may choose its
        own `session_id` on the way in, so a session id is a routing key rather than a
        credential. The `call_id` is the provider's own tool-call id, which a caller
        only ever learns from a `tool_request` on its own stream, and it is accepted at
        most once.
        """
        origin = http_request.headers.get("origin")
        byok = resolve_byok(x_anthropic_key, x_openrouter_key)

        # Whether run 2 may carry the result's images, decided the same way
        # `create_community_assistant` decides citations. Not an authorization
        # check: `_stream_chat_response` makes that one, on the same inputs, and its
        # 403 is the one a caller sees. So a refusal here only means no images. It
        # runs before the call is claimed, because anything raised after the claim
        # would leave the session unanswerable (see the note on the re-park below).
        try:
            allow_images = _resolve_provider(community_id, byok, origin).takes_native_blocks
        except HTTPException as err:
            logger.debug(
                "No provider for /chat/resume images (%s: %s); sending placeholders",
                err.status_code,
                err.detail,
                extra={"community_id": community_id},
            )
            allow_images = False

        session = get_session(community_id, body.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found or expired.")

        # Synchronous by construction; see ChatSession.claim_pending_call. One refusal
        # for all three cases, because telling a caller WHICH of "no call outstanding",
        # "a different call is outstanding" and "that call expired" applies is only
        # useful to someone guessing.
        pending = session.claim_pending_call(body.result.call_id)
        if pending is None:
            raise HTTPException(
                status_code=409,
                detail="No outstanding browser call with that id for this session.",
            )

        user_id = x_user_id or session.session_id

        # The live message list carries the images; the stored one never does. Both are
        # built from the same result so they cannot describe different runs.
        #
        # Claiming already cleared the parked call, so from here until the history is
        # appended the session holds an assistant message with `tool_calls` and nothing
        # answering it. Anything raising in that window would leave the session
        # permanently unusable and unrepairable, because `abandon_pending_call` would
        # have nothing left to abandon. Nothing in these two builders can raise today;
        # the re-park is here so that stays true when someone adds something that can.
        try:
            live_messages = [
                *session.messages,
                build_live_tool_message(body.result, allow_images=allow_images),
            ]
            session.messages.append(build_history_tool_message(body.result))
        except Exception:
            session.set_pending_call(pending)
            logger.exception(
                "Failed to record browser result %s on session %s; the call was "
                "re-parked so the session stays answerable",
                pending.call_id,
                session.session_id,
            )
            raise

        return StreamingResponse(
            _stream_chat_response(
                community_id,
                session,
                byok,
                origin,
                user_id,
                body.model,
                page_context=body.page_context,
                http_request=http_request,
                declared_client_tools=set(body.client_tools),
                initial_messages=live_messages,
                endpoint=f"/{community_id}/chat/resume",
                carried_citations=pending.carried_citations,
                browser_runs_answered=pending.runs_before + 1,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Session-ID": session.session_id,
            },
        )

    @router.get("/sessions/{session_id}", response_model=SessionInfo)
    async def get_session_info(session_id: str, _auth: RequireAdminAuth) -> SessionInfo:
        """Get information about a chat session.

        Uses RequireAdminAuth, not RequireAuth: this never spends a
        caller-supplied BYOK credential against an LLM, so RequireAuth's
        BYOK bypass (any syntactically-plausible key in X-Anthropic-API-Key
        or X-OpenRouter-Key) would otherwise expose every session's
        metadata to a request carrying a junk header.
        """
        session = get_session(community_id, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session.to_info()

    @router.delete("/sessions/{session_id}")
    async def delete_session_endpoint(session_id: str, _auth: RequireAdminAuth) -> dict[str, str]:
        """Delete a chat session. See get_session_info for why this requires
        RequireAdminAuth rather than RequireAuth."""
        if not delete_session(community_id, session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "deleted", "session_id": session_id}

    @router.get("/sessions", response_model=list[SessionInfo])
    async def list_sessions_endpoint(_auth: RequireAdminAuth) -> list[SessionInfo]:
        """List all active chat sessions for this community. See
        get_session_info for why this requires RequireAdminAuth rather than
        RequireAuth."""
        return [session.to_info() for session in list_sessions(community_id)]

    @router.get("", response_model=CommunityConfigResponse)
    @router.get("/", response_model=CommunityConfigResponse, include_in_schema=False)
    async def get_community_config() -> CommunityConfigResponse:
        """Get community configuration including default model settings.

        Returns community information and model configuration that the
        frontend widget uses to display settings and defaults.

        No authentication required - this is public configuration info.
        """
        settings = get_settings()

        # Determine default model: community-specific or platform default
        default_model = settings.default_model
        default_provider = settings.default_model_provider

        if info.community_config and info.community_config.default_model:
            default_model = info.community_config.default_model
            default_provider = info.community_config.default_model_provider

        # Validate required configuration
        if not default_model:
            logger.error(
                "No default model configured for community %s (platform: %s, community: %s)",
                info.id,
                settings.default_model,
                info.community_config.default_model if info.community_config else None,
            )
            raise HTTPException(
                status_code=500,
                detail="Community configuration incomplete: no default model configured",
            )

        # Resolve widget config with defaults applied
        widget_cfg = (
            info.community_config.widget if info.community_config else None
        ) or WidgetConfig()

        # Convention-based logo: if no explicit logo_url, check for logo file
        conv_logo = convention_logo_url(community_id, widget_cfg)

        # Compute lightweight health status for public display
        health_status = "error"
        if info.community_config:
            try:
                health_status = compute_community_health(info.community_config)["status"]
            except (AttributeError, KeyError, TypeError) as e:
                logger.error(
                    "Failed to compute health for community %s: %s",
                    info.id,
                    e,
                    exc_info=True,
                )

        return CommunityConfigResponse(
            id=info.id,
            name=info.name,
            description=info.description,
            default_model=default_model,
            default_model_provider=default_provider,
            offered_models=[
                OfferedModelResponse(id=model_id, label=label)
                for model_id, label in OFFERED_MODELS.items()
            ],
            widget=WidgetConfigResponse(**widget_cfg.resolve(info.name, logo_url=conv_logo)),
            status=health_status,
            **_client_tool_config(info.community_config),
        )

    @router.get("/logo")
    async def get_community_logo() -> FileResponse:
        """Serve the community's logo file.

        Looks for a ``logo.*`` file (SVG, PNG, JPG, WEBP) in the
        community's assistants folder.  Returns 404 if none exists.
        """
        logo_path = find_logo_file(community_id)
        if logo_path is None:
            raise HTTPException(status_code=404, detail="No logo found for this community")

        # Guard against file disappearing between detection and serving
        try:
            logo_path.stat()
        except OSError:
            logger.warning("Logo file disappeared or became unreadable: %s", logo_path)
            raise HTTPException(status_code=404, detail="No logo found for this community")

        media_type = _LOGO_MEDIA_TYPES.get(logo_path.suffix.lower(), "application/octet-stream")
        headers: dict[str, str] = {"Cache-Control": "public, max-age=86400"}
        # Prevent script execution in SVGs opened via direct navigation
        if logo_path.suffix.lower() == ".svg":
            headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"

        return FileResponse(
            logo_path,
            media_type=media_type,
            filename=f"{community_id}-logo{logo_path.suffix}",
            headers=headers,
        )

    @router.get("/runtime/{file_name}")
    async def get_runtime_wheel(file_name: str) -> Response:
        """Serve one wheel the community's lock overlay lists, for the browser runtime.

        Only names the overlay lists are served, found by lookup rather than by
        joining the request onto the disk, and only while the community offers the
        client tools that runtime is for, which is when /config sends the overlay.
        The bytes are the ones hashed against the overlay's sha256, which Pyodide
        checks again as it loads them. Immutable for a year: a wheel's name is its
        identity.

        A 404 means there is no such wheel. An overlay that does not verify is a 503
        instead, so the edge neither caches it nor reports a broken deployment as a
        missing file.
        """
        config = info.community_config
        not_found = HTTPException(status_code=404, detail="No such runtime file")
        if config is None or not _offered_client_tools(config):
            raise not_found
        lockfile = _runtime_lockfile(config)
        if lockfile is None:
            raise not_found
        try:
            wheel = runtime_wheel(_ASSISTANTS_DIR / config.id, lockfile, file_name)
        except RuntimeLockError as err:
            _log_unusable_lock(config.id, err)
            raise HTTPException(
                status_code=503, detail="This community's browser runtime is unavailable"
            ) from None
        if wheel is None:
            raise not_found
        return Response(
            content=wheel,
            media_type="application/octet-stream",
            headers={"Cache-Control": RUNTIME_WHEEL_CACHE_CONTROL},
        )

    # -----------------------------------------------------------------------
    # Per-community Metrics Endpoints
    # -----------------------------------------------------------------------

    def _require_community_access(auth: AuthScope) -> None:
        """Raise 403 if the scoped key cannot access this community."""
        if not auth.can_access_community(community_id):
            raise HTTPException(
                status_code=403,
                detail=f"Your API key does not have access to {community_id} metrics",
            )

    @router.get("/metrics")
    async def community_metrics(auth: RequireScopedAuth) -> dict[str, Any]:
        """Get metrics summary for this community. Requires admin or community key."""
        _require_community_access(auth)
        try:
            with metrics_connection() as conn:
                return get_community_summary(community_id, conn)
        except sqlite3.Error:
            logger.exception("Failed to query metrics for community %s", community_id)
            raise HTTPException(
                status_code=503,
                detail="Metrics database is temporarily unavailable.",
            )

    @router.get("/metrics/usage")
    async def community_usage(
        auth: RequireScopedAuth,
        period: str = Query(
            default="daily",
            description="Time bucket period",
            pattern="^(daily|weekly|monthly)$",
        ),
    ) -> dict[str, Any]:
        """Get time-bucketed usage stats for this community. Requires admin or community key."""
        _require_community_access(auth)
        try:
            with metrics_connection() as conn:
                return get_usage_stats(community_id, period, conn)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except sqlite3.Error:
            logger.exception("Failed to query usage stats for community %s", community_id)
            raise HTTPException(
                status_code=503,
                detail="Metrics database is temporarily unavailable.",
            )

    @router.get("/metrics/quality")
    async def community_quality(
        auth: RequireScopedAuth,
        period: str = Query(
            default="daily",
            description="Time bucket period",
            pattern="^(daily|weekly|monthly)$",
        ),
    ) -> dict[str, Any]:
        """Get quality metrics for this community. Requires admin or community key."""
        _require_community_access(auth)
        try:
            with metrics_connection() as conn:
                return get_quality_metrics(community_id, conn, period)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except sqlite3.Error:
            logger.exception("Failed to query quality metrics for community %s", community_id)
            raise HTTPException(
                status_code=503,
                detail="Metrics database is temporarily unavailable.",
            )

    @router.get("/metrics/quality/summary")
    async def community_quality_summary(auth: RequireScopedAuth) -> dict[str, Any]:
        """Get overall quality summary for this community. Requires admin or community key."""
        _require_community_access(auth)
        try:
            with metrics_connection() as conn:
                return get_quality_summary(community_id, conn)
        except sqlite3.Error:
            logger.exception("Failed to query quality summary for community %s", community_id)
            raise HTTPException(
                status_code=503,
                detail="Metrics database is temporarily unavailable.",
            )

    # -----------------------------------------------------------------------
    # Per-community Public Metrics Endpoints (no auth required)
    # -----------------------------------------------------------------------

    @router.get("/metrics/public")
    async def community_metrics_public() -> dict[str, Any]:
        """Get public metrics summary for this community.

        Returns request counts, error rate, top tools, and config health.
        No tokens, costs, or model information exposed.
        """
        try:
            with metrics_connection() as conn:
                result = get_public_community_summary(community_id, conn)
        except sqlite3.Error:
            logger.exception("Failed to query public metrics for community %s", community_id)
            raise HTTPException(
                status_code=503,
                detail="Metrics database is temporarily unavailable.",
            )

        # Add config health alongside usage metrics
        fallback_health: dict[str, Any] = {
            "status": "error",
            "api_key": "missing",
            "documents": 0,
            "warnings": ["Community configuration not found"],
        }
        if info.community_config:
            try:
                health = compute_community_health(info.community_config)
                # Sanitize warnings for public endpoint: strip env var names
                public_warnings = [w for w in health["warnings"] if "Environment variable" not in w]
                if health["api_key"] == "missing" and not public_warnings:
                    public_warnings = [
                        "API key not configured; using shared platform key. "
                        "This is for demonstration only and is not sustainable."
                    ]
                result["config_health"] = {
                    "status": health["status"],
                    "api_key": health["api_key"],
                    "documents": health["documents"],
                    "warnings": public_warnings,
                }
            except (AttributeError, KeyError, TypeError) as e:
                logger.error(
                    "Failed to compute health for community %s: %s",
                    community_id,
                    e,
                    exc_info=True,
                )
                result["config_health"] = fallback_health
        else:
            result["config_health"] = fallback_health

        # Derive available tools from community config
        if info.community_config:
            try:
                config = info.community_config
                tools = []
                if config.documentation:
                    tools.append(f"retrieve_{config.id}_docs")
                if config.github and config.github.repos:
                    tools.append(f"search_{config.id}_discussions")
                    tools.append(f"list_{config.id}_recent")
                if config.citations and (config.citations.queries or config.citations.dois):
                    tools.append(f"search_{config.id}_papers")
                if config.docstrings and config.docstrings.repos:
                    tools.append(f"search_{config.id}_code_docs")
                if config.faq_generation and config.mailman:
                    tools.append(f"search_{config.id}_faq")
                if config.discourse:
                    tools.append(f"search_{config.id}_forum")
                result["available_tools_list"] = tools
            except (AttributeError, TypeError) as e:
                logger.error(
                    "Failed to derive tools for community %s: %s",
                    community_id,
                    e,
                    exc_info=True,
                )
                result["available_tools_list"] = []
        else:
            result["available_tools_list"] = []

        return result

    @router.get("/metrics/public/usage")
    async def community_usage_public(
        period: str = Query(
            default="daily",
            description="Time bucket period",
            pattern="^(daily|weekly|monthly)$",
        ),
    ) -> dict[str, Any]:
        """Get public time-bucketed usage stats for this community.

        Returns request counts and errors per time bucket.
        No tokens or costs exposed.
        """
        try:
            with metrics_connection() as conn:
                return get_public_usage_stats(community_id, period, conn)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except sqlite3.Error:
            logger.exception("Failed to query public usage stats for community %s", community_id)
            raise HTTPException(
                status_code=503,
                detail="Metrics database is temporarily unavailable.",
            )

    @router.get("/faq", response_model=FAQFeedResponse)
    async def community_faq(
        response: Response,
        q: str | None = Query(
            default=None,
            description="Optional full-text search phrase. If omitted, browses all entries.",
            max_length=200,
        ),
        category: str | None = Query(
            default=None,
            description="Filter by category (how-to, troubleshooting, reference, etc.)",
            max_length=50,
        ),
        min_quality: float = Query(
            default=0.0, ge=0.0, le=1.0, description="Minimum quality score"
        ),
        limit: int = Query(default=50, ge=1, le=200, description="Page size"),
        offset: int = Query(default=0, ge=0, description="Pagination offset"),
    ) -> FAQFeedResponse:
        """Public, read-only FAQ feed for this community.

        Returns synthesized question/answer entries generated from the
        community's mailing-list and forum archives. Disabled by default;
        a community opts in via ``public_feeds.faq: true`` in its config.
        Email addresses are redacted from the output. ``total`` is the full
        match count before pagination, in both browse and search modes.
        """
        config = info.community_config
        if config is None or config.public_feeds is None or not config.public_feeds.faq:
            raise HTTPException(
                status_code=404,
                detail="Public FAQ feed is not enabled for this community.",
            )

        try:
            entries, total = list_faq_entries(
                project=community_id,
                limit=limit,
                offset=offset,
                query=q,
                category=category,
                min_quality=min_quality,
            )
        except sqlite3.Error:
            logger.exception("Failed to query FAQ feed for community %s", community_id)
            raise HTTPException(
                status_code=503,
                detail="Knowledge database is temporarily unavailable.",
            )
        except Exception:
            logger.exception("Unexpected error serving FAQ feed for community %s", community_id)
            raise HTTPException(
                status_code=500,
                detail="An unexpected error occurred while building the FAQ feed.",
            )

        # Public, read-only data; cacheable like the other /…/public endpoints.
        response.headers["Cache-Control"] = "public, max-age=3600"
        return FAQFeedResponse(
            community_id=community_id,
            total=total,
            limit=limit,
            offset=offset,
            entries=[_faq_result_to_response(e) for e in entries],
        )

    @router.get("/citations", response_model=CitationsFeedResponse)
    async def community_citations(response: Response) -> CitationsFeedResponse:
        """Public, read-only citation dashboard for this community.

        Returns per-year counts of papers citing the community's canonical
        works, plus a stacked breakdown keyed by the cited DOI (the shape
        behind a citations-per-year chart). Disabled by default; a community
        opts in via ``public_feeds.citations: true`` in its config.
        """
        config = info.community_config
        if config is None or config.public_feeds is None or not config.public_feeds.citations:
            raise HTTPException(
                status_code=404,
                detail="Public citations feed is not enabled for this community.",
            )

        try:
            stats = get_citation_stats(project=community_id)
        except sqlite3.Error:
            logger.exception("Failed to query citations for community %s", community_id)
            raise HTTPException(
                status_code=503,
                detail="Knowledge database is temporarily unavailable.",
            )
        except Exception:
            logger.exception(
                "Unexpected error serving citations feed for community %s", community_id
            )
            raise HTTPException(
                status_code=500,
                detail="An unexpected error occurred while building the citations feed.",
            )

        canonical_dois = list(config.citations.dois) if config.citations else []
        labels = dict(config.citations.paper_labels) if config.citations else {}

        response.headers["Cache-Control"] = "public, max-age=3600"
        return CitationsFeedResponse(
            community_id=community_id,
            total=stats.total,
            per_year=stats.per_year,
            by_paper=stats.by_paper,
            canonical_dois=canonical_dois,
            labels=labels,
        )

    return router


# ---------------------------------------------------------------------------
# Metrics Helpers
# ---------------------------------------------------------------------------


def _sse_safe_tool_output(tool_output: Any) -> str:
    """Render a tool's `on_tool_end` output for the `tool_end` SSE event.

    `tool_output` is the `ToolMessage` LangGraph's `ToolNode` built, not the tool's
    raw return value, and `str()` on it prints its `content` in full. That was safe
    while `content` was a string. An MCP tool can now return a block list carrying a
    base64 PNG (`src.tools.mcp_client._content_of`), and `str()` would stream it.

    So a block list keeps only what is known to be text: each text block's text, and
    a one-line placeholder naming any other block's type. That fails closed for a
    block spelling nobody has written yet. String content renders exactly as
    `str(tool_output)` always has; nothing reads this field today beyond a log line
    in the widget, so the rendering is for people reading the stream.
    """
    if not tool_output:
        return ""
    content = getattr(tool_output, "content", tool_output)
    if not isinstance(content, list):
        return str(tool_output)
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        else:
            kind = block.get("type", "unknown") if isinstance(block, dict) else type(block).__name__
            parts.append(f"[{kind} block, not shown in this event]")
    return "\n".join(parts)


def _extract_token_usage(event_data: dict) -> tuple[int, int, int, int]:
    """Extract token counts from an on_chat_model_end event.

    Returns (input_tokens, output_tokens, cache_read_tokens,
    cache_creation_tokens), defaulting to all zeros when usage metadata is
    absent or malformed. Never raises; metrics collection must not disrupt
    user-facing streams. See ``src.metrics.db.extract_token_usage`` for the
    non-streaming equivalent and why input_tokens already includes the two
    cache fields.
    """
    try:
        ai_msg = event_data.get("output")
        usage = getattr(ai_msg, "usage_metadata", None) if ai_msg else None
        if not usage or not isinstance(usage, dict):
            return 0, 0, 0, 0
        details = usage.get("input_token_details") or {}
        if not isinstance(details, dict):
            details = {}
        return (
            usage.get("input_tokens") or 0,
            usage.get("output_tokens") or 0,
            details.get("cache_read") or 0,
            resolve_cache_creation_tokens(details),
        )
    except Exception:
        # DEBUG is invisible at this repo's default INFO level, so a genuine
        # extraction bug would silently show up as a free request on the
        # dashboard instead of a visible log line.
        logger.warning("Failed to extract token usage from event data", exc_info=True)
        return 0, 0, 0, 0


def _log_streaming_metrics(
    http_request: Request | None,
    community_id: str,
    endpoint: str,
    awm: AssistantWithMetrics | None,
    tools_called: list[str],
    start_time: float,
    status_code: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> None:
    """Log metrics at the end of a streaming response.

    Called directly from streaming generators since middleware fires
    before streaming completes. Wrapped in try/except to never disrupt
    the SSE stream on failure.
    """
    try:
        duration_ms = (time.monotonic() - start_time) * 1000
        request_id = str(uuid.uuid4())
        if http_request:
            request_id = getattr(http_request.state, "request_id", request_id)
            # Mark as logged so middleware doesn't double-log
            http_request.state.metrics_logged = True

        total_tokens = input_tokens + output_tokens
        has_tokens = total_tokens > 0
        model = awm.model if awm else None
        cost = (
            estimate_cost(
                model,
                input_tokens,
                output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
            )
            if has_tokens
            else None
        )

        entry = RequestLogEntry(
            request_id=request_id,
            timestamp=now_iso(),
            endpoint=endpoint,
            method="POST",
            community_id=community_id,
            duration_ms=round(duration_ms, 1),
            status_code=status_code,
            model=model,
            key_source=awm.key_source if awm else None,
            tools_called=tools_called,
            stream=True,
            tool_call_count=len(tools_called),
            langfuse_trace_id=awm.langfuse_trace_id if awm else None,
            input_tokens=input_tokens if has_tokens else None,
            output_tokens=output_tokens if has_tokens else None,
            total_tokens=total_tokens if has_tokens else None,
            estimated_cost=cost,
        )
        log_request(entry)
    except Exception:
        logger.exception(
            "Failed to log streaming metrics for %s (community=%s, status=%d)",
            endpoint,
            community_id,
            status_code,
        )


# ---------------------------------------------------------------------------
# Streaming Helpers
# ---------------------------------------------------------------------------


async def _stream_ask_response(
    community_id: str,
    question: str,
    byok: ByokCredential | None,
    origin: str | None,
    user_id: str | None,
    page_context: PageContext | None = None,
    requested_model: str | None = None,
    http_request: Request | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response for ask endpoint with JSON-encoded SSE events.

    Event format:
        data: {"event": "content", "content": "text chunk"}
        data: {"event": "thinking"}
        data: {"event": "tool_start", "name": "tool_name", "input": {...}}
        data: {"event": "tool_end", "name": "tool_name", "output": {...}}
        data: {"event": "citation", "marker": 1, "source": "...", "title": "...", "cited_text": "..."}
        data: {"event": "done", "request_id": "...", "model": "...", "content": "final answer", "citations": [...]}
        data: {"event": "error", "message": "error text"}

    The `thinking` event is a liveness signal only -- it never carries the
    model's reasoning text (see src/agents/content.py's module docstring);
    clients that do not recognize it are expected to ignore it.

    A `citation` event fires the first time a source is cited, after the text
    block it supports; its marker text (e.g. "[1]") is also appended to the
    `content` stream at that same point so a client that ignores `citation`
    events still sees the marker inline. If an adapter surfaces a
    citation-only delta before that block's text, the assembler buffers it
    until the text arrives. The final `done.content` is authoritative and
    moves generated markers to sentence boundaries before clients render or
    persist the completed answer.
    """
    start_time = time.monotonic()
    tools_called: list[str] = []
    awm: AssistantWithMetrics | None = None
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cache_creation_tokens = 0
    citation_assembler = CitationAssembler()

    # Per-request id (set by metrics middleware) so the widget can attach feedback.
    request_id = getattr(http_request.state, "request_id", None) if http_request else None

    try:
        awm = create_community_assistant(
            community_id,
            byok=byok,
            origin=origin,
            user_id=user_id,
            requested_model=requested_model,
            preload_docs=True,
            page_context=page_context,
        )
        graph = awm.assistant.build_graph()

        state = {
            "messages": [HumanMessage(content=question)],
            "retrieved_docs": [],
            "tool_calls": [],
        }

        stream_config = awm.langfuse_config or {}
        full_response = ""
        async for event in graph.astream_events(state, version="v2", config=stream_config):
            kind = event.get("event")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk", {})
                raw_content = getattr(chunk, "content", None)
                if raw_content:
                    for block, block_index in classify_content_blocks_with_indices(raw_content):
                        if block.kind == "text":
                            if block.text:
                                full_response += block.text
                                sse_event = {"event": "content", "content": block.text}
                                yield f"data: {json.dumps(sse_event)}\n\n"
                            for sse_event in _build_citation_sse_events(
                                citation_assembler, block, block_index
                            ):
                                if sse_event["event"] == "content":
                                    full_response += encode_citation_markers(sse_event["content"])
                                yield f"data: {json.dumps(sse_event)}\n\n"
                        elif block.kind == "thinking":
                            yield f"data: {json.dumps({'event': 'thinking'})}\n\n"

            elif kind == "on_chat_model_end":
                inp, out, cache_read, cache_creation = _extract_token_usage(event.get("data", {}))
                total_input_tokens += inp
                total_output_tokens += out
                total_cache_read_tokens += cache_read
                total_cache_creation_tokens += cache_creation
                citation_assembler.finish_model_run()

            elif kind == "on_tool_start":
                tool_input = event.get("data", {}).get("input", {})
                tool_name = event.get("name", "")
                if tool_name:
                    tools_called.append(tool_name)
                sse_event = {
                    "event": "tool_start",
                    "name": tool_name,
                    "input": tool_input if isinstance(tool_input, dict) else {},
                }
                yield f"data: {json.dumps(sse_event)}\n\n"

            elif kind == "on_tool_end":
                tool_output = event.get("data", {}).get("output", {})
                sse_event = {
                    "event": "tool_end",
                    "name": event.get("name", ""),
                    "output": _sse_safe_tool_output(tool_output),
                }
                yield f"data: {json.dumps(sse_event)}\n\n"

        final_response = normalize_citation_markers(full_response, citation_assembler.marks)
        sse_event = {
            "event": "done",
            "request_id": request_id,
            "model": awm.model if awm else None,
            "content": final_response,
            "citations": [
                {
                    "marker": m.marker,
                    "source": m.source,
                    "title": m.title,
                    "cited_text": m.cited_text,
                }
                for m in citation_assembler.marks
            ],
        }
        yield f"data: {json.dumps(sse_event)}\n\n"

        # Log metrics at end of streaming
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=f"/{community_id}/ask",
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=200,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_read_tokens=total_cache_read_tokens,
            cache_creation_tokens=total_cache_creation_tokens,
        )

    except HTTPException as e:
        # HTTPException in streaming context (e.g., auth failure, rate limit).
        # Cannot re-raise because response headers are already sent as 200.
        logger.warning(
            "HTTP error in ask streaming for community %s: %d %s",
            community_id,
            e.status_code,
            e.detail,
        )
        sse_event = {"event": "error", "message": str(e.detail)}
        yield f"data: {json.dumps(sse_event)}\n\n"
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=f"/{community_id}/ask",
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=e.status_code,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_read_tokens=total_cache_read_tokens,
            cache_creation_tokens=total_cache_creation_tokens,
        )
    except ValueError as e:
        # Input validation errors - user's fault
        logger.warning("Invalid input in streaming for community %s: %s", community_id, e)
        sse_event = {
            "event": "error",
            "message": f"Invalid request: {str(e)}",
            "retryable": False,
        }
        yield f"data: {json.dumps(sse_event)}\n\n"
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=f"/{community_id}/ask",
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=400,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_read_tokens=total_cache_read_tokens,
            cache_creation_tokens=total_cache_creation_tokens,
        )
    except Exception as e:
        # Unexpected errors - log with full context
        error_id = str(uuid.uuid4())
        logger.error(
            "Unexpected streaming error (ID: %s) in ask endpoint for community %s: %s",
            error_id,
            community_id,
            e,
            exc_info=True,
            extra={
                "error_id": error_id,
                "community_id": community_id,
                "error_type": type(e).__name__,
            },
        )
        sse_event = {
            "event": "error",
            "message": "An error occurred while generating the response. Please try again.",
            "error_id": error_id,
        }
        yield f"data: {json.dumps(sse_event)}\n\n"
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=f"/{community_id}/ask",
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=500,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_read_tokens=total_cache_read_tokens,
            cache_creation_tokens=total_cache_creation_tokens,
        )


def _finish_with_tool_request(
    session: ChatSession,
    pending_payload: dict[str, Any],
    final_state: dict[str, Any] | None,
    *,
    content: str = "",
    citations: Sequence[CitationMark] = (),
    runs_before: int = 0,
) -> Iterator[str]:
    """End run 1 on a browser call: adopt the history, park the call, ask the client to run it.

    No `done` event follows, and that is deliberate. `done` means the assistant finished
    its turn, and this turn is not finished; it is waiting on a browser. A client that
    treated `tool_request` as a turn ending would render a reply that has not been
    written yet.

    The history is adopted whole from the graph's final state rather than reassembled
    from the stream, because the run produced more than text: the assistant message
    carrying `tool_calls`, results for any server tools called in the same batch, and a
    refusal for any second browser call. Rebuilding that from SSE events would be
    re-deriving what the graph already got right.
    """
    # Validate BEFORE mutating. Reversed, a malformed payload would leave the session
    # holding an assistant message with `tool_calls` and no parked call to answer it,
    # which the provider rejects on every later turn: the session would be permanently
    # dead with no way to repair it, because `abandon_pending_call` has nothing to
    # abandon. Build the call first and let it raise while the session is untouched.
    #
    # The citations and the run count travel with the parked call, so the run that
    # answers it continues the reply's numbering and knows its remaining budget.
    # `content` is this run's text with its markers normalized, which the reader would
    # otherwise only ever have in its raw streamed form.
    pending = PendingClientCall.from_state(
        pending_payload, carried_citations=citations, runs_before=runs_before
    )
    session.replace_history(final_state.get("messages", []) if final_state else [])
    session.set_pending_call(pending)
    yield f"data: {json.dumps(pending.to_request_event(session.session_id, content))}\n\n"


async def _stream_chat_response(
    community_id: str,
    session: ChatSession,
    byok: ByokCredential | None,
    origin: str | None,
    user_id: str | None,
    requested_model: str | None = None,
    page_context: PageContext | None = None,
    http_request: Request | None = None,
    declared_client_tools: set[str] | None = None,
    initial_messages: list[BaseMessage] | None = None,
    endpoint: str | None = None,
    carried_citations: Sequence[CitationMark] = (),
    browser_runs_answered: int = 0,
) -> AsyncGenerator[str, None]:
    """Stream assistant response as JSON-encoded Server-Sent Events.

    Event format:
        data: {"event": "content", "content": "text chunk"}
        data: {"event": "thinking"}
        data: {"event": "tool_start", "name": "tool_name", "input": {...}}
        data: {"event": "tool_end", "name": "tool_name", "output": {...}}
        data: {"event": "session", "session_id": "..."}  (sent first)
        data: {"event": "citation", "marker": 1, "source": "...", "title": "...", "cited_text": "..."}
        data: {"event": "warning", "message": "..."}  (optional, before done)
        data: {"event": "done", "session_id": "...", "request_id": "...", "model": "...", "content": "final answer", "citations": [...]}
        data: {"event": "error", "message": "error text"}

    The `thinking` event is a liveness signal only -- it never carries the
    model's reasoning text (see src/agents/content.py's module docstring);
    clients that do not recognize it are expected to ignore it.

    A `citation` event fires the first time a source is cited after the text
    block it supports; its marker text is also appended to the `content`
    stream at that point (see _stream_ask_response's docstring for the full
    rationale). `done.content` is authoritative and contains the normalized
    answer that is persisted in session history, while `done.citations`
    repeats the full citation list.

    A run that ends on a browser call sends `tool_request` instead of `done`,
    carrying that run's normalized `content` and every citation the reply has so
    far. The next run (`/chat/resume`) passes those back as `carried_citations` and
    continues the numbering, so a reply of several runs numbers its sources as the
    one reply the reader sees: the final `done.citations` lists every run's sources,
    and `done.content` carries only the final run's text.

    `browser_runs_answered` is how many browser results this reply has already sent
    back; the run may request at most `MAX_BROWSER_RUNS_PER_REPLY` in total.
    """
    start_time = time.monotonic()
    tools_called: list[str] = []
    awm: AssistantWithMetrics | None = None
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cache_creation_tokens = 0

    # The metrics middleware assigns a per-request UUID; expose it only on the
    # final `done` event (below) so the widget attaches it only to a reply that
    # completed normally. Error paths yield an `error` event instead and never
    # reach `done`, so a partially-streamed or fully-errored reply carries no
    # request_id. This also joins per-response feedback back to request_log.
    request_id = getattr(http_request.state, "request_id", None) if http_request else None

    # The label this turn is recorded under. `_stream_chat_response` is shared by run 1
    # (`/chat`) and run 2 (`/chat/resume`), and the metrics middleware skips its own
    # logging once this path has logged, so what is written here is the ONLY record of
    # the request. Hardcoding "/chat" folded every resume, the half that actually spends
    # the credential, into `/chat`'s numbers and made a browser turn's cost unmeasurable.
    metrics_endpoint = endpoint or f"/{community_id}/chat"

    # Send session_id immediately so the client captures it even if the
    # stream is truncated by a proxy timeout.
    sse_event = {"event": "session", "session_id": session.session_id}
    yield f"data: {json.dumps(sse_event)}\n\n"

    if not session.begin_turn():
        # Refusing is the honest answer. Running anyway means both turns write the
        # whole message list back and the later one silently discards the earlier,
        # including any browser call it had parked.
        logger.info(
            "Refused an overlapping turn on session %s",
            session.session_id,
            extra={"session_id": session.session_id, "reason": "turn_in_flight"},
        )
        yield (
            "data: "
            + json.dumps(
                {
                    "event": "error",
                    "message": (
                        "This conversation is already working on a reply. Wait for it "
                        "to finish before sending another message."
                    ),
                }
            )
            + "\n\n"
        )
        return

    try:
        # Built inside the handlers, not above them: the response headers went out
        # with the `session` event, so anything raised before this `try` would end
        # the stream with no error event and nothing logged.
        try:
            citation_assembler = CitationAssembler(carried_citations)
        except ValueError as e:
            # Not a limit the reader hit, which is what the ValueError handler below
            # reports verbatim: the parked call carried malformed citations, which is
            # a server fault and goes to the generic handler with an error id.
            raise RuntimeError(f"the parked call's citations are malformed: {e}") from e

        awm = create_community_assistant(
            community_id,
            byok=byok,
            origin=origin,
            user_id=user_id,
            requested_model=requested_model,
            preload_docs=True,
            page_context=page_context,
            declared_client_tools=declared_client_tools,
            browser_runs_left=MAX_BROWSER_RUNS_PER_REPLY - browser_runs_answered,
        )
        graph = awm.assistant.build_graph()

        # `initial_messages` is how run 2 of a browser-execution turn seeds the graph:
        # the stored history plus the live tool result, whose images are attached here
        # and deliberately never written back to the session.
        state = {
            "messages": (
                list(initial_messages) if initial_messages is not None else session.messages.copy()
            ),
            "retrieved_docs": [],
            "tool_calls": [],
        }

        stream_config = awm.langfuse_config or {}
        full_response = ""
        final_state: dict[str, Any] | None = None
        # Whether the run ENDED on the client_tools node, tracked as the last graph
        # node to finish. A parked call ends the run there; a node that refused
        # every client call hands back to the agent, which then finishes after
        # it. "The node ran" stopped implying "a call was parked" once refused
        # calls started going back to the model.
        ended_on_client_tools_node = False

        async for event in graph.astream_events(state, version="v2", config=stream_config):
            kind = event.get("event")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk", {})
                raw_content = getattr(chunk, "content", None)
                if raw_content:
                    for block, block_index in classify_content_blocks_with_indices(raw_content):
                        if block.kind == "text":
                            if block.text:
                                full_response += block.text
                                sse_event = {"event": "content", "content": block.text}
                                yield f"data: {json.dumps(sse_event)}\n\n"
                            for sse_event in _build_citation_sse_events(
                                citation_assembler, block, block_index
                            ):
                                if sse_event["event"] == "content":
                                    full_response += encode_citation_markers(sse_event["content"])
                                yield f"data: {json.dumps(sse_event)}\n\n"
                        elif block.kind == "thinking":
                            yield f"data: {json.dumps({'event': 'thinking'})}\n\n"

            elif kind == "on_chat_model_end":
                inp, out, cache_read, cache_creation = _extract_token_usage(event.get("data", {}))
                total_input_tokens += inp
                total_output_tokens += out
                total_cache_read_tokens += cache_read
                total_cache_creation_tokens += cache_creation
                citation_assembler.finish_model_run()

            elif kind == "on_tool_start":
                tool_input = event.get("data", {}).get("input", {})
                tool_name = event.get("name", "")
                if tool_name:
                    tools_called.append(tool_name)
                sse_event = {
                    "event": "tool_start",
                    "name": tool_name,
                    "input": tool_input if isinstance(tool_input, dict) else {},
                }
                yield f"data: {json.dumps(sse_event)}\n\n"

            elif kind == "on_tool_end":
                tool_output = event.get("data", {}).get("output", {})
                sse_event = {
                    "event": "tool_end",
                    "name": event.get("name", ""),
                    "output": _sse_safe_tool_output(tool_output),
                }
                yield f"data: {json.dumps(sse_event)}\n\n"

            elif kind == "on_chain_end":
                if not event.get("parent_ids"):
                    # The root graph's own end event carries the complete final state:
                    # every message the run produced, plus `pending_client_call` when
                    # it ended on a browser call. Identified by an empty `parent_ids`
                    # rather than by the name "LangGraph", which is an implementation
                    # detail a rename would quietly change.
                    output = event.get("data", {}).get("output")
                    if isinstance(output, dict) and "messages" in output:
                        final_state = output
                elif event.get("name") == CLIENT_TOOLS_NODE:
                    # Independent evidence that a browser call was parked, read from a
                    # different event than the state is. If the run ended on this node
                    # and the state did not come back, the two disagree, and that is
                    # checked below rather than left to fall through.
                    ended_on_client_tools_node = True
                elif event.get("name") in ("agent", "tools"):
                    # A node after client_tools means the run continued past it,
                    # so nothing was parked there.
                    ended_on_client_tools_node = False

        pending_payload = (final_state or {}).get("pending_client_call")

        if ended_on_client_tools_node and not pending_payload:
            # The run ended on the node that parks a browser call, and the state that
            # should carry the parked call did not come back. Almost certainly langgraph changed the
            # shape of its events, which is a dependency bump away at any time.
            #
            # This is raised rather than allowed to fall through, and that is the whole
            # point. Falling through produces a turn that looks completely normal: the
            # model's tool-calling message has no text, so `final_response` is empty,
            # nothing is persisted, and the client receives an ordinary `done`. The
            # model's request to run code is dropped permanently, platform-wide, with
            # every metric green and nothing to alert on.
            error_id = str(uuid.uuid4())
            logger.error(
                "Browser call was parked but the graph state did not carry it "
                "(ID: %s, session: %s, community: %s). The langgraph event shape has "
                "probably changed; see _stream_chat_response's on_chain_end branch.",
                error_id,
                session.session_id,
                community_id,
                extra={"error_id": error_id, "community_id": community_id},
            )
            yield f"data: {json.dumps({'event': 'error', 'message': 'The assistant could not start a browser task.', 'error_id': error_id})}\n\n"
            return

        if pending_payload:
            # Recorded like any other tool use. `on_tool_start` never fires for a
            # client tool, on either run: run 1 parks it instead of invoking it, and
            # run 2 already has the result in the message list. Without this, per-tool
            # usage metrics read zero for browser execution forever, which is exactly
            # the number someone would later build a "which tools get used" dashboard
            # on and believe.
            tools_called.append(str(pending_payload.get("tool", "")))
            for sse_line in _finish_with_tool_request(
                session,
                pending_payload,
                final_state,
                content=normalize_citation_markers(full_response, citation_assembler.marks),
                citations=citation_assembler.marks,
                runs_before=browser_runs_answered,
            ):
                yield sse_line
            _log_streaming_metrics(
                http_request=http_request,
                community_id=community_id,
                endpoint=metrics_endpoint,
                awm=awm,
                tools_called=tools_called,
                start_time=start_time,
                status_code=200,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cache_read_tokens=total_cache_read_tokens,
                cache_creation_tokens=total_cache_creation_tokens,
            )
            return

        final_response = normalize_citation_markers(full_response, citation_assembler.marks)
        if final_response:
            try:
                session.add_assistant_message(final_response)
            except ValueError as e:
                # Session limit exceeded
                logger.error("Session limit exceeded in streaming: %s", e)
                sse_event = {"event": "error", "message": str(e)}
                yield f"data: {json.dumps(sse_event)}\n\n"
                return

        # Warn if conversation is approaching the token budget (87.5% of 80K).
        warning_threshold = int(DEFAULT_MAX_CONVERSATION_TOKENS * 0.875)
        # The same counter the agent budgets with, so the warning fires on the same
        # arithmetic the trimmer acts on. Two counters would mean warning at one
        # threshold and trimming at another.
        approx_tokens = count_conversation_tokens(session.messages)
        if approx_tokens > warning_threshold:
            sse_event = {
                "event": "warning",
                "message": "Conversation is getting long. Consider starting a new chat for best results.",
            }
            yield f"data: {json.dumps(sse_event)}\n\n"

        sse_event = {
            "event": "done",
            "session_id": session.session_id,
            "request_id": request_id,
            "model": awm.model if awm else None,
            "content": final_response,
            "citations": [
                {
                    "marker": m.marker,
                    "source": m.source,
                    "title": m.title,
                    "cited_text": m.cited_text,
                }
                for m in citation_assembler.marks
            ],
        }
        yield f"data: {json.dumps(sse_event)}\n\n"

        # Log metrics at end of streaming
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=metrics_endpoint,
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=200,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_read_tokens=total_cache_read_tokens,
            cache_creation_tokens=total_cache_creation_tokens,
        )

    except HTTPException as e:
        # HTTPException in streaming context (e.g., auth failure, rate limit).
        # Cannot re-raise because response headers are already sent as 200.
        logger.warning(
            "HTTP error in chat streaming for session %s (community: %s): %d %s",
            session.session_id,
            community_id,
            e.status_code,
            e.detail,
        )
        sse_event = {"event": "error", "message": str(e.detail)}
        yield f"data: {json.dumps(sse_event)}\n\n"
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=metrics_endpoint,
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=e.status_code,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_read_tokens=total_cache_read_tokens,
            cache_creation_tokens=total_cache_creation_tokens,
        )
    except ValueError as e:
        # Session limit errors
        logger.error("Session limit error: %s", e)
        sse_event = {"event": "error", "message": str(e)}
        yield f"data: {json.dumps(sse_event)}\n\n"
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=metrics_endpoint,
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=400,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_read_tokens=total_cache_read_tokens,
            cache_creation_tokens=total_cache_creation_tokens,
        )
    except Exception as e:
        error_id = str(uuid.uuid4())
        logger.error(
            "Unexpected streaming error (ID: %s) in chat endpoint for session %s (community: %s): %s",
            error_id,
            session.session_id,
            community_id,
            e,
            exc_info=True,
            extra={
                "error_id": error_id,
                "community_id": community_id,
                "error_type": type(e).__name__,
            },
        )
        sse_event = {
            "event": "error",
            "message": "An error occurred while processing your request.",
            "error_id": error_id,
        }
        yield f"data: {json.dumps(sse_event)}\n\n"
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=metrics_endpoint,
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=500,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_read_tokens=total_cache_read_tokens,
            cache_creation_tokens=total_cache_creation_tokens,
        )
    finally:
        # Released however this generator ends: normal return, error, or the client
        # dropping the connection, which closes the generator and runs this.
        session.end_turn()
