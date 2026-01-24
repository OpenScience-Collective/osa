"""Generic community assistant API router factory.

Creates parameterized routers for any registered community.
Each community gets endpoints like /{community_id}/ask, /{community_id}/chat, etc.
"""

import hashlib
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field, field_validator

from src.api.config import get_settings
from src.api.security import RequireAuth
from src.assistants import registry
from src.assistants.community import CommunityAssistant
from src.assistants.community import PageContext as AgentPageContext
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


class ChatSession:
    """A chat session with message history."""

    def __init__(self, session_id: str, community_id: str) -> None:
        self.session_id = session_id
        self.community_id = community_id
        self.messages: list[HumanMessage | AIMessage] = []
        self.created_at = datetime.now(UTC)
        self.last_active = self.created_at

    def add_user_message(self, content: str) -> None:
        """Add a user message to history."""
        self.messages.append(HumanMessage(content=content))
        self.last_active = datetime.now(UTC)

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to history."""
        self.messages.append(AIMessage(content=content))
        self.last_active = datetime.now(UTC)

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


def get_or_create_session(community_id: str, session_id: str | None) -> ChatSession:
    """Get existing session or create a new one."""
    store = _get_session_store(community_id)

    if session_id and session_id in store:
        return store[session_id]

    new_id = session_id or str(uuid.uuid4())
    session = ChatSession(new_id, community_id)
    store[new_id] = session
    return session


def get_session(community_id: str, session_id: str) -> ChatSession | None:
    """Get a session by ID."""
    store = _get_session_store(community_id)
    return store.get(session_id)


def delete_session(community_id: str, session_id: str) -> bool:
    """Delete a session. Returns True if deleted, False if not found."""
    store = _get_session_store(community_id)
    if session_id in store:
        del store[session_id]
        return True
    return False


def list_sessions(community_id: str) -> list[ChatSession]:
    """List all sessions for a community."""
    store = _get_session_store(community_id)
    return list(store.values())


# ---------------------------------------------------------------------------
# Assistant Factory
# ---------------------------------------------------------------------------


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
    api_key: str | None = None,
    user_id: str | None = None,
    preload_docs: bool = True,
    page_context: PageContext | None = None,
) -> CommunityAssistant:
    """Create a community assistant instance.

    Args:
        community_id: The community identifier (e.g., "hed", "bids")
        api_key: Optional API key override (BYOK)
        user_id: User ID for cache optimization (sticky routing)
        preload_docs: Whether to preload documents
        page_context: Optional context about the page where the widget is embedded

    Returns:
        Configured CommunityAssistant instance

    Raises:
        ValueError: If community_id is not registered
    """
    if registry.get(community_id) is None:
        raise ValueError(f"Unknown community: {community_id}")

    settings = get_settings()

    # Determine user_id for prompt caching optimization
    cache_user_id = _get_cache_user_id(community_id, api_key, user_id)

    model = create_openrouter_llm(
        model=settings.default_model,
        api_key=api_key or settings.openrouter_api_key,
        temperature=settings.llm_temperature,
        provider=settings.default_model_provider,
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
        request: AskRequest,
        _auth: RequireAuth,
        x_openrouter_key: Annotated[str | None, Header(alias="X-OpenRouter-Key")] = None,
        x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    ) -> AskResponse | StreamingResponse:
        """Ask a single question to the community assistant.

        This endpoint is for one-off questions without conversation history.
        For multi-turn conversations, use the /chat endpoint.

        **BYOK (Bring Your Own Key):**
        Pass your OpenRouter API key in the `X-OpenRouter-Key` header.

        **Cache Optimization:**
        Pass a stable user ID in the `X-User-ID` header for better cache hit rates.
        """
        if request.stream:
            return StreamingResponse(
                _stream_ask_response(
                    community_id,
                    request.question,
                    x_openrouter_key,
                    x_user_id,
                    request.page_context,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        try:
            assistant = create_community_assistant(
                community_id, x_openrouter_key, x_user_id, page_context=request.page_context
            )
            messages = [HumanMessage(content=request.question)]
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
            raise HTTPException(status_code=500, detail=str(e)) from e

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
        request: ChatRequest,
        _auth: RequireAuth,
        x_openrouter_key: Annotated[str | None, Header(alias="X-OpenRouter-Key")] = None,
        x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    ) -> ChatResponse | StreamingResponse:
        """Chat with the community assistant.

        Supports multi-turn conversations with session persistence.

        **BYOK (Bring Your Own Key):**
        Pass your OpenRouter API key in the `X-OpenRouter-Key` header.

        **Cache Optimization:**
        Pass a stable user ID in the `X-User-ID` header for better cache hit rates.
        """
        session = get_or_create_session(community_id, request.session_id)
        user_id = x_user_id or session.session_id
        session.add_user_message(request.message)

        if request.stream:
            return StreamingResponse(
                _stream_chat_response(community_id, session, x_openrouter_key, user_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Session-ID": session.session_id,
                },
            )

        try:
            assistant = create_community_assistant(community_id, x_openrouter_key, user_id)
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

            session.add_assistant_message(response_content)

            return ChatResponse(
                session_id=session.session_id,
                message=ChatMessage(role="assistant", content=response_content),
                tool_calls=tool_calls_info,
            )

        except Exception as e:
            logger.error(
                "Error in chat endpoint for session %s (community: %s): %s",
                session.session_id,
                community_id,
                e,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(e)) from e

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
    api_key: str | None,
    user_id: str | None,
    page_context: PageContext | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response for ask endpoint."""
    try:
        assistant = create_community_assistant(
            community_id, api_key, user_id, preload_docs=True, page_context=page_context
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
                    yield f"data: {content.content}\n\n"

            elif kind == "on_tool_start":
                yield f"event: tool_start\ndata: {event.get('name', '')}\n\n"

            elif kind == "on_tool_end":
                yield f"event: tool_end\ndata: {event.get('name', '')}\n\n"

        yield "event: done\ndata: complete\n\n"

    except Exception as e:
        logger.error(
            "Streaming error in ask endpoint for community %s: %s",
            community_id,
            e,
            exc_info=True,
        )
        yield f"event: error\ndata: {e!s}\n\n"


async def _stream_chat_response(
    community_id: str,
    session: ChatSession,
    api_key: str | None,
    user_id: str | None,
) -> AsyncGenerator[str, None]:
    """Stream assistant response as Server-Sent Events."""
    try:
        assistant = create_community_assistant(community_id, api_key, user_id, preload_docs=True)
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
                    yield f"data: {chunk}\n\n"

            elif kind == "on_tool_start":
                yield f"event: tool_start\ndata: {event.get('name', '')}\n\n"

            elif kind == "on_tool_end":
                yield f"event: tool_end\ndata: {event.get('name', '')}\n\n"

        if full_response:
            session.add_assistant_message(full_response)

        yield f"event: done\ndata: {session.session_id}\n\n"

    except Exception as e:
        logger.error(
            "Streaming error in chat endpoint for session %s (community: %s): %s",
            session.session_id,
            community_id,
            e,
            exc_info=True,
        )
        yield f"event: error\ndata: {e!s}\n\n"
