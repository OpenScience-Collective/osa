"""Tests for the client-tool routing added to BaseAgent (src.agents.base).

Phase 1 (epic #429, issue #430) teaches the graph to call a tool it does not
execute: a message whose tool_calls include a client tool is routed to a
`client_tools` node that parks the first such call as `pending_client_call`
and ends the run, instead of the plain `tools` node. See
tests/test_tools/test_client_tools.py for the tool class and
`build_client_tools` itself.
"""

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from src.agents.base import ToolAgent
from src.tools.client_tools import ClientTool, ExecuteCodeArgs


@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def _client_tool(name: str = "execute_code", requires_permission: bool = True) -> ClientTool:
    return ClientTool(
        name=name,
        description="Run Python in the browser.",
        args_schema=ExecuteCodeArgs,
        requires_permission=requires_permission,
    )


def _fake_model(*responses: AIMessage) -> FakeMessagesListChatModel:
    return FakeMessagesListChatModel(responses=list(responses))


class TestClientToolNamesDerivation:
    """Tests for BaseAgent.__init__'s client_tool_names."""

    def test_derived_from_tools_when_not_passed(self) -> None:
        agent = ToolAgent(model=_fake_model(AIMessage(content="x")), tools=[add, _client_tool()])
        assert agent.client_tool_names == {"execute_code"}

    def test_no_client_tools_gives_empty_set(self) -> None:
        agent = ToolAgent(model=_fake_model(AIMessage(content="x")), tools=[add])
        assert agent.client_tool_names == set()

    def test_explicit_override_is_used_as_is(self) -> None:
        agent = ToolAgent(
            model=_fake_model(AIMessage(content="x")),
            tools=[add],
            client_tool_names={"add"},
        )
        assert agent.client_tool_names == {"add"}


class TestGraphStructure:
    """Tests that build_graph only adds the client_tools node/edge when needed."""

    def test_no_client_tools_graph_matches_pre_client_tools_shape(self) -> None:
        """Byte-identical routing to before client tools existed."""
        agent = ToolAgent(model=_fake_model(AIMessage(content="x")), tools=[add])
        g = agent.build_graph().get_graph()

        assert set(g.nodes.keys()) == {"__start__", "__end__", "agent", "tools"}
        edges = {(e.source, e.target) for e in g.edges}
        assert edges == {
            ("__start__", "agent"),
            ("agent", "tools"),
            ("agent", "__end__"),
            ("tools", "agent"),
        }

    def test_no_tools_at_all_graph_unchanged(self) -> None:
        """The tools-free path (SimpleAgent-shaped) stays a two-node graph."""
        agent = ToolAgent(model=_fake_model(AIMessage(content="x")), tools=[])
        g = agent.build_graph().get_graph()

        assert set(g.nodes.keys()) == {"__start__", "__end__", "agent"}
        edges = {(e.source, e.target) for e in g.edges}
        assert edges == {("__start__", "agent"), ("agent", "__end__")}

    def test_with_client_tools_adds_node_and_edge_to_end(self) -> None:
        agent = ToolAgent(model=_fake_model(AIMessage(content="x")), tools=[add, _client_tool()])
        g = agent.build_graph().get_graph()

        assert set(g.nodes.keys()) == {"__start__", "__end__", "agent", "tools", "client_tools"}
        edges = {(e.source, e.target) for e in g.edges}
        assert ("agent", "client_tools") in edges
        assert ("client_tools", "__end__") in edges
        # A client call ends the run; there is no loop back to "agent".
        assert ("client_tools", "agent") not in edges


