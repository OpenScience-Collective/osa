"""LangGraph state definitions for OSA agents."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


@dataclass
class AgentMetadata:
    """Metadata for agent execution tracking."""

    session_id: str
    assistant_type: str
    model: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    user_id: str | None = None
    total_tokens: int = 0
    estimated_cost: float = 0.0


class AgentState:
    """Base state for all OSA agents.

    Uses TypedDict-style annotations for LangGraph compatibility.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    """Conversation messages with automatic message merging."""

    metadata: AgentMetadata
    """Session and execution metadata."""

    retrieved_docs: list[dict[str, Any]]
    """Documents retrieved during the conversation."""

    tool_calls: list[dict[str, Any]]
    """History of tool calls made during execution."""

    next_action: Literal["continue", "end", "human_input"] | None
    """Control flow indicator for the workflow."""


class PendingClientCallPayload(TypedDict):
    """The graph-state form of a parked browser call.

    A `TypedDict` rather than `dict[str, Any]` because this crosses a boundary that is
    otherwise invisible to both the type checker and the runtime: the `client_tools`
    node writes it and `src.api.tool_results.PendingClientCall.from_state` reads it, and
    LangGraph validates nothing in between.

    Before this was typed, renaming a key here was silent. `from_state` reads `args`
    with `.get(...) or {}`, so a renamed key produced a call parked with EMPTY
    arguments: the browser would be asked to run nothing, with no exception, no log and
    no failing test. Dropping `call_id` instead raised a `KeyError` from inside the SSE
    generator, which truncates the stream with neither a `tool_request` nor an `error`
    event. Both now fail at the type checker instead.

    Still a plain dict at runtime, so LangGraph's serialization is unaffected.
    """

    call_id: str
    """The provider-assigned `tool_call["id"]`, never a server-generated one, so
    correlation with the model's own `tool_use` block is exact."""

    tool: str
    """The tool name the model called."""

    args: dict[str, Any]
    """The arguments the model passed."""

    requires_permission: bool
    """Whether the browser must show a permission gate before running this."""


class BaseAgentState(TypedDict, total=False):
    """TypedDict state for LangGraph StateGraph.

    All fields are optional (total=False) to allow partial updates.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    """Conversation messages with automatic message merging."""

    session_id: str
    """Unique session identifier."""

    assistant_type: str
    """Type of assistant handling this conversation."""

    model: str
    """LLM model being used."""

    user_id: str | None
    """Optional user identifier."""

    retrieved_docs: list[dict[str, Any]]
    """Documents retrieved during the conversation."""

    tool_calls: list[dict[str, Any]]
    """History of tool calls made during execution."""

    pending_client_call: "PendingClientCallPayload | None"
    """The one browser-executed tool call still waiting on a result, if any.

    Set by the `client_tools` node (`src.agents.base.BaseAgent`) for the
    FIRST client tool call in an assistant message's `tool_calls`; every
    further client call in the same message gets an error `ToolMessage`
    instead (only one browser execution runs per turn). A plain
    JSON-serializable dict: `{"call_id", "tool", "args",
    "requires_permission"}`. `call_id` is the provider-assigned
    `tool_call["id"]`, never a server-generated one, so correlation with the
    model's own `tool_use` block is exact."""


class RouterState(TypedDict, total=False):
    """State for the router agent that dispatches to specialists."""

    messages: Annotated[list[AnyMessage], add_messages]
    """Conversation messages."""

    query: str
    """The user's current query."""

    detected_topics: list[str]
    """Topics detected in the query (e.g., 'hed', 'bids', 'eeglab')."""

    selected_assistant: str | None
    """The specialist assistant to route to."""

    confidence: float
    """Confidence score for the routing decision."""


class SpecialistState(BaseAgentState):
    """Extended state for specialist assistants (HED, BIDS, EEGLAB)."""

    system_prompt: str
    """The specialist's system prompt."""

    preloaded_docs: list[dict[str, Any]]
    """Documents preloaded into context."""

    available_tools: list[str]
    """Names of tools available to this specialist."""
