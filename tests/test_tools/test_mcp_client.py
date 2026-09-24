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
import base64
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from langchain_core.messages import ToolMessage
from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

from src.api.tool_results import IMAGES_NOT_SENT
from src.core.config.community import McpServer
from src.core.limits import MAX_IMAGE_BYTES, MAX_IMAGE_EDGE_PX
from src.tools.mcp_client import (
    MCP_IMAGES_KILL_SWITCH_ENV,
    clear_tool_cache,
    discover_mcp_tools,
)
from tests.helpers.images import tiny_png

# --------------------------------------------------------------------------
# A real MCP server on a real port.
# --------------------------------------------------------------------------


#: The fixture server's own tiny PNG, real bytes from zlib/struct
#: (`tests.helpers.images.tiny_png`), not a stand-in. Module-level so every test
#: that inspects `render_overview`'s output compares against the same image.
FIXTURE_PNG = tiny_png(width=6, height=4)
FIXTURE_PNG_B64 = base64.b64encode(FIXTURE_PNG).decode()


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

    @srv.tool(structured_output=False)
    def render_overview(dataset_id: str) -> CallToolResult:
        """Answer the way nemar_render_overview does: structured content plus a
        real PNG `ImageContent` block, standing in for the tool this fixture
        server cannot actually run."""
        return CallToolResult(
            content=[
                TextContent(type="text", text=f"overview of {dataset_id}"),
                ImageContent(type="image", data=FIXTURE_PNG_B64, mimeType="image/png"),
            ],
            structuredContent={"dataset_id": dataset_id},
        )

    @srv.tool(structured_output=False)
    def many_images(count: int) -> CallToolResult:
        """`count` real PNGs in one result, to exercise MAX_IMAGES for real."""
        return CallToolResult(
            content=[
                ImageContent(
                    type="image",
                    data=base64.b64encode(tiny_png(width=2, height=2)).decode(),
                    mimeType="image/png",
                )
                for _ in range(count)
            ],
        )

    @srv.tool(structured_output=False)
    def undecodable_image() -> CallToolResult:
        """Claims image/png but is not decodable as one."""
        return CallToolResult(
            content=[
                ImageContent(
                    type="image", data=base64.b64encode(b"not a png").decode(), mimeType="image/png"
                )
            ],
        )

    @srv.tool(structured_output=False)
    def oversized_images() -> CallToolResult:
        """Three real PNGs, each over a different cap: bytes (the PNG followed by
        padding, which leaves its IHDR readable), edge, and both at once."""
        wide = tiny_png(width=MAX_IMAGE_EDGE_PX + 1, height=1)
        padding = b"\x00" * MAX_IMAGE_BYTES
        return CallToolResult(
            content=[
                ImageContent(
                    type="image",
                    data=base64.b64encode(image).decode(),
                    mimeType="image/png",
                )
                for image in (FIXTURE_PNG + padding, wide, wide + padding)
            ],
        )

    @srv.tool(structured_output=False)
    def wrong_mime_image() -> CallToolResult:
        """A real, valid PNG, but declared under a media type this reader refuses
        regardless of size or validity -- only image/png is accepted here."""
        return CallToolResult(
            content=[ImageContent(type="image", data=FIXTURE_PNG_B64, mimeType="image/jpeg")],
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
            "fixture_render_overview",
            "fixture_many_images",
            "fixture_undecodable_image",
            "fixture_oversized_images",
            "fixture_wrong_mime_image",
        }

    def test_a_per_deployment_url_connects_to_the_running_deployments_server(
        self, mcp_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The develop chat reads the staging server (#480): discovery goes to the
        resolved URL, and never to the other deployment's. Port 1 refuses, so the
        wrong one yields no tools."""
        dead = "http://127.0.0.1:1/mcp"
        monkeypatch.delenv("ROOT_PATH", raising=False)
        server = McpServer(name="fixture", url={"production": dead, "develop": mcp_url})
        monkeypatch.setenv("OSA_DEPLOYMENT", "develop")
        assert "fixture_echo_dataset" in {t.name for t in discover_mcp_tools(server)}
        monkeypatch.setenv("OSA_DEPLOYMENT", "production")
        assert discover_mcp_tools(server) == []

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
        assert len(tools) == 8

    def test_works_from_a_plain_synchronous_caller(self, mcp_url: str) -> None:
        """The other half: no loop running at all."""
        assert len(discover_mcp_tools(_server(mcp_url))) == 8


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
        # The PREFIX, not just the server's sentence. Without this the test passes
        # even if the `is_error` branch is deleted, because the fallback path
        # returns the same sentence unprefixed (mutation-checked).
        assert result.startswith("The tool reported an error:")
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
# Images: nemar_render_overview's PNG, real end to end against the fixture
# server (issue #432, phase 3). Invoked through a `ToolCall` dict, never a bare
# args dict, because only that path makes LangChain build a real `ToolMessage`
# (`_format_output`) -- a bare args dict just returns `_call`'s raw return
# value, which is not what proves the content-block list survives the wrapper
# a real `ToolNode` uses.
# --------------------------------------------------------------------------


def _call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class TestImages:
    def setup_method(self) -> None:
        # Discovery is cached for TOOL_CACHE_TTL_S; without this, a test earlier
        # in the module that discovered "fixture" at one allow_images value could
        # answer a later test's discovery call at a different one.
        clear_tool_cache()

    async def test_an_accepted_image_reaches_a_real_toolmessage_as_a_content_block(
        self, mcp_url: str
    ) -> None:
        tool = next(
            t
            for t in discover_mcp_tools(_server(mcp_url), allow_images=True)
            if t.name.endswith("render_overview")
        )

        result = await tool.ainvoke(_call(tool.name, {"dataset_id": "nm000103"}))

        assert isinstance(result, ToolMessage)
        assert isinstance(result.content, list), (
            "a malformed block would make LangChain's _format_output stringify "
            "this list wholesale; asserting the type is what catches that"
        )
        text_blocks = [b for b in result.content if b.get("type") == "text"]
        image_blocks = [b for b in result.content if b.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": FIXTURE_PNG_B64},
        }
        assert '"dataset_id": "nm000103"' in text_blocks[0]["text"]

    async def test_disallowed_by_the_provider_gate_carries_no_image_block(
        self, mcp_url: str
    ) -> None:
        """`allow_images=False` is what a non-Anthropic model path is wrapped with;
        the refusal is baked in at wrap time, not decided per call."""
        tool = next(
            t
            for t in discover_mcp_tools(_server(mcp_url), allow_images=False)
            if t.name.endswith("render_overview")
        )

        result = await tool.ainvoke({"dataset_id": "nm000103"})

        assert isinstance(result, str)
        assert FIXTURE_PNG_B64 not in result
        assert "[image 1 of 1 not attached: images are not sent to this model]" in result

    async def test_the_kill_switch_withdraws_an_otherwise_allowed_image(
        self, mcp_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Checked live inside the call, not baked in at wrap time: unlike the
        provider gate above, this must win even on a tool wrapped with
        allow_images=True, because it exists to be flippable without a restart.
        So the tool is wrapped first and the switch is set after, which a switch read
        at wrap time would miss."""
        tool = next(
            t
            for t in discover_mcp_tools(_server(mcp_url), allow_images=True)
            if t.name.endswith("render_overview")
        )
        monkeypatch.setenv(MCP_IMAGES_KILL_SWITCH_ENV, "1")

        result = await tool.ainvoke({"dataset_id": "nm000103"})

        assert isinstance(result, str)
        assert FIXTURE_PNG_B64 not in result
        assert "[image 1 of 1 not attached: images are not sent to this model]" in result

    async def test_only_image_png_is_accepted(self, mcp_url: str) -> None:
        tool = next(
            t
            for t in discover_mcp_tools(_server(mcp_url), allow_images=True)
            if t.name.endswith("wrong_mime_image")
        )

        result = await tool.ainvoke({})

        assert isinstance(result, str), "nothing was accepted, so this stays a plain string"
        assert FIXTURE_PNG_B64 not in result
        assert "only image/png is accepted" in result

    async def test_data_that_does_not_decode_as_a_png_is_refused(self, mcp_url: str) -> None:
        tool = next(
            t
            for t in discover_mcp_tools(_server(mcp_url), allow_images=True)
            if t.name.endswith("undecodable_image")
        )

        result = await tool.ainvoke({})

        assert isinstance(result, str)
        assert "[image 1 of 1 not attached: not a valid PNG (wrong signature)]" in result

    async def test_max_images_caps_how_many_of_one_results_images_are_attached(
        self, mcp_url: str
    ) -> None:
        from src.core.limits import MAX_IMAGES

        tool = next(
            t
            for t in discover_mcp_tools(_server(mcp_url), allow_images=True)
            if t.name.endswith("many_images")
        )

        result = await tool.ainvoke(_call(tool.name, {"count": MAX_IMAGES + 2}))

        assert isinstance(result, ToolMessage)
        image_blocks = [b for b in result.content if b.get("type") == "image"]
        assert len(image_blocks) == MAX_IMAGES
        text_blocks = [b for b in result.content if b.get("type") == "text"]
        assert any("MAX_IMAGES" in b["text"] for b in text_blocks)

    async def test_each_cap_is_named_when_an_image_breaks_it(self, mcp_url: str) -> None:
        tool = next(
            t
            for t in discover_mcp_tools(_server(mcp_url), allow_images=True)
            if t.name.endswith("oversized_images")
        )

        result = await tool.ainvoke({})

        assert isinstance(result, str), "every image was refused, so no block list"
        lines = [line for line in result.splitlines() if line.startswith("[image ")]
        assert len(lines) == 3
        assert "over the" in lines[0] and "byte cap" in lines[0]
        assert f"less than or equal to {MAX_IMAGE_EDGE_PX}" in lines[1]
        assert "byte cap" in lines[2] and f"less than or equal to {MAX_IMAGE_EDGE_PX}" in lines[2]

    async def test_one_server_discovered_for_both_paths_keeps_them_apart(
        self, mcp_url: str
    ) -> None:
        """Discovery is cached per server for minutes, and an Anthropic request and an
        OpenRouter request can discover the same server inside that window. Each must
        get tools wrapped for its own path, whichever discovered first."""
        for first, second in ((True, False), (False, True)):
            clear_tool_cache()
            discovered = {
                allow: next(
                    t
                    for t in discover_mcp_tools(_server(mcp_url), allow_images=allow)
                    if t.name.endswith("render_overview")
                )
                for allow in (first, second)
            }

            with_images = await discovered[True].ainvoke(
                _call(discovered[True].name, {"dataset_id": "nm000103"})
            )
            without = await discovered[False].ainvoke({"dataset_id": "nm000103"})

            assert any(b.get("type") == "image" for b in with_images.content), (first, second)
            assert isinstance(without, str) and IMAGES_NOT_SENT in without, (first, second)


class TestTheRequestProviderDecides:
    """The entry point: `create_community_assistant` resolves the request's provider
    and the MCP tools it binds must follow it. Tested through the assistant's own
    tool list, so a gate dropped or inverted in the factory fails here."""

    COMMUNITY = "mcpimagegate"

    async def _render_overview_through(self, mcp_url: str, provider: str) -> Any:
        from src.api.routers.community import create_community_assistant
        from src.api.security import ByokCredential
        from src.assistants.registry import registry
        from src.core.config.community import CommunityConfig

        clear_tool_cache()
        registry.register_from_config(
            CommunityConfig(
                id=self.COMMUNITY,
                name="MCP image gate",
                description="Binds the fixture MCP server",
                extensions={"mcp_servers": [{"name": "fixture", "url": mcp_url}]},
            )
        )
        try:
            # Construction never calls the provider, so a placeholder key is enough.
            key = "sk-ant-test" if provider == "anthropic" else "sk-or-test"
            awm = create_community_assistant(
                self.COMMUNITY, byok=ByokCredential(key=key, provider=provider), preload_docs=False
            )
        finally:
            registry._assistants.pop(self.COMMUNITY, None)
        tool = next(t for t in awm.assistant.tools if t.name == "fixture_render_overview")
        return await tool.ainvoke(_call(tool.name, {"dataset_id": "nm000103"}))

    async def test_an_anthropic_request_sees_the_image(self, mcp_url: str) -> None:
        result = await self._render_overview_through(mcp_url, "anthropic")

        assert any(b.get("type") == "image" for b in result.content)

    async def test_an_openrouter_request_gets_the_placeholder(self, mcp_url: str) -> None:
        result = await self._render_overview_through(mcp_url, "openrouter")

        assert isinstance(result.content, str)
        assert IMAGES_NOT_SENT in result.content
        assert FIXTURE_PNG_B64 not in result.content


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------


class TestDegradation:
    def test_an_unreachable_server_yields_no_tools_and_does_not_raise(self) -> None:
        # Port 1 on localhost: nothing listens, and the connection is refused
        # immediately rather than hanging, so this does not depend on a timeout.
        tools = discover_mcp_tools(McpServer(name="dead", url="http://127.0.0.1:1/mcp"))
        assert tools == []

    def test_a_stalled_server_gives_up_instead_of_hanging(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure that matters, and the one an unreachable port does NOT cover.

        A refused connection fails instantly. A server that completes the TCP
        handshake and then never answers used to hang forever: the discovery
        timeout wrapped only `list_tools()`, leaving connect bounded solely by the
        SDK's 300 s read default, and `.result()` carried no timeout at all. Since
        `discover_mcp_tools` is called per request from the API's event loop
        thread, that froze every community, not just this one.
        """
        import socket as _socket

        listener = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        listener.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        port = listener.getsockname()[1]
        accepted: list[Any] = []

        def _accept_and_stall() -> None:
            # Accept so the client's connect() succeeds, then never respond. Hold a
            # reference so the socket is not garbage collected into a reset.
            while True:
                try:
                    accepted.append(listener.accept()[0])
                except OSError:
                    return

        stall_thread = threading.Thread(target=_accept_and_stall, daemon=True)
        stall_thread.start()

        monkeypatch.setattr("src.tools.mcp_client.DISCOVERY_TIMEOUT_S", 2.0)
        try:
            started = time.monotonic()
            tools = discover_mcp_tools(
                McpServer(name="stalled", url=f"http://127.0.0.1:{port}/mcp")
            )
            elapsed = time.monotonic() - started
        finally:
            listener.close()
            for conn in accepted:
                conn.close()

        assert tools == []
        # Generous, but far below the SDK's 300 s read default, which is what this
        # would take if the bound were lost again.
        assert elapsed < 30.0, f"discovery took {elapsed:.1f}s; the timeout is not bounding connect"

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
        """The behavior the prompt tells the model to relay: a declined request
        explains itself and names a workaround."""
        tools = discover_mcp_tools(McpServer(name="nemar", url=self.URL))
        describe = next(t for t in tools if t.name == "nemar_describe_dataset")
        result = await describe.ainvoke({"dataset_id": "nm999999"})
        assert isinstance(result, str)
        assert "not found" in result.lower()

    async def test_render_overview_reaches_the_model_as_an_image_block(self) -> None:
        """The point of the whole gate: NEMAR's own PNG, through OSA's own client,
        on the Anthropic path (`allow_images=True`, resolved once here rather than
        per-block, exactly as `CommunityAssistant` resolves it once at wrap time)."""
        tools = discover_mcp_tools(McpServer(name="nemar", url=self.URL), allow_images=True)
        list_recordings = next(t for t in tools if t.name == "nemar_list_recordings")
        recordings = await list_recordings.ainvoke({"dataset_id": "nm000103", "limit": 1})
        recording = recordings["recordings"][0]

        render_overview = next(t for t in tools if t.name == "nemar_render_overview")
        result = await render_overview.ainvoke(
            _call(
                render_overview.name,
                {
                    "dataset_id": "nm000103",
                    "recording": recording["path"],
                    "group": recording["groups"][0]["name"],
                },
            )
        )

        assert isinstance(result, ToolMessage)
        assert isinstance(result.content, list)
        image_blocks = [b for b in result.content if b.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["source"]["media_type"] == "image/png"
        assert len(image_blocks[0]["source"]["data"]) > 0
