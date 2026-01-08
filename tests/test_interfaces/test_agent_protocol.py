"""Tests for AgentProtocol implementation.

These tests verify that all agents implement the protocol correctly.
Tests are parameterized and run against all agents defined in conftest.py.
"""

import pytest
from langchain_core.messages import AIMessage

from src.interfaces import AgentProtocol, ToolProtocol


class TestAgentProtocolCompliance:
    """Tests that verify protocol compliance."""

    def test_agent_implements_protocol(self, agent) -> None:
        """Agent should implement AgentProtocol."""
        assert isinstance(agent, AgentProtocol)

    def test_agent_has_model(self, agent) -> None:
        """Agent should have a model attribute."""
        assert agent.model is not None

    def test_agent_has_tools_list(self, agent) -> None:
        """Agent should have a tools list."""
        assert hasattr(agent, "tools")
        assert isinstance(agent.tools, list)


class TestAgentTools:
    """Tests for agent tool integration."""

    def test_agent_has_tools(self, agent) -> None:
        """Agent should have at least one tool."""
        assert len(agent.tools) > 0

    def test_tools_implement_protocol(self, agent) -> None:
        """All tools should implement ToolProtocol."""
        for tool in agent.tools:
            assert isinstance(tool, ToolProtocol)

    def test_tools_have_names(self, agent) -> None:
        """All tools should have names."""
        for tool in agent.tools:
            assert tool.name
            assert isinstance(tool.name, str)

    def test_tools_have_descriptions(self, agent) -> None:
        """All tools should have descriptions."""
        for tool in agent.tools:
            assert tool.description
            assert isinstance(tool.description, str)

    def test_tool_names_are_unique(self, agent) -> None:
        """Tool names should be unique within an agent."""
        names = [tool.name for tool in agent.tools]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"


class TestAgentSystemPrompt:
    """Tests for agent system prompt."""

    def test_get_system_prompt_returns_string(self, agent) -> None:
        """get_system_prompt should return a string."""
        prompt = agent.get_system_prompt()
        assert isinstance(prompt, str)

    def test_system_prompt_is_non_empty(self, agent) -> None:
        """System prompt should not be empty."""
        prompt = agent.get_system_prompt()
        assert len(prompt) > 0

    def test_system_prompt_is_deterministic(self, agent) -> None:
        """System prompt should be consistent across calls."""
        prompt1 = agent.get_system_prompt()
        prompt2 = agent.get_system_prompt()
        assert prompt1 == prompt2


class TestAgentGraph:
    """Tests for agent graph building."""

    def test_build_graph_returns_graph(self, agent) -> None:
        """build_graph should return a compiled graph."""
        graph = agent.build_graph()
        assert graph is not None

    def test_graph_is_invokable(self, agent) -> None:
        """Built graph should be invokable."""
        graph = agent.build_graph()
        # Graph should have invoke method
        assert hasattr(graph, "invoke")
        assert callable(graph.invoke)


class TestAgentInvocation:
    """Tests for agent invocation."""

    def test_invoke_with_string(self, agent) -> None:
        """Agent should accept string input."""
        result = agent.invoke("Hello")
        assert isinstance(result, dict)

    def test_invoke_returns_messages(self, agent) -> None:
        """Invoke result should contain messages."""
        result = agent.invoke("Hello")
        assert "messages" in result
        assert isinstance(result["messages"], list)

    def test_invoke_last_message_is_ai(self, agent) -> None:
        """Last message in result should be from AI."""
        result = agent.invoke("Hello")
        messages = result["messages"]
        assert len(messages) > 0
        last_msg = messages[-1]
        assert isinstance(last_msg, AIMessage)

    def test_invoke_tracks_tool_calls(self, agent) -> None:
        """Invoke result should track tool calls."""
        result = agent.invoke("Hello")
        assert "tool_calls" in result

    def test_invoke_tracks_retrieved_docs(self, agent) -> None:
        """Invoke result should track retrieved docs."""
        result = agent.invoke("Hello")
        assert "retrieved_docs" in result


@pytest.mark.asyncio
class TestAgentAsyncInvocation:
    """Tests for async agent invocation."""

    async def test_ainvoke_with_string(self, agent) -> None:
        """Agent should accept string input asynchronously."""
        result = await agent.ainvoke("Hello")
        assert isinstance(result, dict)

    async def test_ainvoke_returns_messages(self, agent) -> None:
        """Async invoke result should contain messages."""
        result = await agent.ainvoke("Hello")
        assert "messages" in result
        assert isinstance(result["messages"], list)

    async def test_ainvoke_last_message_is_ai(self, agent) -> None:
        """Last message in async result should be from AI."""
        result = await agent.ainvoke("Hello")
        messages = result["messages"]
        assert len(messages) > 0
        last_msg = messages[-1]
        assert isinstance(last_msg, AIMessage)
