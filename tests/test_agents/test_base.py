"""Tests for base agent workflow patterns.

These tests verify the agent graph structure and logic without making
actual LLM API calls.
"""

import pytest
from langchain_core.language_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import tool

from src.agents.base import (
    DEFAULT_MAX_CONVERSATION_TOKENS,
    BaseAgent,
    SimpleAgent,
    ToolAgent,
)


@tool
def dummy_tool(query: str) -> str:
    """A dummy tool for testing."""
    return f"Result for: {query}"


class TestSimpleAgent:
    """Tests for SimpleAgent without tools."""

    def test_simple_agent_initialization(self) -> None:
        """SimpleAgent should initialize with a model."""
        model = FakeListChatModel(responses=["Hello!"])
        agent = SimpleAgent(model=model)
        assert agent.model is not None
        assert agent.tools == []
        assert agent.system_prompt is None

    def test_simple_agent_has_system_prompt(self) -> None:
        """SimpleAgent should have a default system prompt."""
        model = FakeListChatModel(responses=["Hello!"])
        agent = SimpleAgent(model=model)
        prompt = agent.get_system_prompt()
        assert "open science" in prompt.lower()

    def test_simple_agent_builds_graph(self) -> None:
        """SimpleAgent should build a valid graph."""
        model = FakeListChatModel(responses=["Hello!"])
        agent = SimpleAgent(model=model)
        graph = agent.build_graph()
        assert graph is not None

    def test_simple_agent_invoke(self) -> None:
        """SimpleAgent should process a simple query."""
        model = FakeListChatModel(responses=["I can help with open science!"])
        agent = SimpleAgent(model=model)

        result = agent.invoke("What is BIDS?")

        assert "messages" in result
        assert len(result["messages"]) >= 1
        # Last message should be from AI
        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)

    def test_simple_agent_invoke_with_message_list(self) -> None:
        """SimpleAgent should accept a list of messages."""
        model = FakeListChatModel(responses=["Here's the info!"])
        agent = SimpleAgent(model=model)

        result = agent.invoke([HumanMessage(content="Tell me about HED")])

        assert "messages" in result
        assert len(result["messages"]) >= 1

    def test_simple_agent_custom_system_prompt(self) -> None:
        """SimpleAgent should use custom system prompt if provided."""
        model = FakeListChatModel(responses=["Custom response"])
        custom_prompt = "You are a BIDS expert."
        agent = SimpleAgent(model=model, system_prompt=custom_prompt)

        assert agent.system_prompt == custom_prompt


class TestToolAgent:
    """Tests for ToolAgent with tools."""

    def test_tool_agent_initialization_with_tools(self) -> None:
        """ToolAgent should initialize with tools."""
        model = FakeListChatModel(responses=["Using tool..."])
        agent = ToolAgent(model=model, tools=[dummy_tool])

        assert len(agent.tools) == 1
        assert agent.tools[0].name == "dummy_tool"

    def test_tool_agent_has_system_prompt(self) -> None:
        """ToolAgent should have a tool-aware system prompt."""
        model = FakeListChatModel(responses=["Hello!"])
        agent = ToolAgent(model=model, tools=[dummy_tool])
        prompt = agent.get_system_prompt()
        assert "tools" in prompt.lower()

    def test_tool_agent_builds_graph_with_tool_node(self) -> None:
        """ToolAgent should build a graph with a tools node."""
        model = FakeListChatModel(responses=["Done!"])
        agent = ToolAgent(model=model, tools=[dummy_tool])
        graph = agent.build_graph()

        # Graph should compile without errors
        assert graph is not None

    def test_tool_agent_without_tools(self) -> None:
        """ToolAgent should work without tools (just uses default prompt)."""
        model = FakeListChatModel(responses=["No tools needed."])
        agent = ToolAgent(model=model, tools=[])
        graph = agent.build_graph()
        assert graph is not None


class TestBaseAgentAbstract:
    """Tests for BaseAgent abstract class."""

    def test_base_agent_is_abstract(self) -> None:
        """BaseAgent should not be instantiable directly."""
        model = FakeListChatModel(responses=["Hello!"])

        # This should work because we need to test the class structure
        # but calling get_system_prompt would fail without implementation
        with pytest.raises(TypeError):
            BaseAgent(model=model)  # type: ignore


