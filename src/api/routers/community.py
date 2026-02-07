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
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field, field_validator

from src.api.config import get_settings
from src.api.security import AuthScope, RequireAuth, RequireScopedAuth
from src.assistants import registry
from src.assistants.community import CommunityAssistant
from src.assistants.community import PageContext as AgentPageContext
from src.assistants.registry import AssistantInfo
from src.core.services.litellm_llm import create_openrouter_llm
from src.metrics.cost import estimate_cost
from src.metrics.db import (
    RequestLogEntry,
    extract_token_usage,
    extract_tool_names,
    log_request,
    metrics_connection,
    now_iso,
)
from src.metrics.queries import (
    get_community_summary,
    get_public_community_summary,
    get_public_usage_stats,
    get_quality_metrics,
    get_quality_summary,
    get_usage_stats,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Models (shared across all community routers)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single chat message."""

    role: Literal["user", "assistant"] = Field(..., description="Message role")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Request body for chat endpoint."""

    message: str = Field(..., description="User message", min_length=1)
    session_id: str | None = Field(
        default=None,
        description="Session ID for conversation continuity. If not provided, a new session is created.",
    )
    stream: bool = Field(default=True, description="Whether to stream the response")
    model: str | None = Field(
        default=None,
        description="Optional model override (OpenRouter format: creator/model-name). Requires BYOK.",
    )


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


class AskRequest(BaseModel):
    """Request body for single question (ask) endpoint."""

    question: str = Field(..., description="Question to ask", min_length=1)
    stream: bool = Field(default=False, description="Whether to stream the response")
    page_context: PageContext | None = Field(
        default=None,
        description="Optional context about the page where the widget is embedded",
    )
    model: str | None = Field(
        default=None,
        description="Optional model override (OpenRouter format: creator/model-name). Requires BYOK.",
    )


class ToolCallInfo(BaseModel):
    """Information about a tool call made during response generation."""

    name: str = Field(..., description="Tool name")
    args: dict = Field(default_factory=dict, description="Tool arguments")


class ChatResponse(BaseModel):
    """Response body for chat/ask endpoints."""

    session_id: str = Field(..., description="Session ID for follow-up messages")
    message: ChatMessage = Field(..., description="Assistant response")
    tool_calls: list[ToolCallInfo] = Field(
        default_factory=list, description="Tools called during response generation"
    )


class AskResponse(BaseModel):
    """Response body for single question endpoint."""

    answer: str = Field(..., description="Assistant's answer")
    tool_calls: list[ToolCallInfo] = Field(
        default_factory=list, description="Tools called during response generation"
    )


class SessionInfo(BaseModel):
    """Information about a chat session."""

    session_id: str = Field(..., description="Unique session identifier", min_length=1)
    community_id: str = Field(..., description="Community this session belongs to", min_length=1)
    message_count: int = Field(..., description="Number of messages in session", ge=0)
    created_at: str = Field(..., description="ISO timestamp when session was created")
    last_active: str = Field(..., description="ISO timestamp of last activity")


class CommunityConfigResponse(BaseModel):
    """Community configuration information."""

    id: str = Field(..., description="Community identifier")
    name: str = Field(..., description="Community display name")
    description: str = Field(..., description="Community description")
    default_model: str = Field(..., description="Default LLM model for this community")
    default_model_provider: str | None = Field(
        default=None, description="Default provider for model routing"
    )


# ---------------------------------------------------------------------------
# Session Management (In-Memory, per-community isolation)
# ---------------------------------------------------------------------------

# Session limits and constraints
MAX_SESSIONS_PER_COMMUNITY = 1000  # Prevent memory exhaustion
SESSION_TTL_HOURS = 24  # Auto-delete inactive sessions after 24h
MAX_MESSAGES_PER_SESSION = 100  # Limit conversation length
MAX_MESSAGE_LENGTH = 10000  # Max characters per message


class ChatSession:
    """A chat session with message history.

    Enforces constraints:
    - Max messages per session: 100
    - Max message length: 10,000 characters
    - TTL: 24 hours from last activity
    """

    def __init__(self, session_id: str, community_id: str) -> None:
        self.session_id = session_id
        self.community_id = community_id
        self.messages: list[HumanMessage | AIMessage] = []
        self.created_at = datetime.now(UTC)
        self.last_active = self.created_at

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
    """Remove least-recently-used session when limit is reached."""
    store = _get_session_store(community_id)
    if not store:
        return

    # Find session with oldest last_active timestamp
    lru_id = min(store.keys(), key=lambda sid: store[sid].last_active)
    del store[lru_id]
    logger.warning(
        "Evicted LRU session %s from community %s (limit: %d)",
        lru_id,
        community_id,
        MAX_SESSIONS_PER_COMMUNITY,
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
    1. Platform default origins (demo.osc.earth, *.osc.earth, and legacy pages.dev)
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
        "https://*.demo.osc.earth",
        "https://*.osc.earth",
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


def _select_api_key(
    community_id: str,
    byok: str | None,
    origin: str | None,
) -> tuple[str, str]:
    """Select API key based on BYOK and origin authorization.

    **Authorization Logic:**
    1. If BYOK provided → use it (always allowed)
    2. If origin matches community CORS → allow fallback to community/platform key
    3. Otherwise → reject (CLI or unauthorized origin must provide BYOK)

    This ensures:
    - CLI users must provide their own key
    - Widget users on authorized sites can use platform keys
    - Custom model requests require BYOK (checked separately)

    Args:
        community_id: Community identifier
        byok: User-provided API key from X-OpenRouter-Key header
        origin: Origin header from HTTP request

    Returns:
        Tuple of (api_key, source) where source is "byok", "community", or "platform"

    Raises:
        HTTPException(403): If origin is not authorized and BYOK is not provided
        HTTPException(500): If no platform API key is configured and no other key is available
    """
    # Case 1: BYOK provided - always allowed
    if byok:
        logger.debug(
            "Using BYOK for community %s",
            community_id,
            extra={"community_id": community_id, "key_source": "byok"},
        )
        return (byok, "byok")

    # Case 2: Check if origin is authorized for platform key usage
    if not _is_authorized_origin(origin, community_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "API key required. Please provide your OpenRouter API key via the X-OpenRouter-Key header. "
                "Get your key at: https://openrouter.ai/keys"
            ),
        )

    # Origin is authorized - allow fallback to community/platform keys
    settings = get_settings()
    community_info = registry.get(community_id)

    # Try community-specific key first
    if community_info and community_info.community_config:
        env_var = community_info.community_config.openrouter_api_key_env_var
        if env_var:
            community_key = os.getenv(env_var)
            if community_key:
                logger.info(
                    "Using community-specific API key from %s for %s",
                    env_var,
                    community_id,
                    extra={
                        "community_id": community_id,
                        "key_source": "community",
                        "env_var": env_var,
                    },
                )
                return (community_key, "community")
            logger.error(
                "Community %s configured to use %s but env var not set, falling back to platform key. "
                "This may incur unexpected costs. Set the environment variable to fix this.",
                community_id,
                env_var,
                extra={
                    "community_id": community_id,
                    "key_source": "platform",
                    "configured_env_var": env_var,
                    "env_var_missing": True,
                    "fallback_to_platform": True,
                    "origin": origin,
                },
            )

    # Fall back to platform key
    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=500,
            detail="No API key configured for this community. Please contact support.",
        )

    logger.debug(
        "Using platform API key for community %s",
        community_id,
        extra={"community_id": community_id, "key_source": "platform"},
    )
    return (settings.openrouter_api_key, "platform")


