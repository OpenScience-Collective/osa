"""Tests for base agent workflow patterns.

These tests verify the agent graph structure and logic without making
actual LLM API calls.
"""

import pytest
from langchain_core.language_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from src.agents.base import BaseAgent, SimpleAgent, ToolAgent


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