class TestAgentStateTracking:
    """Tests for agent state tracking."""

    def test_agent_tracks_retrieved_docs(self) -> None:
        """Agent state should track retrieved documents."""
        model = FakeListChatModel(responses=["Found the docs."])
        agent = SimpleAgent(model=model)

        result = agent.invoke("Find HED docs")

        assert "retrieved_docs" in result
        assert isinstance(result["retrieved_docs"], list)

    def test_agent_tracks_tool_calls(self) -> None:
        """Agent state should track tool calls."""
        model = FakeListChatModel(responses=["Processing..."])
        agent = SimpleAgent(model=model)

        result = agent.invoke("Do something")

        assert "tool_calls" in result
        assert isinstance(result["tool_calls"], list)


class TestTokenTrimming:
    """Tests for conversation token trimming."""

    def test_default_max_conversation_tokens(self) -> None:
        """Default max conversation tokens should be 6000."""
        assert DEFAULT_MAX_CONVERSATION_TOKENS == 6000

    def test_agent_uses_default_max_tokens(self) -> None:
        """Agent should use default max conversation tokens."""
        model = FakeListChatModel(responses=["Hello!"])
        agent = SimpleAgent(model=model)
        assert agent.max_conversation_tokens == DEFAULT_MAX_CONVERSATION_TOKENS

    def test_agent_accepts_custom_max_tokens(self) -> None:
        """Agent should accept custom max conversation tokens."""
        model = FakeListChatModel(responses=["Hello!"])
        agent = SimpleAgent(model=model, max_conversation_tokens=4000)
        assert agent.max_conversation_tokens == 4000

    def test_prepare_messages_includes_system_prompt(self) -> None:
        """_prepare_messages should always include system prompt."""
        model = FakeListChatModel(responses=["Hello!"])
        agent = SimpleAgent(model=model)

        state = {"messages": [HumanMessage(content="Hi")]}
        messages = agent._prepare_messages(state)

        # First message should be system prompt
        assert len(messages) >= 2
        assert messages[0].content  # System prompt exists
        assert "open science" in messages[0].content.lower()

    def test_prepare_messages_trims_long_conversation(self) -> None:
        """_prepare_messages should trim conversation exceeding token budget."""
        model = FakeListChatModel(responses=["Response"])
        # Use very small token budget to force trimming
        agent = SimpleAgent(model=model, max_conversation_tokens=100)

        # Create a long conversation that exceeds 100 tokens
        long_messages = [
            HumanMessage(content="This is a very long message " * 20),
            AIMessage(content="This is a very long response " * 20),
            HumanMessage(content="Another long message " * 20),
            AIMessage(content="Another long response " * 20),
            HumanMessage(content="Final question"),
        ]

        state = {"messages": long_messages}
        messages = agent._prepare_messages(state)

        # Count tokens in conversation part (excluding system prompt)
        conversation_messages = messages[1:]  # Skip system prompt
        conversation_tokens = count_tokens_approximately(conversation_messages)

        # Should be trimmed to fit within budget (with some tolerance)
        assert conversation_tokens <= 150  # 100 + buffer

    def test_prepare_messages_keeps_recent_messages(self) -> None:
        """Trimming should keep most recent messages."""
        model = FakeListChatModel(responses=["Response"])
        agent = SimpleAgent(model=model, max_conversation_tokens=200)

        messages = [
            HumanMessage(content="Old message 1 " * 50),
            AIMessage(content="Old response 1 " * 50),
            HumanMessage(content="Recent question"),
        ]

        state = {"messages": messages}
        result = agent._prepare_messages(state)

        # The most recent message should be preserved
        conversation = result[1:]  # Skip system prompt
        assert any("Recent question" in str(m.content) for m in conversation)

    def test_prepare_messages_handles_empty_state(self) -> None:
        """_prepare_messages should handle empty message state."""
        model = FakeListChatModel(responses=["Hello!"])
        agent = SimpleAgent(model=model)

        state = {"messages": []}
        messages = agent._prepare_messages(state)

        # Should only have system prompt
        assert len(messages) == 1

    def test_prepare_messages_preserves_ai_responses(self) -> None:
        """Trimming should preserve final AI responses, not drop them."""
        model = FakeListChatModel(responses=["Response"])
        agent = SimpleAgent(model=model, max_conversation_tokens=500)

        # Conversation ending with AI response (common pattern)
        messages = [
            HumanMessage(content="What is HED?"),
            AIMessage(content="HED is Hierarchical Event Descriptors."),
        ]

        state = {"messages": messages}
        result = agent._prepare_messages(state)

        # The AI response should be preserved (not dropped by end_on filter)
        conversation = result[1:]  # Skip system prompt
        assert len(conversation) == 2
        assert isinstance(conversation[-1], AIMessage)
        assert "HED" in conversation[-1].content
