"""Base agent workflow patterns for OSA."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from src.agents.state import BaseAgentState


class BaseAgent(ABC):
    """Abstract base class for OSA agents.

    Provides common patterns for building LangGraph-based agents.
    """

    def __init__(
        self,
        model: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        """Initialize the agent.

        Args:
            model: The language model to use.
            tools: Optional list of tools available to the agent.
            system_prompt: Optional system prompt for the agent.
        """
        self.model = model
        self.tools = list(tools) if tools else []
        self.system_prompt = system_prompt

        # Bind tools to model if supported
        if self.tools:
            try:
                self.model_with_tools = model.bind_tools(self.tools)
            except NotImplementedError:
                # Model doesn't support tool binding (e.g., FakeListChatModel)
                self.model_with_tools = model
        else:
            self.model_with_tools = model

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent."""

    def build_graph(self) -> CompiledStateGraph:
        """Build and compile the LangGraph workflow.

        Returns a compiled graph ready for invocation.
        """
        graph = StateGraph(BaseAgentState)

        # Add nodes
        graph.add_node("agent", self._agent_node)
        if self.tools:
            graph.add_node("tools", ToolNode(self.tools))

        # Set entry point
        graph.set_entry_point("agent")

        # Add edges
        if self.tools:
            graph.add_conditional_edges(
                "agent",
                self._should_use_tools,
                {
                    "tools": "tools",
                    "end": END,
                },
            )
            graph.add_edge("tools", "agent")
        else:
            graph.add_edge("agent", END)

        return graph.compile()

    def _agent_node(self, state: BaseAgentState) -> dict[str, Any]:
        """Main agent node that processes messages and generates responses."""
        messages = self._prepare_messages(state)
        response = self.model_with_tools.invoke(messages)

        # Track tool calls if any
        tool_calls = state.get("tool_calls", [])
        if hasattr(response, "tool_calls") and response.tool_calls:
            for tc in response.tool_calls:
                tool_calls.append(
                    {
                        "name": tc["name"],
                        "args": tc["args"],
                    }
                )

        return {
            "messages": [response],
            "tool_calls": tool_calls,
        }

    def _prepare_messages(self, state: BaseAgentState) -> list[BaseMessage]:
        """Prepare messages for the model, including system prompt."""
        messages: list[BaseMessage] = []

        # Add system prompt
        system_prompt = self.system_prompt or self.get_system_prompt()
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))

        # Add conversation messages
        state_messages = state.get("messages", [])
        messages.extend(state_messages)

        return messages

    def _should_use_tools(self, state: BaseAgentState) -> str:
        """Determine if the agent should use tools or end."""
        messages = state.get("messages", [])
        if not messages:
            return "end"

        last_message = messages[-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return "end"

    async def ainvoke(
        self,
        messages: list[BaseMessage] | str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke the agent asynchronously.

        Args:
            messages: Input messages or a single string query.
            config: Optional config for callbacks, metadata, etc.

        Returns:
            The final state after execution.
        """
        # Convert string to message list
        if isinstance(messages, str):
            messages = [HumanMessage(content=messages)]

        # Build initial state
        initial_state: BaseAgentState = {
            "messages": messages,
            "retrieved_docs": [],
            "tool_calls": [],
        }

        # Compile and invoke
        graph = self.build_graph()
        return await graph.ainvoke(initial_state, config=config)

    def invoke(
        self,
        messages: list[BaseMessage] | str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke the agent synchronously.

        Args:
            messages: Input messages or a single string query.
            config: Optional config for callbacks, metadata, etc.

        Returns:
            The final state after execution.
        """
        # Convert string to message list
        if isinstance(messages, str):
            messages = [HumanMessage(content=messages)]

        # Build initial state
        initial_state: BaseAgentState = {
            "messages": messages,
            "retrieved_docs": [],
            "tool_calls": [],
        }

        # Compile and invoke
        graph = self.build_graph()
        return graph.invoke(initial_state, config=config)


class SimpleAgent(BaseAgent):
    """A simple agent without tools for basic Q&A."""

    def get_system_prompt(self) -> str:
        """Return a default system prompt."""
        return """You are a helpful assistant for open science projects.
You help researchers with questions about data formats, analysis tools, and best practices.
Be concise and accurate in your responses."""


class ToolAgent(BaseAgent):
    """An agent with tools for document retrieval and actions."""

    def get_system_prompt(self) -> str:
        """Return a system prompt that encourages tool use."""
        return """You are a helpful assistant for open science projects.
You have access to tools for retrieving documentation and performing actions.
Use your tools when you need to look up specific information or perform tasks.
Be concise and accurate in your responses."""
