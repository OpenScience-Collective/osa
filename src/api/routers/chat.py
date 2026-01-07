"""Chat API router with streaming responses."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from src.agents.hed import HEDAssistant
from src.api.config import get_settings
from src.core.services.litellm_llm import create_openrouter_llm

router = APIRouter(prefix="/chat", tags=["Chat"])


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
    assistant: str = Field(
        default="hed",
        description="Assistant to use: 'hed', 'bids', 'eeglab', or 'general'",
    )
    stream: bool = Field(default=True, description="Whether to stream the response")


class ToolCallInfo(BaseModel):
    """Information about a tool call made during response generation."""

    name: str = Field(..., description="Tool name")
    args: dict = Field(default_factory=dict, description="Tool arguments")


class ChatResponse(BaseModel):
    """Response body for non-streaming chat."""

    session_id: str = Field(..., description="Session ID for follow-up messages")
    message: ChatMessage = Field(..., description="Assistant response")
    assistant: str = Field(..., description="Assistant that handled the request")
    tool_calls: list[ToolCallInfo] = Field(
        default_factory=list, description="Tools called during response generation"
    )


class SessionInfo(BaseModel):
    """Information about a chat session."""

    session_id: str
    assistant: str
    message_count: int
    created_at: str
    last_active: str


# ---------------------------------------------------------------------------
# Session Management (In-Memory)
# ---------------------------------------------------------------------------


class ChatSession:
    """A chat session with message history."""

    def __init__(self, session_id: str, assistant: str) -> None:
        self.session_id = session_id
        self.assistant = assistant
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
            assistant=self.assistant,
            message_count=len(self.messages),
            created_at=self.created_at.isoformat(),
            last_active=self.last_active.isoformat(),
        )


# Simple in-memory session store
# For production, consider Redis or database-backed sessions
_sessions: dict[str, ChatSession] = {}


def get_or_create_session(session_id: str | None, assistant: str) -> ChatSession:
    """Get existing session or create a new one."""
    if session_id and session_id in _sessions:
        session = _sessions[session_id]
        # Update assistant if different (allows switching)
        session.assistant = assistant
        return session

    # Create new session
    new_id = session_id or str(uuid.uuid4())
    session = ChatSession(new_id, assistant)
    _sessions[new_id] = session
    return session


def get_session(session_id: str) -> ChatSession | None:
    """Get a session by ID."""
    return _sessions.get(session_id)


# ---------------------------------------------------------------------------
# Assistant Factory
# ---------------------------------------------------------------------------


def create_assistant(
    assistant_type: str,
    api_key: str | None = None,
    user_id: str | None = None,
    preload_docs: bool = True,
) -> HEDAssistant:
    """Create an assistant instance.

    Args:
        assistant_type: Type of assistant ('hed', 'bids', 'eeglab', 'general')
        api_key: Optional API key override (BYOK)
        user_id: User ID for cache optimization (sticky routing)
        preload_docs: Whether to preload documents

    Returns:
        Configured assistant instance

    Raises:
        ValueError: If assistant type is not supported
    """
    settings = get_settings()

    # Get model using LiteLLM with prompt caching support
    model = create_openrouter_llm(
        model=settings.default_model,
        api_key=api_key or settings.openrouter_api_key,
        temperature=settings.llm_temperature,
        provider=settings.default_model_provider,
        user_id=user_id,
        # enable_caching auto-detects based on model (Anthropic models)
    )

    # Currently only HED assistant is implemented
    if assistant_type == "hed":
        return HEDAssistant(model=model, preload_docs=preload_docs)
    else:
        # For now, use HED assistant for all types
        # TODO: Implement BIDS, EEGLAB, and General assistants
        return HEDAssistant(model=model, preload_docs=preload_docs)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ChatResponse,
    responses={
        200: {"description": "Successful response (non-streaming)"},
        400: {"description": "Invalid request"},
        500: {"description": "Internal server error"},
    },
)
async def chat(
    request: ChatRequest,
    x_openrouter_key: Annotated[str | None, Header(alias="X-OpenRouter-Key")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
) -> ChatResponse | StreamingResponse:
    """Chat with an OSA assistant.

    Send a message and receive a response from the selected assistant.
    Supports both streaming and non-streaming responses.

    **BYOK (Bring Your Own Key):**
    Pass your OpenRouter API key in the `X-OpenRouter-Key` header to use your own credits.

    **Cache Optimization:**
    Pass a stable user ID in the `X-User-ID` header for better cache hit rates.
    This enables sticky routing and up to 90% cost reduction on Anthropic models.

    **Streaming:**
    Set `stream: true` to receive a streaming response (Server-Sent Events).
    """
    # Use BYOK if provided
    api_key = x_openrouter_key

    # User ID for cache optimization (use session ID as fallback)
    user_id = x_user_id

    # TODO: Add server API key validation when required
    # if x_api_key: validate_api_key(x_api_key)

    # Get or create session
    session = get_or_create_session(request.session_id, request.assistant)

    # Use session ID as user_id if not provided
    if not user_id:
        user_id = session.session_id

    # Add user message to history
    session.add_user_message(request.message)

    if request.stream:
        return StreamingResponse(
            stream_response(session, api_key, user_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Session-ID": session.session_id,
            },
        )
    else:
        # Non-streaming response
        try:
            assistant = create_assistant(session.assistant, api_key, user_id)
            result = await assistant.ainvoke(session.messages)

            # Extract response content
            response_content = ""
            if result.get("messages"):
                last_msg = result["messages"][-1]
                if isinstance(last_msg, AIMessage):
                    response_content = last_msg.content

            # Extract tool calls from result
            tool_calls_info = []
            for tc in result.get("tool_calls", []):
                tool_calls_info.append(
                    ToolCallInfo(
                        name=tc.get("name", ""),
                        args=tc.get("args", {}),
                    )
                )

            # Add to session history
            session.add_assistant_message(response_content)

            return ChatResponse(
                session_id=session.session_id,
                message=ChatMessage(role="assistant", content=response_content),
                assistant=session.assistant,
                tool_calls=tool_calls_info,
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e


async def stream_response(
    session: ChatSession,
    api_key: str | None,
    user_id: str | None,
) -> AsyncGenerator[str, None]:
    """Stream assistant response as Server-Sent Events."""
    try:
        assistant = create_assistant(session.assistant, api_key, user_id, preload_docs=True)

        # Build the graph for streaming
        graph = assistant.build_graph()

        # Prepare initial state
        state = {
            "messages": session.messages.copy(),
            "retrieved_docs": [],
            "tool_calls": [],
        }

        full_response = ""

        # Stream the response
        async for event in graph.astream_events(state, version="v2"):
            kind = event.get("event")

            if kind == "on_chat_model_stream":
                content = event.get("data", {}).get("chunk", {})
                if hasattr(content, "content") and content.content:
                    chunk = content.content
                    full_response += chunk
                    # Send as SSE
                    yield f"data: {chunk}\n\n"

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                yield f"event: tool_start\ndata: {tool_name}\n\n"

            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                yield f"event: tool_end\ndata: {tool_name}\n\n"

        # Add complete response to session history
        if full_response:
            session.add_assistant_message(full_response)

        # Send done event
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
    """List all active chat sessions."""
    return [session.to_info() for session in _sessions.values()]
