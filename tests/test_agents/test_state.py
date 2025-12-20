"""Tests for LangGraph state definitions."""

from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.state import (
    AgentMetadata,
    BaseAgentState,
    RouterState,
    SpecialistState,
)


class TestAgentMetadata:
    """Tests for AgentMetadata dataclass."""

    def test_metadata_creation_with_defaults(self) -> None:
        """AgentMetadata should initialize with default timestamp."""
        metadata = AgentMetadata(
            session_id="test-session",
            assistant_type="general",
            model="gpt-4o-mini",
        )
        assert metadata.session_id == "test-session"
        assert metadata.assistant_type == "general"
        assert metadata.model == "gpt-4o-mini"
        assert metadata.user_id is None
        assert metadata.total_tokens == 0
        assert metadata.estimated_cost == 0.0
        assert isinstance(metadata.started_at, datetime)

    def test_metadata_creation_with_all_fields(self) -> None:
        """AgentMetadata should accept all optional fields."""
        metadata = AgentMetadata(
            session_id="test-session",
            assistant_type="hed",
            model="claude-3-5-sonnet",
            user_id="user-123",
            total_tokens=1500,
            estimated_cost=0.045,
        )
        assert metadata.user_id == "user-123"
        assert metadata.total_tokens == 1500
        assert metadata.estimated_cost == 0.045


class TestBaseAgentState:
    """Tests for BaseAgentState TypedDict."""

    def test_state_with_messages(self) -> None:
        """BaseAgentState should hold conversation messages."""
        state: BaseAgentState = {
            "messages": [
                HumanMessage(content="Hello"),
                AIMessage(content="Hi there!"),
            ],
            "session_id": "session-1",
            "assistant_type": "general",
            "model": "gpt-4o",
            "retrieved_docs": [],
            "tool_calls": [],
        }
        assert len(state["messages"]) == 2
        assert state["session_id"] == "session-1"

    def test_state_with_retrieved_docs(self) -> None:
        """BaseAgentState should store retrieved documents."""
        state: BaseAgentState = {
            "messages": [],
            "retrieved_docs": [
                {"title": "HED Specification", "url": "https://hedtags.org"},
                {"title": "BIDS Overview", "url": "https://bids.neuroimaging.io"},
            ],
            "tool_calls": [],
        }
        assert len(state["retrieved_docs"]) == 2
        assert state["retrieved_docs"][0]["title"] == "HED Specification"

    def test_state_with_tool_calls(self) -> None:
        """BaseAgentState should track tool call history."""
        state: BaseAgentState = {
            "messages": [],
            "retrieved_docs": [],
            "tool_calls": [
                {"name": "retrieve_hed_docs", "args": {"query": "schema"}},
                {"name": "search_github", "args": {"repo": "hed-standard"}},
            ],
        }
        assert len(state["tool_calls"]) == 2
        assert state["tool_calls"][0]["name"] == "retrieve_hed_docs"


class TestRouterState:
    """Tests for RouterState TypedDict."""

    def test_router_state_fields(self) -> None:
        """RouterState should have routing-specific fields."""
        state: RouterState = {
            "messages": [HumanMessage(content="How do I use HED?")],
            "query": "How do I use HED?",
            "detected_topics": ["hed", "annotation"],
            "selected_assistant": "hed-assistant",
            "confidence": 0.95,
        }
        assert state["query"] == "How do I use HED?"
        assert "hed" in state["detected_topics"]
        assert state["selected_assistant"] == "hed-assistant"
        assert state["confidence"] == 0.95

    def test_router_state_partial(self) -> None:
        """RouterState should allow partial initialization."""
        state: RouterState = {
            "messages": [],
            "query": "What is BIDS?",
        }
        assert state["query"] == "What is BIDS?"
        assert "selected_assistant" not in state


class TestSpecialistState:
    """Tests for SpecialistState TypedDict."""

    def test_specialist_state_extends_base(self) -> None:
        """SpecialistState should include base fields plus specialist fields."""
        state: SpecialistState = {
            "messages": [HumanMessage(content="Explain HED schema")],
            "session_id": "spec-session",
            "assistant_type": "hed",
            "model": "gpt-4o",
            "retrieved_docs": [],
            "tool_calls": [],
            "system_prompt": "You are an expert in HED annotations...",
            "preloaded_docs": [{"title": "HED Overview", "content": "..."}],
            "available_tools": ["retrieve_hed_docs", "validate_hed_string"],
        }
        assert state["system_prompt"].startswith("You are an expert")
        assert len(state["preloaded_docs"]) == 1
        assert "retrieve_hed_docs" in state["available_tools"]
