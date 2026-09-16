"""Model Context Protocol (MCP) client: turn a remote MCP server's tools into
LangChain tools the assistant can call.

WHY THIS IS DIRECT SDK USAGE AND NOT `langchain-mcp-adapters`. That package
would be the obvious choice and it cannot be used: it pins `mcp<2.0.0`, and the
2026-07-28 protocol revision this is built against only exists in `mcp` 2.x. So
this module does the wrapping itself, which is about sixty lines.

TWO DESIGN POINTS WORTH READING BEFORE CHANGING ANYTHING HERE.

**Discovery runs in a worker thread, and that is not a workaround.** Tools are
assembled in `CommunityAssistant.__init__`, a SYNCHRONOUS constructor, which the
FastAPI app may itself call from inside a running event loop. `asyncio.run`
raises `RuntimeError` there ("cannot be called from a running event loop"), and
there is no correct synchronous way to await from inside a live loop on the same
thread. A dedicated thread with its own loop is correct whether or not the caller
has one, so this works identically at import time, in a script, and inside a
request handler. Tool INVOCATION needs none of this: it is a plain coroutine that
LangChain awaits on the caller's own loop.

**A session per call is the right shape for this server, not laziness.** The
NEMAR MCP server is stateless by design: no session id, nothing to keep alive,
and `GET`/`DELETE` on the endpoint are 405 because there is no stream to resume.
Connect-call-close therefore costs one round trip and removes every piece of
lifecycle management a long-lived connection would need -- reconnection,
liveness, and a shared object whose failure mode is every tool breaking at once.
Do not "optimize" this into a persistent client without first checking that the
server has something to persist.

**Failure degrades the assistant, it never breaks it.** An unreachable server
yields an empty tool list and a log line, matching `_load_plugin_tools`' contract
in `src/assistants/community.py`. An assistant that cannot start because someone
else's host is down is worse than one that is missing a few tools.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, StructuredTool

if TYPE_CHECKING:
    from src.core.config.community import McpServer

logger = logging.getLogger(__name__)

#: How long to wait for a server to list its tools before giving up and starting
#: without them. Discovery blocks the assistant's constructor, so this is a
#: startup-latency budget, not a network timeout: a slow server must not hold up
#: an app boot.
DISCOVERY_TIMEOUT_S = 20.0

#: How long a single tool call may take. Generous, because a tool may legitimately
#: read a remote index or render an image, but not unbounded.
CALL_TIMEOUT_S = 60.0


def _text_of(result: Any) -> str:
    """Join every text block of a `CallToolResult`.

    Joined rather than "the first one" or "the last one": a multi-block answer
    otherwise reaches the model as an arbitrary fragment of itself.
    """
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _payload_of(result: Any) -> Any:
    """What the model should see for a tool result.

    `structured_content` when the server sent it, because these tools return
    structured JSON and a model reasons better over the object than over its
    rendering. Falls back to joined text.

    An `is_error` result is returned as TEXT rather than raised. The server's
    error messages are written to be read -- they name the cap that was exceeded,
    or the public URL to fetch instead -- so handing that sentence to the model
    lets it correct itself, where an exception would just end the turn.
    """
    if getattr(result, "is_error", False):
        return f"The tool reported an error: {_text_of(result) or 'no detail given'}"
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    return _text_of(result)


def _wrap_tool(server: McpServer, url: str, mcp_tool: Any) -> BaseTool:
    """One MCP tool as a LangChain `StructuredTool`.

    The name is prefixed with the server's name (`nemar_search_datasets`) so two
    servers offering a `search` cannot collide in one assistant's tool list.
    """
    tool_name = mcp_tool.name

    async def _call(**kwargs: Any) -> Any:
        # Imported here, not at module scope: this module is imported during
        # config loading, and a missing optional dependency should surface as
        # "MCP tools unavailable" from the loader below rather than as an
        # ImportError that takes the whole app down.
        from mcp import Client

        async with Client(url) as client:
            result = await asyncio.wait_for(
                client.call_tool(tool_name, kwargs), timeout=CALL_TIMEOUT_S
            )
        return _payload_of(result)

    return StructuredTool(
        name=f"{server.name}_{tool_name}",
        description=mcp_tool.description or f"{tool_name} on the {server.name} MCP server",
        # The server's own JSON Schema, verbatim. Not re-derived into a pydantic
        # model: the server is the authority on what it accepts, and every
        # translation step is a chance to disagree with it.
        args_schema=mcp_tool.input_schema,
        coroutine=_call,
    )


async def _discover(server: McpServer, url: str) -> list[BaseTool]:
    from mcp import Client

    async with Client(url) as client:
        listed = await asyncio.wait_for(client.list_tools(), timeout=DISCOVERY_TIMEOUT_S)
    tools = [_wrap_tool(server, url, mcp_tool) for mcp_tool in listed.tools]
    logger.info("Discovered %d tool(s) from MCP server %s at %s", len(tools), server.name, url)
    return tools


def discover_mcp_tools(server: McpServer) -> list[BaseTool]:
    """Connect to `server`, list its tools, and return them as LangChain tools.

    Safe to call from synchronous code whether or not an event loop is already
    running on the calling thread (see the module docstring). Returns `[]` and
    logs on any failure; never raises.
    """
    if server.url is None:
        # `command`-style (stdio) servers are a different transport and nothing
        # configures one today. Declining explicitly beats a confusing failure
        # deeper in the SDK.
        logger.warning(
            "MCP server %s has no url; only remote (Streamable HTTP) servers are supported",
            server.name,
        )
        return []

    url = str(server.url)
    try:
        # A dedicated thread with its own loop. `asyncio.run` here would raise if
        # the caller already has a running loop, which the FastAPI app does.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-discover") as pool:
            return pool.submit(lambda: asyncio.run(_discover(server, url))).result()
    except Exception as exc:  # noqa: BLE001 - degrade, never break the assistant
        logger.error("Could not load tools from MCP server %s at %s: %s", server.name, url, exc)
        return []
