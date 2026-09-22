"""Base agent workflow patterns for OSA."""

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, ValidationError

from src.agents.state import BaseAgentState, PendingClientCallPayload
from src.tools.client_tools import ClientTool

logger = logging.getLogger(__name__)

# Token budget for conversation history (excludes system prompt).
# 80K conversation + ~14K system prompt = ~94K total, well under the 200K context limit.
# A generous budget avoids trimming in most sessions, which preserves Anthropic prompt
# caching (identical prefix = cache hit) and tool call sequences (AIMessage + ToolMessage
# pairs must stay together).
DEFAULT_MAX_CONVERSATION_TOKENS = 80000

#: Tokens charged per image block when measuring a conversation.
#:
#: `count_tokens_approximately` defaults to 85, which is OpenAI's LOW-RESOLUTION image
#: cost. Anthropic bills roughly `width * height / 750` and caps near this figure, so the
#: default under-counts a real plot by more than an order of magnitude. Under-counting is
#: the direction that fails: it means believing a conversation fits when it does not.
#: Over-counting only spends headroom.
#:
#: Note this is a flat rate, not a measurement. The image block carries base64 and a
#: media type but no dimensions, so the exact cost is not recoverable from the message,
#: and a flat rate has the further virtue of being deterministic, which matters because
#: prompt caching is a byte-exact prefix match and a counter that varied per call would
#: make trimming vary per call.
#:
#: Sized for the models this project actually offers. Anthropic's documented cost is
#: per tier: the standard tier caps near 1568 tokens, which is what `claude-haiku-4-5`
#: and `claude-sonnet-5` use, and a later high-resolution tier caps near 4784. If a
#: community is ever configured onto a model in that higher tier, this constant would
#: under-count by roughly 3x, which is the exact direction this comment exists to warn
#: against. Revisit it when the offered model list changes, not before.
#:
#: Requires `tokens_per_image`, which landed in langchain-core 1.2.8; pyproject floors
#: the dependency at 1.6.0 as a margin above that.
ANTHROPIC_TOKENS_PER_IMAGE = 1600

#: Name of the graph node that parks a browser-executed tool call.
#:
#: Declared here rather than spelled as a literal at each use, because it is read from
#: OUTSIDE this module too: `_stream_chat_response` watches for this node's own
#: `on_chain_end` event as independent evidence that a call was parked, and compares it
#: against the graph state. Two copies of the string would let a rename here make that
#: check quietly stop firing, which would restore exactly the silent failure it exists
#: to catch.
CLIENT_TOOLS_NODE = "client_tools"


def _after_client_tools(state: BaseAgentState) -> str:
    """Where the run goes after the client_tools node.

    END only when the node parked a call for the browser. Otherwise every client
    call it saw was refused, and the model has to read those refusals to answer.
    """
    return "end" if state.get("pending_client_call") else "agent"