def _select_model(
    community_info: AssistantInfo,
    requested_model: str | None,
    has_byok: bool,
) -> tuple[str, str | None]:
    """Select model based on community config and user request.

    **Model Selection Logic:**
    1. If user requests custom model:
       - Must have BYOK (otherwise reject)
       - Use requested model
    2. Else if community has default_model → use it
    3. Else → use platform default_model

    This ensures:
    - Custom models always require BYOK (prevents abuse)
    - Communities can have preferred models
    - Platform default is the fallback

    Args:
        community_info: Community information from registry
        requested_model: User-requested model from request body
        has_byok: Whether user provided their own API key

    Returns:
        Tuple of (model, provider)

    Raises:
        HTTPException(403): If custom model requested without BYOK
    """
    settings = get_settings()

    # Determine the default model for this community
    default_model = settings.default_model
    default_provider = settings.default_model_provider
    if community_info.community_config and community_info.community_config.default_model:
        default_model = community_info.community_config.default_model
        default_provider = community_info.community_config.default_model_provider

    # If user requests a custom model, require BYOK
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
        # User has BYOK, allow custom model
        return (requested_model, None)  # Custom model uses default routing

    # Use community or platform default
    return (default_model, default_provider)


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
    key_source: str
    langfuse_config: dict | None = None
    langfuse_trace_id: str | None = None


