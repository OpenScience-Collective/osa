"""HED Assistant API router."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field, field_validator

from src.agents.hed import HEDAssistant
from src.agents.hed import PageContext as AgentPageContext
from src.api.config import get_settings
from src.api.security import RequireAuth
from src.core.services.litellm_llm import create_openrouter_llm

router = APIRouter(prefix="/hed", tags=["HED Assistant"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single chat message."""

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
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
        max_length=2048,  # Reasonable URL length limit
    )
    title: str | None = Field(
        default=None,
        description="Title of the page where the assistant is embedded",
        max_length=500,  # Prevent DoS with huge titles
    )

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, url: str | None) -> str | None:
        """Ensure URL has valid scheme if provided."""
        if url is None:
            return url
        # Validate URL has proper scheme - reject invalid schemes explicitly
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

    session_id: str
    message_count: int
    created_at: str
    last_active: str


# ---------------------------------------------------------------------------
# Session Management (In-Memory)
# ---------------------------------------------------------------------------


class ChatSession:
    """A chat session with message history."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
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
            message_count=len(self.messages),
            created_at=self.created_at.isoformat(),
            last_active=self.last_active.isoformat(),
        )


# Simple in-memory session store
_sessions: dict[str, ChatSession] = {}


def get_or_create_session(session_id: str | None) -> ChatSession:
    """Get existing session or create a new one."""
    if session_id and session_id in _sessions:
        return _sessions[session_id]

    new_id = session_id or str(uuid.uuid4())
    session = ChatSession(new_id)
    _sessions[new_id] = session
    return session


def get_session(session_id: str) -> ChatSession | None:
    """Get a session by ID."""
    return _sessions.get(session_id)


# ---------------------------------------------------------------------------
# Assistant Factory
# ---------------------------------------------------------------------------


def create_hed_assistant(
    api_key: str | None = None,
    user_id: str | None = None,
    preload_docs: bool = True,
    page_context: PageContext | None = None,
) -> HEDAssistant:
    """Create a HED assistant instance.

    Args:
        api_key: Optional API key override (BYOK)
        user_id: User ID for cache optimization (sticky routing)
        preload_docs: Whether to preload documents
        page_context: Optional context about the page where the widget is embedded

    Returns:
        Configured HEDAssistant instance
    """
    settings = get_settings()

    model = create_openrouter_llm(
        model=settings.default_model,
        api_key=api_key or settings.openrouter_api_key,
        temperature=settings.llm_temperature,
        provider=settings.default_model_provider,
        user_id=user_id,
    )

    # Convert Pydantic PageContext to agent's dataclass PageContext
    agent_page_context = None
    if page_context:
        agent_page_context = AgentPageContext(
            url=page_context.url,
            title=page_context.title,
        )

    return HEDAssistant(model=model, preload_docs=preload_docs, page_context=agent_page_context)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


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
    """Ask a single question to the HED assistant.

    This endpoint is for one-off questions without conversation history.
    For multi-turn conversations, use the /chat endpoint.

    **BYOK (Bring Your Own Key):**
    Pass your OpenRouter API key in the `X-OpenRouter-Key` header.

    **Cache Optimization:**
    Pass a stable user ID in the `X-User-ID` header for better cache hit rates.
    """
    if request.stream:
        return StreamingResponse(
            stream_ask_response(
                request.question, x_openrouter_key, x_user_id, request.page_context
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    try:
        assistant = create_hed_assistant(
            x_openrouter_key, x_user_id, page_context=request.page_context
        )
        messages = [HumanMessage(content=request.question)]
        result = await assistant.ainvoke(messages)

        response_content = ""
        if result.get("messages"):
            last_msg = result["messages"][-1]
            if isinstance(last_msg, AIMessage):
                response_content = last_msg.content

        tool_calls_info = [
            ToolCallInfo(name=tc.get("name", ""), args=tc.get("args", {}))
            for tc in result.get("tool_calls", [])
        ]

        return AskResponse(answer=response_content, tool_calls=tool_calls_info)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


async def stream_ask_response(
    question: str,
    api_key: str | None,
    user_id: str | None,
    page_context: PageContext | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response for ask endpoint."""
    try:
        assistant = create_hed_assistant(
            api_key, user_id, preload_docs=True, page_context=page_context
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
        yield f"event: error\ndata: {e!s}\n\n"


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
    """Chat with the HED assistant.

    Supports multi-turn conversations with session persistence.

    **BYOK (Bring Your Own Key):**
    Pass your OpenRouter API key in the `X-OpenRouter-Key` header.

    **Cache Optimization:**
    Pass a stable user ID in the `X-User-ID` header for better cache hit rates.
    """
    session = get_or_create_session(request.session_id)
    user_id = x_user_id or session.session_id
    session.add_user_message(request.message)

    if request.stream:
        return StreamingResponse(
            stream_chat_response(session, x_openrouter_key, user_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Session-ID": session.session_id,
            },
        )

    try:
        assistant = create_hed_assistant(x_openrouter_key, user_id)
        result = await assistant.ainvoke(session.messages)

        response_content = ""
        if result.get("messages"):
            last_msg = result["messages"][-1]
            if isinstance(last_msg, AIMessage):
                response_content = last_msg.content

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
        raise HTTPException(status_code=500, detail=str(e)) from e


async def stream_chat_response(
    session: ChatSession,
    api_key: str | None,
    user_id: str | None,
) -> AsyncGenerator[str, None]:
    """Stream assistant response as Server-Sent Events."""
    try:
        assistant = create_hed_assistant(api_key, user_id, preload_docs=True)
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
        yield f"event: error\ndata: {e!s}\n\n"


@router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session_info(session_id: str) -> SessionInfo:
    """Get information about a chat session."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_info()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, str]:
    """Delete a chat session."""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    del _sessions[session_id]
    return {"status": "deleted", "session_id": session_id}


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions() -> list[SessionInfo]:
    """List all active HED chat sessions."""
    return [session.to_info() for session in _sessions.values()]
