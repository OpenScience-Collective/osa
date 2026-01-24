"""Generic community assistant API router factory.

Creates parameterized routers for any registered community.
Each community gets endpoints like /{community_id}/ask, /{community_id}/chat, etc.
"""

import hashlib
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field, field_validator

from src.api.config import get_settings
from src.api.security import RequireAuth
from src.assistants import registry
from src.assistants.community import CommunityAssistant
from src.assistants.community import PageContext as AgentPageContext
from src.assistants.registry import AssistantInfo
from src.core.services.litellm_llm import create_openrouter_llm

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
    store = _get_session_store(community_id)
    # Filter out expired sessions
    active_sessions = [s for s in store.values() if not s.is_expired()]
    # Clean up expired sessions from store
    expired = [sid for sid, s in store.items() if s.is_expired()]
    for sid in expired:
        del store[sid]
    if expired:
        logger.info("Cleaned %d expired sessions from %s", len(expired), community_id)
    return active_sessions


# ---------------------------------------------------------------------------
# Assistant Factory
# ---------------------------------------------------------------------------


def _is_authorized_origin(origin: str | None, community_id: str) -> bool:
    """Check if Origin header matches community's allowed CORS origins.

    This determines if a request is coming from an authorized widget embed
    (vs CLI, unauthorized web page, or API client).

    Args:
        origin: Origin header from HTTP request (e.g., "https://hedtags.org")
        community_id: Community identifier

    Returns:
        True if origin matches community's cors_origins, False otherwise.
        Returns False if origin is None (CLI, mobile apps, browser extensions).
    """
    if not origin:
        return False

    import re

    community_info = registry.get(community_id)
    if not community_info or not community_info.community_config:
        return False

    cors_origins = community_info.community_config.cors_origins
    if not cors_origins:
        return False

    # Check exact matches first
    for allowed in cors_origins:
        if "*" not in allowed and origin == allowed:
            return True

    # Check wildcard patterns
    for allowed in cors_origins:
        if "*" in allowed:
            # Convert wildcard pattern to regex (same logic as main.py)
            escaped = re.escape(allowed)
            pattern = escaped.replace(r"\*", r"[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?")
            if re.fullmatch(pattern, origin):
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
    import os

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


