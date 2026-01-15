"""Base agent workflow patterns for OSA."""

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from src.agents.state import BaseAgentState

logger = logging.getLogger(__name__)

# Token budget for conversation history (excludes system prompt)
# System prompt with preloaded docs is ~14K tokens, conversation budget is additional
DEFAULT_MAX_CONVERSATION_TOKENS = 6000


class BaseAgent(ABC):
    """Abstract base class for OSA agents.

    Provides common patterns for building LangGraph-based agents.
    """

    def __init__(
        self,
        model: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
        system_prompt: str | None = None,
        max_conversation_tokens: int = DEFAULT_MAX_CONVERSATION_TOKENS,
    ) -> None:
        """Initialize the agent.

        Args:
            model: The language model to use.
            tools: Optional list of tools available to the agent.
            system_prompt: Optional system prompt for the agent.
            max_conversation_tokens: Maximum tokens for conversation history.
                This caps the accumulated messages to prevent unbounded growth.
                Default is 6000 tokens, which combined with ~14K system prompt
                keeps total context under 20K tokens per iteration.
        """
        self.model = model
        self.tools = list(tools) if tools else []
        self.system_prompt = system_prompt
        self.max_conversation_tokens = max_conversation_tokens

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
        """Prepare messages for the model, including system prompt.

        Uses token-aware trimming to prevent unbounded context growth.
        The system prompt is always included in full, while conversation
        history is trimmed to fit within max_conversation_tokens budget.
        """
        messages: list[BaseMessage] = []

        # Add system prompt (always included in full)
        system_prompt = self.system_prompt or self.get_system_prompt()
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))

        # Trim conversation history to fit token budget
        state_messages = state.get("messages", [])
        if state_messages:
            # Count tokens before trimming for logging
            pre_trim_tokens = count_tokens_approximately(state_messages)

            trimmed = trim_messages(
                state_messages,
                max_tokens=self.max_conversation_tokens,
                strategy="last",  # Keep most recent messages
                token_counter=count_tokens_approximately,
                start_on="human",  # Ensure we start on a user message
                end_on=("human", "tool"),  # Valid ending message types
                include_system=False,  # System prompt handled separately
            )

            post_trim_tokens = count_tokens_approximately(trimmed)
            if pre_trim_tokens > post_trim_tokens:
                logger.debug(
                    "Trimmed conversation from %d to %d tokens",
                    pre_trim_tokens,
                    post_trim_tokens,
                )

            messages.extend(trimmed)

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
