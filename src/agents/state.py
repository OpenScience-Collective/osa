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