class TestShouldUseTools:
    """Tests for BaseAgent._should_use_tools' client_tools destination."""

    def _agent(self, tools: list) -> ToolAgent:
        return ToolAgent(model=_fake_model(AIMessage(content="x")), tools=tools)

    def test_end_when_no_messages(self) -> None:
        agent = self._agent([add])
        assert agent._should_use_tools({"messages": []}) == "end"

    def test_end_when_last_message_has_no_tool_calls(self) -> None:
        agent = self._agent([add])
        state = {"messages": [AIMessage(content="hi")]}
        assert agent._should_use_tools(state) == "end"

    def test_tools_for_pure_server_batch(self) -> None:
        agent = self._agent([add])
        ai = AIMessage(
            content="", tool_calls=[{"id": "1", "name": "add", "args": {"a": 1, "b": 2}}]
        )
        assert agent._should_use_tools({"messages": [ai]}) == "tools"

    def test_client_tools_when_the_only_call_is_client(self) -> None:
        agent = self._agent([add, _client_tool()])
        ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "1", "name": "execute_code", "args": {"code": "x", "description": "d"}}
            ],
        )
        assert agent._should_use_tools({"messages": [ai]}) == "client_tools"

    def test_client_tools_for_a_mixed_batch(self) -> None:
        """One destination per message: any client call routes the whole batch."""
        agent = self._agent([add, _client_tool()])
        ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "1", "name": "add", "args": {"a": 1, "b": 2}},
                {"id": "2", "name": "execute_code", "args": {"code": "x", "description": "d"}},
            ],
        )
        assert agent._should_use_tools({"messages": [ai]}) == "client_tools"

    def test_tools_for_a_pure_server_batch_when_client_tools_are_also_bound(self) -> None:
        """An agent bound to both a server AND a client tool must still route
        a server-only batch to "tools", never to "client_tools".

        Every other case in this class either binds no client tool at all
        (`test_tools_for_pure_server_batch`, where `self.client_tool_names`
        is empty and the `call_names & self.client_tool_names` check is never
        reached) or already includes a client call (where `&` and a mutated
        `|` agree). Neither would catch `&` becoming `|` in
        `BaseAgent._should_use_tools`, which would route every ordinary tool
        call into the client-tools parking flow for any community that also
        configures a client tool such as `execute_code`. This is the one
        case where the two operators disagree: `call_names` is `{"add"}` and
        `self.client_tool_names` is `{"execute_code"}`, so `&` is empty
        (falsy, routes to "tools") while `|` is non-empty (truthy, would
        wrongly route to "client_tools").
        """
        agent = self._agent([add, _client_tool()])
        ai = AIMessage(
            content="", tool_calls=[{"id": "1", "name": "add", "args": {"a": 1, "b": 2}}]
        )
        assert agent._should_use_tools({"messages": [ai]}) == "tools"


class TestClientToolsNodeDirect:
    """Direct tests of _client_tools_node for client-only and unknown-name batches.

    These pass an empty config since no server tool executes in them; the
    mixed-batch (server + client) case needs a real LangGraph runtime
    context for the nested ToolNode invocation, so it is covered through a
    full `agent.ainvoke(...)` in TestFullTurnRouting instead.
    """

    async def test_two_client_calls_second_gets_message_first_is_pending(self) -> None:
        agent = ToolAgent(model=_fake_model(AIMessage(content="x")), tools=[_client_tool()])
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_first",
                    "name": "execute_code",
                    "args": {"code": "a", "description": "first"},
                },
                {
                    "id": "call_second",
                    "name": "execute_code",
                    "args": {"code": "b", "description": "second"},
                },
            ],
        )

        result = await agent._client_tools_node({"messages": [ai]}, {})

        assert result["pending_client_call"]["call_id"] == "call_first"
        assert result["pending_client_call"]["tool"] == "execute_code"
        assert result["pending_client_call"]["args"] == {"code": "a", "description": "first"}

        assert len(result["messages"]) == 1
        rejected = result["messages"][0]
        assert isinstance(rejected, ToolMessage)
        assert rejected.tool_call_id == "call_second"
        assert "only one" in rejected.content.lower()

    async def test_pending_call_carries_the_providers_own_id(self) -> None:
        """call_id must be the provider-assigned tool_call id, never a new one."""
        agent = ToolAgent(model=_fake_model(AIMessage(content="x")), tools=[_client_tool()])
        provider_id = "toolu_01AbCDefGhijKLmnop"
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": provider_id,
                    "name": "execute_code",
                    "args": {"code": "1 + 1", "description": "add"},
                }
            ],
        )

        result = await agent._client_tools_node({"messages": [ai]}, {})

        assert result["pending_client_call"]["call_id"] == provider_id

    async def test_requires_permission_reflects_tool_config(self) -> None:
        agent = ToolAgent(
            model=_fake_model(AIMessage(content="x")),
            tools=[_client_tool(requires_permission=False)],
        )
        ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_1", "name": "execute_code", "args": {"code": "x", "description": "d"}}
            ],
        )

        result = await agent._client_tools_node({"messages": [ai]}, {})

        assert result["pending_client_call"]["requires_permission"] is False

    async def test_unknown_tool_name_gets_error_tool_message(self) -> None:
        """An unrecognized name (server or client) gets an error, not an exception."""
        agent = ToolAgent(model=_fake_model(AIMessage(content="x")), tools=[_client_tool()])
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_known",
                    "name": "execute_code",
                    "args": {"code": "x", "description": "d"},
                },
                {"id": "call_unknown", "name": "mystery_tool", "args": {}},
            ],
        )

        # Confirm this batch is actually reachable through routing: any
        # client call sends the whole message here, unknown name and all.
        assert agent._should_use_tools({"messages": [ai]}) == "client_tools"

        result = await agent._client_tools_node({"messages": [ai]}, {})

        assert result["pending_client_call"]["call_id"] == "call_known"
        assert len(result["messages"]) == 1
        error_message = result["messages"][0]
        assert isinstance(error_message, ToolMessage)
        assert error_message.tool_call_id == "call_unknown"
        assert "unknown tool" in error_message.content.lower()

    async def test_no_client_calls_leaves_pending_call_unset(self) -> None:
        """A batch with no client call at all should not set pending_client_call."""
        agent = ToolAgent(model=_fake_model(AIMessage(content="x")), tools=[_client_tool()])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "call_unknown", "name": "mystery_tool", "args": {}}],
        )

        result = await agent._client_tools_node({"messages": [ai]}, {})

        assert "pending_client_call" not in result