def create_community_assistant(
    community_id: str,
    byok: str | None = None,
    origin: str | None = None,
    user_id: str | None = None,
    requested_model: str | None = None,
    preload_docs: bool = True,
    page_context: PageContext | None = None,
) -> CommunityAssistant:
    """Create a community assistant instance with authorization checks.

    **Authorization:**
    - If BYOK provided → always allowed
    - If origin matches community CORS → can use community/platform keys
    - Otherwise → rejects with 403 (CLI/unauthorized must provide BYOK)

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
        Configured CommunityAssistant instance

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
        )

    return registry.create_assistant(
        community_id,
        model=model,
        preload_docs=preload_docs,
        page_context=agent_page_context,
    )


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
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        try:
            assistant = create_community_assistant(
                community_id,
                byok=x_openrouter_key,
                origin=origin,
                user_id=x_user_id,
                requested_model=body.model,
                page_context=body.page_context,
            )
            messages = [HumanMessage(content=body.question)]
            result = await assistant.ainvoke(messages)

            response_content = ""
            if result.get("messages"):
                last_msg = result["messages"][-1]
                if isinstance(last_msg, AIMessage):
                    # Handle both string and list content (multimodal responses)
                    content = last_msg.content
                    response_content = content if isinstance(content, str) else str(content)

            tool_calls_info = [
                ToolCallInfo(name=tc.get("name", ""), args=tc.get("args", {}))
                for tc in result.get("tool_calls", [])
            ]

            return AskResponse(answer=response_content, tool_calls=tool_calls_info)

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
                    community_id, session, x_openrouter_key, origin, user_id, body.model
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Session-ID": session.session_id,
                },
            )

        try:
            assistant = create_community_assistant(
                community_id,
                byok=x_openrouter_key,
                origin=origin,
                user_id=user_id,
                requested_model=body.model,
            )
            result = await assistant.ainvoke(session.messages)

            response_content = ""
            if result.get("messages"):
                last_msg = result["messages"][-1]
                if isinstance(last_msg, AIMessage):
                    # Handle both string and list content (multimodal responses)
                    content = last_msg.content
                    response_content = content if isinstance(content, str) else str(content)

            tool_calls_info = [
                ToolCallInfo(name=tc.get("name", ""), args=tc.get("args", {}))
                for tc in result.get("tool_calls", [])
            ]

            # Add assistant message with constraint validation
            try:
                session.add_assistant_message(response_content)
            except ValueError as e:
                logger.error("Session limit exceeded: %s", e)
                raise HTTPException(
                    status_code=500,
                    detail="Session limit exceeded. Please start a new conversation.",
                ) from e

            return ChatResponse(
                session_id=session.session_id,
                message=ChatMessage(role="assistant", content=response_content),
                tool_calls=tool_calls_info,
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

    return router


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
) -> AsyncGenerator[str, None]:
    """Stream response for ask endpoint with JSON-encoded SSE events.

    Event format:
        data: {"event": "content", "content": "text chunk"}
        data: {"event": "tool_start", "name": "tool_name", "input": {...}}
        data: {"event": "tool_end", "name": "tool_name", "output": {...}}
        data: {"event": "done"}
        data: {"event": "error", "message": "error text"}
    """
    try:
        assistant = create_community_assistant(
            community_id,
            byok=byok,
            origin=origin,
            user_id=user_id,
            requested_model=requested_model,
            preload_docs=True,
            page_context=page_context,
        )
        graph = assistant.build_graph()

        state = {
            "messages": [HumanMessage(content=question)],
            "retrieved_docs": [],
            "tool_calls": [],
        }

        async for event in graph.astream_events(state, version="v2"):
            kind = event.get("event")

            if kind == "on_chat_model_stream":
                content = event.get("data", {}).get("chunk", {})
                if hasattr(content, "content") and content.content:
                    sse_event = {"event": "content", "content": content.content}
                    yield f"data: {json.dumps(sse_event)}\n\n"

            elif kind == "on_tool_start":
                tool_input = event.get("data", {}).get("input", {})
                sse_event = {
                    "event": "tool_start",
                    "name": event.get("name", ""),
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

    except Exception as e:
        logger.error(
            "Streaming error in ask endpoint for community %s: %s",
            community_id,
            e,
            exc_info=True,
        )
        sse_event = {"event": "error", "message": str(e)}
        yield f"data: {json.dumps(sse_event)}\n\n"


async def _stream_chat_response(
    community_id: str,
    session: ChatSession,
    byok: str | None,
    origin: str | None,
    user_id: str | None,
    requested_model: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream assistant response as JSON-encoded Server-Sent Events.

    Event format:
        data: {"event": "content", "content": "text chunk"}
        data: {"event": "tool_start", "name": "tool_name", "input": {...}}
        data: {"event": "tool_end", "name": "tool_name", "output": {...}}
        data: {"event": "done", "session_id": "..."}
        data: {"event": "error", "message": "error text"}
    """
    try:
        assistant = create_community_assistant(
            community_id,
            byok=byok,
            origin=origin,
            user_id=user_id,
            requested_model=requested_model,
            preload_docs=True,
        )
        graph = assistant.build_graph()

        state = {
            "messages": session.messages.copy(),
            "retrieved_docs": [],
            "tool_calls": [],
        }

        full_response = ""

        async for event in graph.astream_events(state, version="v2"):
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
                sse_event = {
                    "event": "tool_start",
                    "name": event.get("name", ""),
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

    except ValueError as e:
        # Session limit errors
        logger.error("Session limit error: %s", e)
        sse_event = {"event": "error", "message": str(e)}
        yield f"data: {json.dumps(sse_event)}\n\n"
    except Exception as e:
        logger.error(
            "Streaming error in chat endpoint for session %s (community: %s): %s",
            session.session_id,
            community_id,
            e,
            exc_info=True,
        )
        sse_event = {"event": "error", "message": str(e)}
        yield f"data: {json.dumps(sse_event)}\n\n"