def create_community_assistant(
    community_id: str,
    byok: str | None = None,
    origin: str | None = None,
    user_id: str | None = None,
    requested_model: str | None = None,
    preload_docs: bool = True,
    page_context: PageContext | None = None,
) -> AssistantWithMetrics:
    """Create a community assistant instance with authorization checks.

    **Authorization:**
    - If BYOK provided -> always allowed
    - If origin matches community CORS -> can use community/platform keys
    - Otherwise -> rejects with 403 (CLI/unauthorized must provide BYOK)

    **Model Selection:**
    - Custom model requests require BYOK
    - Otherwise uses community default_model or platform default_model

    Args:
        community_id: The community identifier (e.g., "hed", "bids")
        byok: User-provided API key from X-OpenRouter-Key header
        origin: Origin header from HTTP request (for CORS authorization)
        user_id: User ID for cache optimization (sticky routing)
        requested_model: Optional model override from request body
        preload_docs: Whether to preload documents
        page_context: Optional context about the page where the widget is embedded

    Returns:
        AssistantWithMetrics containing the assistant, resolved model, and key source.
        Access the assistant via .assistant attribute.

    Raises:
        ValueError: If community_id is not registered
        HTTPException(403): If authorization fails or custom model requested without BYOK
    """
    community_info = registry.get(community_id)
    if community_info is None:
        raise ValueError(f"Unknown community: {community_id}")

    settings = get_settings()

    # Select API key with authorization checks
    effective_api_key, key_source = _select_api_key(community_id, byok, origin)
    logger.debug(
        "Using %s API key",
        key_source,
        extra={"community_id": community_id, "origin": origin, "key_source": key_source},
    )

    # Select model (checks BYOK requirement for custom models)
    selected_model, selected_provider = _select_model(
        community_info, requested_model, has_byok=bool(byok)
    )
    logger.debug(
        "Using model %s",
        selected_model,
        extra={"community_id": community_id, "origin": origin, "model": selected_model},
    )

    # Determine user_id for prompt caching optimization
    cache_user_id = _get_cache_user_id(community_id, byok, user_id)

    model = create_openrouter_llm(
        model=selected_model,
        api_key=effective_api_key,
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
        except Exception:
            logger.warning(
                "LangFuse tracing setup failed for %s, continuing without it",
                community_id,
                exc_info=True,
            )

    return AssistantWithMetrics(
        assistant=assistant,
        model=selected_model,
        key_source=key_source,
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


def _extract_agent_result(result: dict) -> AgentResult:
    """Extract response content, tool calls, and token usage from agent result.

    Consolidates the common post-invocation logic shared by ask and chat endpoints.
    """
    response_content = ""
    if result.get("messages"):
        last_msg = result["messages"][-1]
        if isinstance(last_msg, AIMessage):
            content = last_msg.content
            response_content = content if isinstance(content, str) else str(content)

    tools_called = extract_tool_names(result)
    tool_calls_info = [
        ToolCallInfo(name=tc.get("name", ""), args=tc.get("args", {}))
        for tc in result.get("tool_calls", [])
    ]

    inp, out, total = extract_token_usage(result)
    return AgentResult(
        response_content=response_content,
        tool_calls_info=tool_calls_info,
        tools_called=tools_called,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
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
            awm.model, agent_result.input_tokens, agent_result.output_tokens
        ),
        "tools_called": agent_result.tools_called,
        "tool_call_count": len(agent_result.tools_called),
        "langfuse_trace_id": awm.langfuse_trace_id,
        "stream": False,
    }


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------


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
        x_openrouter_key: Annotated[str | None, Header(alias="X-OpenRouter-Key")] = None,
        x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    ) -> AskResponse | StreamingResponse:
        """Ask a single question to the community assistant.

        This endpoint is for one-off questions without conversation history.
        For multi-turn conversations, use the /chat endpoint.

        **BYOK (Bring Your Own Key):**
        Pass your OpenRouter API key in the `X-OpenRouter-Key` header.
        Required for CLI usage and custom model requests.

        **Custom Models:**
        Specify a custom model via the `model` field in the request body.
        Custom models require BYOK.

        **Cache Optimization:**
        Pass a stable user ID in the `X-User-ID` header for better cache hit rates.
        """
        # Extract origin for authorization
        origin = http_request.headers.get("origin")

        if body.stream:
            return StreamingResponse(
                _stream_ask_response(
                    community_id,
                    body.question,
                    x_openrouter_key,
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
                byok=x_openrouter_key,
                origin=origin,
                user_id=x_user_id,
                requested_model=body.model,
                page_context=body.page_context,
            )
            messages = [HumanMessage(content=body.question)]
            result = await awm.assistant.ainvoke(messages, config=awm.langfuse_config)

            ar = _extract_agent_result(result)
            _set_metrics_on_request(http_request, awm, ar)

            return AskResponse(answer=ar.response_content, tool_calls=ar.tool_calls_info)

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
        x_openrouter_key: Annotated[str | None, Header(alias="X-OpenRouter-Key")] = None,
        x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    ) -> ChatResponse | StreamingResponse:
        """Chat with the community assistant.

        Supports multi-turn conversations with session persistence.

        **BYOK (Bring Your Own Key):**
        Pass your OpenRouter API key in the `X-OpenRouter-Key` header.
        Required for CLI usage and custom model requests.

        **Custom Models:**
        Specify a custom model via the `model` field in the request body.
        Custom models require BYOK.

        **Cache Optimization:**
        Pass a stable user ID in the `X-User-ID` header for better cache hit rates.
        """
        # Extract origin for authorization
        origin = http_request.headers.get("origin")

        session = get_or_create_session(community_id, body.session_id)
        user_id = x_user_id or session.session_id

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
                    x_openrouter_key,
                    origin,
                    user_id,
                    body.model,
                    http_request=http_request,
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
                byok=x_openrouter_key,
                origin=origin,
                user_id=user_id,
                requested_model=body.model,
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

    @router.get("/sessions/{session_id}", response_model=SessionInfo)
    async def get_session_info(session_id: str, _auth: RequireAuth) -> SessionInfo:
        """Get information about a chat session."""
        session = get_session(community_id, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session.to_info()

    @router.delete("/sessions/{session_id}")
    async def delete_session_endpoint(session_id: str, _auth: RequireAuth) -> dict[str, str]:
        """Delete a chat session."""
        if not delete_session(community_id, session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "deleted", "session_id": session_id}

    @router.get("/sessions", response_model=list[SessionInfo])
    async def list_sessions_endpoint(_auth: RequireAuth) -> list[SessionInfo]:
        """List all active chat sessions for this community."""
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

        return CommunityConfigResponse(
            id=info.id,
            name=info.name,
            description=info.description,
            default_model=default_model,
            default_model_provider=default_provider,
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

        Returns request counts, error rate, and top tools.
        No tokens, costs, or model information exposed.
        """
        try:
            with metrics_connection() as conn:
                return get_public_community_summary(community_id, conn)
        except sqlite3.Error:
            logger.exception("Failed to query public metrics for community %s", community_id)
            raise HTTPException(
                status_code=503,
                detail="Metrics database is temporarily unavailable.",
            )

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

    return router


# ---------------------------------------------------------------------------
# Metrics Helpers
# ---------------------------------------------------------------------------


def _log_streaming_metrics(
    http_request: Request | None,
    community_id: str,
    endpoint: str,
    awm: AssistantWithMetrics | None,
    tools_called: list[str],
    start_time: float,
    status_code: int,
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

        entry = RequestLogEntry(
            request_id=request_id,
            timestamp=now_iso(),
            endpoint=endpoint,
            method="POST",
            community_id=community_id,
            duration_ms=round(duration_ms, 1),
            status_code=status_code,
            model=awm.model if awm else None,
            key_source=awm.key_source if awm else None,
            tools_called=tools_called,
            stream=True,
            tool_call_count=len(tools_called),
            langfuse_trace_id=awm.langfuse_trace_id if awm else None,
        )
        log_request(entry)
    except Exception:
        logger.exception("Failed to log streaming metrics for %s", endpoint)


# ---------------------------------------------------------------------------
# Streaming Helpers
# ---------------------------------------------------------------------------


async def _stream_ask_response(
    community_id: str,
    question: str,
    byok: str | None,
    origin: str | None,
    user_id: str | None,
    page_context: PageContext | None = None,
    requested_model: str | None = None,
    http_request: Request | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response for ask endpoint with JSON-encoded SSE events.

    Event format:
        data: {"event": "content", "content": "text chunk"}
        data: {"event": "tool_start", "name": "tool_name", "input": {...}}
        data: {"event": "tool_end", "name": "tool_name", "output": {...}}
        data: {"event": "done"}
        data: {"event": "error", "message": "error text"}
    """
    start_time = time.monotonic()
    tools_called: list[str] = []
    awm: AssistantWithMetrics | None = None

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
        async for event in graph.astream_events(state, version="v2", config=stream_config):
            kind = event.get("event")

            if kind == "on_chat_model_stream":
                content = event.get("data", {}).get("chunk", {})
                if hasattr(content, "content") and content.content:
                    sse_event = {"event": "content", "content": content.content}
                    yield f"data: {json.dumps(sse_event)}\n\n"

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
                    "output": str(tool_output) if tool_output else "",
                }
                yield f"data: {json.dumps(sse_event)}\n\n"

        sse_event = {"event": "done"}
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
        )