class TestFullTurnRouting:
    """End-to-end graph invocations, proving the wiring and node work together."""

    async def test_no_client_tools_full_turn_routes_agent_tools_agent_end(self) -> None:
        """The pre-client-tools path: agent -> tools -> agent -> end."""
        tool_call_msg = AIMessage(
            content="", tool_calls=[{"id": "call_1", "name": "add", "args": {"a": 2, "b": 3}}]
        )
        final_msg = AIMessage(content="The answer is 5.")
        agent = ToolAgent(model=_fake_model(tool_call_msg, final_msg), tools=[add])

        result = await agent.ainvoke([HumanMessage(content="add 2 and 3")])

        kinds = [type(m).__name__ for m in result["messages"]]
        assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
        assert result["messages"][-1].content == "The answer is 5."
        assert "pending_client_call" not in result

    async def test_client_tool_only_call_ends_turn_with_pending_call(self) -> None:
        """A single client call ends the run with pending_client_call set."""
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "execute_code",
                    "args": {"code": "1 + 1", "description": "Add one and one."},
                }
            ],
        )
        agent = ToolAgent(model=_fake_model(ai), tools=[_client_tool()])

        result = await agent.ainvoke([HumanMessage(content="run something")])

        assert result["pending_client_call"] == {
            "call_id": "call_1",
            "tool": "execute_code",
            "args": {"code": "1 + 1", "description": "Add one and one."},
            "requires_permission": True,
        }
        # The run ended without a second model call: the last message is
        # still the assistant's tool-calling message, not a further reply.
        assert result["messages"][-1] is ai or result["messages"][-1] == ai

    async def test_mixed_batch_server_answered_and_exactly_one_pending(self) -> None:
        """A message mixing a server and a client call: both handled in one node."""
        ai_mixed = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_srv", "name": "add", "args": {"a": 1, "b": 2}},
                {
                    "id": "call_cli",
                    "name": "execute_code",
                    "args": {"code": "1 + 1", "description": "add"},
                },
            ],
        )
        agent = ToolAgent(model=_fake_model(ai_mixed), tools=[add, _client_tool()])

        result = await agent.ainvoke([HumanMessage(content="do both things")])

        tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_messages) == 1
        assert tool_messages[0].tool_call_id == "call_srv"
        assert tool_messages[0].content == "3"

        assert result["pending_client_call"] == {
            "call_id": "call_cli",
            "tool": "execute_code",
            "args": {"code": "1 + 1", "description": "add"},
            "requires_permission": True,
        }
