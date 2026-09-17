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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
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

#: How long a discovered tool list stays usable before it is fetched again.
#:
#: Discovery is NOT a boot-time cost. `create_community_assistant` is uncached and
#: runs per request, so without this every chat message paid a full MCP handshake
#: plus `tools/list` against a third-party host before the model was called. The
#: NEMAR server itself advertises `ttlMs: 86400000` on `tools/list`; this is far
#: more conservative so a tool rename is picked up within minutes.
TOOL_CACHE_TTL_S = 300.0

#: Cache of discovered tools, keyed by (server name, url). Guarded by a lock
#: because discovery can be entered from more than one worker thread.
_tool_cache: dict[tuple[str, str], tuple[float, list[BaseTool]]] = {}
_tool_cache_lock = threading.Lock()


def clear_tool_cache() -> None:
    """Drop every cached tool list. For tests, and for an operator-triggered reload."""
    with _tool_cache_lock:
        _tool_cache.clear()


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

        async def _once() -> Any:
            # The connect and protocol handshake are INSIDE the timeout. They used
            # to sit outside it, where the only bound was the SDK's 300 s read
            # default, so a server that accepted the socket and then stalled hung
            # the call for five minutes.
            async with Client(url) as client:
                return await client.call_tool(tool_name, kwargs)

        try:
            result = await asyncio.wait_for(_once(), timeout=CALL_TIMEOUT_S)
        except TimeoutError:
            logger.warning(
                "MCP tool %s on %s did not answer within %.0fs", tool_name, url, CALL_TIMEOUT_S
            )
            return (
                f"The {tool_name} tool did not answer within {CALL_TIMEOUT_S:.0f} seconds. "
                "Try a narrower request, or tell the user that NEMAR's tool server is slow "
                "right now."
            )
        except Exception as exc:  # noqa: BLE001 - a dead turn is worse than a degraded one
            # langgraph's default ToolNode handler converts only ToolInvocationError
            # and re-raises everything else, and these tools carry a raw JSON Schema
            # rather than a pydantic model, so that error never occurs for them.
            # Without this branch a transport blip ends the turn with a generic
            # "An error occurred" and discards whatever was already streamed.
            logger.exception("MCP tool %s on %s failed", tool_name, url)
            return (
                f"The {tool_name} tool could not be reached ({type(exc).__name__}). "
                "Tell the user NEMAR's dataset service is temporarily unavailable and "
                "point them at https://nemar.org/discover."
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

    async def _list() -> Any:
        # Connect and handshake inside the timeout; see the note in `_call`.
        async with Client(url) as client:
            return await client.list_tools()

    listed = await asyncio.wait_for(_list(), timeout=DISCOVERY_TIMEOUT_S)

    # One tool at a time. A single descriptor the wrapper rejects used to take the
    # whole server's tool list down with it, and the log then blamed the network.
    tools: list[BaseTool] = []
    for mcp_tool in listed.tools:
        try:
            tools.append(_wrap_tool(server, url, mcp_tool))
        except Exception:  # noqa: BLE001 - skip the offender, keep the rest
            logger.exception(
                "Skipping tool %r from MCP server %s: its descriptor could not be wrapped",
                getattr(mcp_tool, "name", "<unnamed>"),
                server.name,
            )

    if getattr(listed, "next_cursor", None):
        logger.warning(
            "MCP server %s returned a paginated tool list; only the first page is used",
            server.name,
        )

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
    key = (server.name, url)

    with _tool_cache_lock:
        cached = _tool_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < TOOL_CACHE_TTL_S:
            return list(cached[1])

    # A dedicated thread with its own loop. `asyncio.run` here would raise if the
    # caller already has a running loop, which the FastAPI app does.
    #
    # NOT a `with` block: ThreadPoolExecutor.__exit__ calls shutdown(wait=True),
    # which joins the worker and would re-block the caller for exactly as long as
    # the timeout below is meant to prevent. `.result(timeout=...)` plus a
    # non-joining shutdown is what actually bounds this. It matters because
    # `.result()` is a synchronous block run on the API's event loop thread, so an
    # unbounded wait here freezes every community's requests, not just this one.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-discover")
    try:
        future = pool.submit(lambda: asyncio.run(_discover(server, url)))
        tools = future.result(timeout=DISCOVERY_TIMEOUT_S + 5.0)
    except FutureTimeoutError:
        logger.error(
            "MCP server %s at %s did not answer within %.0fs; starting without its tools",
            server.name,
            url,
            DISCOVERY_TIMEOUT_S + 5.0,
        )
        return []
    except ImportError as exc:
        logger.error(
            "MCP support unavailable for server %s; install the server extra "
            "(uv sync --extra server): %s",
            server.name,
            exc,
        )
        return []
    except Exception as exc:  # noqa: BLE001 - degrade, never break the assistant
        logger.error(
            "Could not load tools from MCP server %s at %s (%s): %s",
            server.name,
            url,
            type(exc).__name__,
            exc,
        )
        return []
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    with _tool_cache_lock:
        _tool_cache[key] = (time.monotonic(), list(tools))
    return tools