async def _stream_chat_response(
    community_id: str,
    session: ChatSession,
    byok: str | None,
    origin: str | None,
    user_id: str | None,
    requested_model: str | None = None,
    http_request: Request | None = None,
) -> AsyncGenerator[str, None]:
    """Stream assistant response as JSON-encoded Server-Sent Events.

    Event format:
        data: {"event": "content", "content": "text chunk"}
        data: {"event": "tool_start", "name": "tool_name", "input": {...}}
        data: {"event": "tool_end", "name": "tool_name", "output": {...}}
        data: {"event": "done", "session_id": "..."}
        data: {"event": "error", "message": "error text"}
    """
    start_time = time.monotonic()
    tools_called: list[str] = []
    awm: AssistantWithMetrics | None = None

    try:
        awm = create_community_assistant(
            community_id,
            byok=byok,
            origin=origin,
            user_id=user_id,
            requested_model=requested_model,
            preload_docs=True,
        )
        graph = awm.assistant.build_graph()

        state = {
            "messages": session.messages.copy(),
            "retrieved_docs": [],
            "tool_calls": [],
        }

        stream_config = awm.langfuse_config or {}
        full_response = ""

        async for event in graph.astream_events(state, version="v2", config=stream_config):
            kind = event.get("event")

            if kind == "on_chat_model_stream":
                content = event.get("data", {}).get("chunk", {})
                if hasattr(content, "content") and content.content:
                    chunk = content.content
                    full_response += chunk
                    sse_event = {"event": "content", "content": chunk}
                    yield f"data: {json.dumps(sse_event)}\n\n"

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
                    "output": str(tool_output) if tool_output else "",
                }
                yield f"data: {json.dumps(sse_event)}\n\n"

        if full_response:
            try:
                session.add_assistant_message(full_response)
            except ValueError as e:
                # Session limit exceeded
                logger.error("Session limit exceeded in streaming: %s", e)
                sse_event = {"event": "error", "message": str(e)}
                yield f"data: {json.dumps(sse_event)}\n\n"
                return

        sse_event = {"event": "done", "session_id": session.session_id}
        yield f"data: {json.dumps(sse_event)}\n\n"

        # Log metrics at end of streaming
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=f"/{community_id}/chat",
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=200,
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
            endpoint=f"/{community_id}/chat",
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=e.status_code,
        )
    except ValueError as e:
        # Session limit errors
        logger.error("Session limit error: %s", e)
        sse_event = {"event": "error", "message": str(e)}
        yield f"data: {json.dumps(sse_event)}\n\n"
        _log_streaming_metrics(
            http_request=http_request,
            community_id=community_id,
            endpoint=f"/{community_id}/chat",
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=400,
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
            endpoint=f"/{community_id}/chat",
            awm=awm,
            tools_called=tools_called,
            start_time=start_time,
            status_code=500,
        )