def _validated_client_args(
    tool: BaseTool | None, args: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Validate a client call's arguments against the tool's own schema.

    Returns the arguments to park, with defaults filled in so the browser never
    has to re-derive them, and None; or the original arguments and a short,
    deterministic description of what was wrong. The description is bounded
    because it becomes a ToolMessage that stays in stored history.
    """
    schema = getattr(tool, "args_schema", None)
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        return args, None
    try:
        return schema.model_validate(args).model_dump(), None
    except ValidationError as err:
        problems = [
            f"{'.'.join(str(part) for part in e['loc']) or 'arguments'}: {e['msg']}"
            for e in err.errors()[:5]
        ]
        return args, "; ".join(problems)[:500]


def count_conversation_tokens(messages: Sequence[BaseMessage]) -> int:
    """Approximate tokens for a conversation that may carry images.

    One call site's worth of behavior, named so the budget check, the trimmer and the
    post-trim log cannot drift apart by one of them forgetting the image rate.
    """
    return count_tokens_approximately(messages, tokens_per_image=ANTHROPIC_TOKENS_PER_IMAGE)


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
        *,
        client_tool_names: set[str] | None = None,
    ) -> None:
        """Initialize the agent.

        Args:
            model: The language model to use.
            tools: Optional list of tools available to the agent.
            system_prompt: Optional system prompt for the agent.
            max_conversation_tokens: Maximum tokens for conversation history.
                This caps the accumulated messages to prevent unbounded growth.
                Default is 80000 tokens. See DEFAULT_MAX_CONVERSATION_TOKENS.
            client_tool_names: Names, among `tools`, that are client tools
                (`src.tools.client_tools.ClientTool`) -- tools the graph
                parks a call to in `pending_client_call` rather than
                executes. Defaults to the subset of `tools` that are
                `ClientTool` instances, derived rather than accepted as-is
                so a caller cannot pass a set that has drifted out of step
                with what `tools` actually contains.
        """
        self.model = model
        self.tools = list(tools) if tools else []
        self.system_prompt = system_prompt
        self.max_conversation_tokens = max_conversation_tokens

        if client_tool_names is not None:
            self.client_tool_names = set(client_tool_names)
        else:
            self.client_tool_names = {t.name for t in self.tools if isinstance(t, ClientTool)}

        # Client tool instances by name, for their `requires_permission` flag
        # when the client_tools node builds `pending_client_call`. Always
        # derived from `self.tools` (not from `self.client_tool_names`,
        # which a caller may have supplied independently of `tools`).
        self._client_tools_by_name: dict[str, ClientTool] = {
            t.name: t for t in self.tools if isinstance(t, ClientTool)
        }

        # Tools a plain ToolNode may execute: everything that is not a
        # client tool call target. `build_graph` and `_client_tools_node`
        # both use this, so the two agree on exactly what "server tools"
        # means.
        self._server_tools = [t for t in self.tools if t.name not in self.client_tool_names]

        # Built lazily, on the first mixed batch that actually needs it (see
        # _client_tools_node), not here. Some callers pass a test double
        # (e.g. a MagicMock) as a tool that is never routed through a
        # ToolNode in practice; ToolNode's constructor calls create_tool()
        # on anything that is not a BaseTool instance, which rejects such a
        # double immediately. Building it here would fail agent
        # construction for those callers even when no client tool, and so
        # no client_tools node, is ever involved.
        self._server_tool_node: ToolNode | None = None

        # Bind tools to model if supported
        if self.tools:
            try:
                self.model_with_tools = model.bind_tools(self.tools)
            except NotImplementedError:
                logger.warning(
                    "Model %s does not support tool binding; running without tools",
                    type(model).__name__,
                )
                self.model_with_tools = model
        else:
            self.model_with_tools = model

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent."""

    def build_graph(self) -> CompiledStateGraph:
        """Build and compile the LangGraph workflow.

        When this agent has no client tools, the graph is unchanged from
        before client tools existed: an "agent" node, a "tools" node when
        `self.tools` is non-empty, and the same two-way routing between
        them. The "client_tools" node and its edge to END are added only
        when `self.client_tool_names` is non-empty.

        Returns a compiled graph ready for invocation.
        """
        graph = StateGraph(BaseAgentState)

        # Add nodes
        graph.add_node("agent", self._agent_node)
        if self.tools:
            # Server tools only. A ClientTool reaching a plain ToolNode would be
            # executed, and its _run raises, so the backstop works today only because
            # langgraph's default error handler re-raises anything that is not a
            # ToolInvocationError. Passing handle_tool_errors=True, or a future change
            # to that default, would turn a routing bug into a garbled ToolMessage and
            # a 200 instead of a crash. Scoping the node makes the guarantee structural
            # rather than borrowed: this node cannot see a client tool at all.
            graph.add_node("tools", ToolNode(self._server_tools))
        if self.client_tool_names:
            graph.add_node(CLIENT_TOOLS_NODE, self._client_tools_node)

        # Set entry point
        graph.set_entry_point("agent")

        # Add edges
        if self.tools:
            routes = {"tools": "tools", "end": END}
            if self.client_tool_names:
                routes[CLIENT_TOOLS_NODE] = CLIENT_TOOLS_NODE
            graph.add_conditional_edges(
                "agent",
                self._should_use_tools,
                routes,
            )
            graph.add_edge("tools", "agent")
            if self.client_tool_names:
                # A PARKED client call ends the run; the browser executes it
                # and a fresh run (resume) continues the turn. That is the
                # whole point of the two-run continuation.
                #
                # When nothing was parked, because every client call in the
                # message was refused (an unknown name, invalid arguments), the
                # run goes back to the model instead. An unconditional edge to
                # END ended those turns on an error ToolMessage with no reply at
                # all: the person saw nothing, and the model never saw the
                # error it was given.
                graph.add_conditional_edges(
                    CLIENT_TOOLS_NODE,
                    _after_client_tools,
                    {"end": END, "agent": "agent"},
                )
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

        # Include conversation history, trimming only when over budget.
        # Passing all messages through when under budget enables Anthropic prompt
        # caching (identical prefix = cache hit) and preserves tool call sequences
        # (AIMessage + ToolMessage pairs must stay together).
        state_messages = state.get("messages", [])
        if state_messages:
            pre_trim_tokens = count_conversation_tokens(state_messages)

            if pre_trim_tokens <= self.max_conversation_tokens:
                # Under budget: pass all messages through unchanged
                messages.extend(state_messages)
            else:
                # Over budget: trim keeping most recent messages.
                # Note: start_on is omitted to avoid splitting tool call sequences.
                trimmed = trim_messages(
                    state_messages,
                    max_tokens=self.max_conversation_tokens,
                    strategy="last",
                    token_counter=count_conversation_tokens,
                    include_system=False,
                )

                # Strip orphaned ToolMessages at the start of trimmed results.
                # After trimming, the first message could be a ToolMessage without
                # its preceding AIMessage, which LLM providers reject.
                while trimmed and isinstance(trimmed[0], ToolMessage):
                    trimmed = trimmed[1:]

                post_trim_tokens = count_conversation_tokens(trimmed)
                logger.debug(
                    "Trimmed conversation from %d to %d tokens",
                    pre_trim_tokens,
                    post_trim_tokens,
                )
                messages.extend(trimmed)

        return messages

    def _should_use_tools(self, state: BaseAgentState) -> str:
        """Determine if the agent should use tools, park a client call, or end.

        Returns "client_tools" when the last AIMessage's tool_calls contain
        ANY name in `self.client_tool_names` -- even if the same message
        also calls a server tool. The router returns one destination per
        message, so a mixed batch is resolved inside the "client_tools"
        node rather than by splitting the route here; see
        `_client_tools_node`. Otherwise, behavior is unchanged: "tools" when
        there are tool_calls, "end" otherwise.
        """
        messages = state.get("messages", [])
        if not messages:
            return "end"

        last_message = messages[-1]
        if not (isinstance(last_message, AIMessage) and last_message.tool_calls):
            return "end"

        if self.client_tool_names:
            call_names = {tc["name"] for tc in last_message.tool_calls}
            if call_names & self.client_tool_names:
                return CLIENT_TOOLS_NODE

        return "tools"

    async def _client_tools_node(
        self, state: BaseAgentState, config: RunnableConfig
    ) -> dict[str, Any]:
        """Handle an assistant message whose tool_calls include a client tool.

        Given the last AIMessage's tool_calls:

        1. Partitions them into server calls (name matches a server tool),
           client calls (name in `self.client_tool_names`), and unknown
           calls (name matches neither) -- the last get an error
           `ToolMessage` rather than an exception, since an unrecognized
           name is a data problem (a stale or mismatched tool_call), not a
           programming error.
        2. Runs the server calls through a `ToolNode` scoped to server
           tools only (`self._server_tool_node`), invoked with a synthetic
           `AIMessage` carrying just those calls, and appends the real
           `ToolMessage`s it returns.
        3. Records the FIRST client call as `pending_client_call`, using
           the provider-assigned `tool_call["id"]` as `call_id` -- never a
           newly minted one -- so a later resume can correlate against the
           model's own `tool_use` block exactly.
        4. Gives every FURTHER client call in the same message an error
           `ToolMessage`: only one browser execution runs per turn, and
           that call was not run. Every `tool_use` needs a matching
           `tool_result` or the provider rejects the next request outright,
           so this is required, not just informative.

        `build_graph` wires this node's only outgoing edge to END: parking
        a client call ends the run, and the browser's result reaches the
        model on a fresh run (resume) rather than by looping back to
        "agent" here.

        This node is async because it awaits `ToolNode.ainvoke` for the
        server calls; it accepts `config` (unlike `_agent_node`) solely to
        forward LangGraph's own runtime context into that nested
        `ToolNode` invocation, which requires it to resolve the executor it
        runs tools under.
        """
        messages = state.get("messages", [])
        last_message = messages[-1] if messages else None

        if not (isinstance(last_message, AIMessage) and last_message.tool_calls):
            # _should_use_tools only routes here when this holds; defensive
            # only, so a direct/test invocation on the wrong state is a
            # no-op rather than an IndexError or AttributeError.
            return {}

        server_tool_names = {t.name for t in self._server_tools}

        server_calls: list[dict[str, Any]] = []
        client_calls: list[dict[str, Any]] = []
        new_messages: list[BaseMessage] = []

        for tc in last_message.tool_calls:
            name = tc["name"]
            if name in server_tool_names:
                server_calls.append(tc)
            elif name in self.client_tool_names:
                client_calls.append(tc)
            else:
                # A data problem, not an exception: the model asked for something that
                # does not exist, and telling it so in a ToolMessage is the answer.
                # Logged because server-side this is invisible otherwise, and the two
                # things that cause it, a stale widget declaring a renamed tool and a
                # config typo, are exactly the drift the client-tool contract depends
                # on not happening. Without this it surfaces only as a confusing model
                # reply that a user might or might not report.
                logger.warning(
                    "Model called unknown tool %r; no server or client executor is "
                    "registered for it. Known server tools: %s. Known client tools: %s.",
                    name,
                    sorted(server_tool_names),
                    sorted(self.client_tool_names),
                )
                new_messages.append(
                    ToolMessage(
                        content=(
                            f"Unknown tool '{name}': no server or client "
                            "executor is registered for it."
                        ),
                        tool_call_id=tc["id"],
                        name=name,
                    )
                )

        if server_calls:
            if self._server_tool_node is None:
                self._server_tool_node = ToolNode(self._server_tools)
            synthetic = AIMessage(content="", tool_calls=server_calls)
            result = await self._server_tool_node.ainvoke({"messages": [synthetic]}, config)
            new_messages.extend(result.get("messages", []))

        pending_client_call: PendingClientCallPayload | None = None
        for tc in client_calls:
            tool = self._client_tools_by_name.get(tc["name"])
            # Validated HERE, before anything reaches a browser. The provider does
            # not enforce a tool's input schema, so a call can arrive missing
            # `description` (and render a blank permission gate) or carrying a
            # negative offset or an oversized call_id that the browser would
            # otherwise have to defend against. A refused call is answered, like
            # an unknown name, with a ToolMessage the model can act on.
            args, problem = _validated_client_args(tool, tc.get("args") or {})
            if problem is not None:
                new_messages.append(
                    ToolMessage(
                        content=(
                            f"Invalid arguments for {tc['name']}: {problem}. Nothing "
                            "was run; call it again with valid arguments."
                        ),
                        tool_call_id=tc["id"],
                        name=tc["name"],
                        status="error",
                    )
                )
            elif pending_client_call is None:
                pending_client_call = {
                    "call_id": tc["id"],
                    "tool": tc["name"],
                    "args": args,
                    "requires_permission": (tool.requires_permission if tool is not None else True),
                }
            else:
                new_messages.append(
                    ToolMessage(
                        content=(
                            "Only one browser-executed tool call runs per "
                            "turn; this call was not run. Wait for the "
                            "pending call to finish before requesting "
                            "another."
                        ),
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )

        update: dict[str, Any] = {"messages": new_messages}
        if pending_client_call is not None:
            update["pending_client_call"] = pending_client_call
        return update

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
