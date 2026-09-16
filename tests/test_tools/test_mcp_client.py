"""Tests for the MCP client runtime.

NO MOCKS. Every test here runs a REAL MCP server -- `mcp`'s own `MCPServer` over
Streamable HTTP, on a real socket, driven by the real `mcp` client through the
real LangChain tool wrappers. The only thing stood up specially is the server's
tool bodies, which is the fixture's subject, not a substitute for one.

That matters most for the two things this module actually has to get right:

  1. Discovery works from a synchronous caller EVEN WHEN an event loop is already
     running on that thread, because `CommunityAssistant.__init__` is synchronous
     and FastAPI calls it from inside a loop. A mock cannot demonstrate this; only
     really running it can.
  2. An unreachable server degrades to an empty tool list rather than raising,
     because an assistant that cannot start when someone else's host is down is
     worse than one missing a few tools.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from src.core.config.community import McpServer
from src.tools.mcp_client import discover_mcp_tools

# --------------------------------------------------------------------------
# A real MCP server on a real port.
# --------------------------------------------------------------------------


def _build_server() -> MCPServer:
    srv = MCPServer(name="fixture-server", version="0.0.1")

    @srv.tool(structured_output=False)
    def echo_dataset(dataset_id: str, limit: int = 10) -> CallToolResult:
        """Echo back what it was given, as structured content."""
        return CallToolResult(
            content=[TextContent(type="text", text=f"echo {dataset_id}")],
            structuredContent={"dataset_id": dataset_id, "limit": limit},
        )

    @srv.tool(structured_output=False)
    def text_only() -> CallToolResult:
        """Answer with text blocks and no structured content."""
        return CallToolResult(
            content=[
                TextContent(type="text", text="first block"),
                TextContent(type="text", text="second block"),
            ]
        )

    @srv.tool(structured_output=False)
    def always_refuses() -> CallToolResult:
        """Refuse the way the real server refuses: isError plus an explanation."""
        return CallToolResult(
            content=[TextContent(type="text", text="declines: over the 60 s cap")],
            isError=True,
        )

    return srv


@pytest.fixture(scope="module")
def mcp_url() -> Iterator[str]:
    """A real MCP server on a background thread, torn down after the module."""
    import uvicorn

    app = _build_server().streamable_http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the socket to be assigned rather than sleeping a guessed amount.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.started and server.servers:
            break
        time.sleep(0.05)
    else:  # pragma: no cover - only on a broken environment
        raise RuntimeError("fixture MCP server did not start")

    port = server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"

    # Confirm it really answers before any test runs, so a failure here is
    # reported as a fixture problem rather than as every test failing oddly.
    for _ in range(60):
        try:
            httpx.post(url, json={}, timeout=2.0)
            break
        except httpx.HTTPError:
            time.sleep(0.05)

    yield url

    server.should_exit = True
    thread.join(timeout=10)


def _server(url: str, name: str = "fixture") -> McpServer:
    return McpServer(name=name, url=url)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


class TestDiscovery:
    def test_returns_a_langchain_tool_per_mcp_tool(self, mcp_url: str) -> None:
        tools = discover_mcp_tools(_server(mcp_url))
        assert {t.name for t in tools} == {
            "fixture_echo_dataset",
            "fixture_text_only",
            "fixture_always_refuses",
        }

    def test_names_are_prefixed_with_the_server_name(self, mcp_url: str) -> None:
        """So two servers offering the same tool cannot collide in one assistant."""
        tools = discover_mcp_tools(_server(mcp_url, name="other"))
        assert all(t.name.startswith("other_") for t in tools)

    def test_args_schema_is_the_servers_own_input_schema(self, mcp_url: str) -> None:
        """Passed through verbatim rather than re-derived: the server is the
        authority on what it accepts, and every translation step is a chance to
        disagree with it."""
        tool = next(
            t for t in discover_mcp_tools(_server(mcp_url)) if t.name.endswith("echo_dataset")
        )
        assert isinstance(tool.args_schema, dict)
        assert set(tool.args_schema["properties"]) == {"dataset_id", "limit"}
        assert tool.args_schema["required"] == ["dataset_id"]

    def test_description_comes_from_the_server(self, mcp_url: str) -> None:
        tool = next(
            t for t in discover_mcp_tools(_server(mcp_url)) if t.name.endswith("echo_dataset")
        )
        assert "Echo back" in tool.description

    def test_works_from_inside_a_running_event_loop(self, mcp_url: str) -> None:
        """The case a naive `asyncio.run` breaks on, and the reason discovery uses
        a worker thread: `CommunityAssistant.__init__` is synchronous and the
        FastAPI app calls it from inside a live loop."""

        async def caller() -> list[Any]:
            # A synchronous call, made from inside a running loop.
            return discover_mcp_tools(_server(mcp_url))

        tools = asyncio.run(caller())
        assert len(tools) == 3

    def test_works_from_a_plain_synchronous_caller(self, mcp_url: str) -> None:
        """The other half: no loop running at all."""
        assert len(discover_mcp_tools(_server(mcp_url))) == 3


# --------------------------------------------------------------------------
# Invocation
# --------------------------------------------------------------------------


class TestInvocation:
    async def test_round_trips_arguments_and_structured_result(self, mcp_url: str) -> None:
        tool = next(
            t for t in discover_mcp_tools(_server(mcp_url)) if t.name.endswith("echo_dataset")
        )
        result = await tool.ainvoke({"dataset_id": "nm000329", "limit": 3})
        assert result == {"dataset_id": "nm000329", "limit": 3}

    async def test_a_default_is_the_servers_default(self, mcp_url: str) -> None:
        tool = next(
            t for t in discover_mcp_tools(_server(mcp_url)) if t.name.endswith("echo_dataset")
        )
        result = await tool.ainvoke({"dataset_id": "nm000329"})
        assert result == {"dataset_id": "nm000329", "limit": 10}

    async def test_text_only_result_joins_every_block(self, mcp_url: str) -> None:
        """Joined, not "the first" or "the last": a multi-block answer otherwise
        reaches the model as an arbitrary fragment of itself."""
        tool = next(t for t in discover_mcp_tools(_server(mcp_url)) if t.name.endswith("text_only"))
        result = await tool.ainvoke({})
        assert result == "first block\nsecond block"

    async def test_a_refusal_comes_back_as_text_not_an_exception(self, mcp_url: str) -> None:
        """The server's refusals name a cap or a workaround, so handing that
        sentence to the model lets it correct itself; raising would end the turn."""
        tool = next(
            t for t in discover_mcp_tools(_server(mcp_url)) if t.name.endswith("always_refuses")
        )
        result = await tool.ainvoke({})
        assert isinstance(result, str)
        assert "over the 60 s cap" in result

    async def test_an_input_the_schema_rejects_does_not_raise_out(self, mcp_url: str) -> None:
        """A schema rejection is an `isError` result on this SDK, so it takes the
        same path as any other refusal and reaches the model as readable text."""
        tool = next(
            t for t in discover_mcp_tools(_server(mcp_url)) if t.name.endswith("echo_dataset")
        )
        result = await tool.ainvoke({"dataset_id": "nm000329", "limit": "not-an-int"})
        assert isinstance(result, str)
        assert "error" in result.lower()


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------


class TestDegradation:
    def test_an_unreachable_server_yields_no_tools_and_does_not_raise(self) -> None:
        # Port 1 on localhost: nothing listens, and the connection is refused
        # immediately rather than hanging, so this does not depend on a timeout.
        tools = discover_mcp_tools(McpServer(name="dead", url="http://127.0.0.1:1/mcp"))
        assert tools == []

    def test_a_url_that_is_not_an_mcp_server_yields_no_tools(self, mcp_url: str) -> None:
        # The right host, the wrong path: the descriptor route, not the transport.
        base = mcp_url.rsplit("/mcp", 1)[0]
        assert discover_mcp_tools(McpServer(name="wrong-path", url=f"{base}/nope")) == []

    def test_a_command_style_server_is_declined_explicitly(self) -> None:
        """Only Streamable HTTP is supported; a stdio server is declined with a log
        rather than failing somewhere deeper in the SDK."""
        assert discover_mcp_tools(McpServer(name="stdio", command=["some-server"])) == []


# --------------------------------------------------------------------------
# Against the real NEMAR server. Deselected in CI (`-m "not network"`).
# --------------------------------------------------------------------------


@pytest.mark.network
class TestAgainstProductionNemar:
    """The end-to-end proof, run on demand rather than in CI.

    The fixture-server tests above prove the wrapper. They cannot prove that
    NEMAR's actual server still presents the tools this assistant's prompt tells
    the model about -- a rename or a removal there would break the assistant
    while every test above stayed green. This closes that gap when someone runs
    it: `uv run pytest tests/test_tools/test_mcp_client.py -m network`.
    """

    URL = "https://mcp.nemar.org/mcp"

    def test_discovers_the_documented_tool_set(self) -> None:
        tools = discover_mcp_tools(McpServer(name="nemar", url=self.URL))
        assert {t.name for t in tools} == {
            "nemar_search_datasets",
            "nemar_describe_dataset",
            "nemar_list_recordings",
            "nemar_get_events",
            "nemar_render_overview",
            "nemar_read_window",
        }

    async def test_search_returns_real_datasets(self) -> None:
        tools = discover_mcp_tools(McpServer(name="nemar", url=self.URL))
        search = next(t for t in tools if t.name == "nemar_search_datasets")
        result = await search.ainvoke({"query": "motor imagery", "limit": 2})
        assert isinstance(result, dict)
        assert result["count"] > 0
        assert len(result["results"]) == 2
        # Dataset ids are nm/on plus six digits, not OpenNeuro ds accessions --
        # the assistant's prompt says so, so it is worth holding the server to.
        for row in result["results"]:
            assert row["dataset_id"][:2] in {"nm", "on"}

    async def test_a_refusal_arrives_as_readable_text(self) -> None:
        """The behaviour the prompt tells the model to relay: a declined request
        explains itself and names a workaround."""
        tools = discover_mcp_tools(McpServer(name="nemar", url=self.URL))
        describe = next(t for t in tools if t.name == "nemar_describe_dataset")
        result = await describe.ainvoke({"dataset_id": "nm999999"})
        assert isinstance(result, str)
        assert "not found" in result.lower()
