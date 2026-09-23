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
import base64
import binascii
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import ValidationError

from src.api.tool_results import ToolResultImage, png_dimensions
from src.core.limits import MAX_IMAGES
from src.tools.client_tools import _TRUTHY

if TYPE_CHECKING:
    from src.core.config.community import McpServer

logger = logging.getLogger(__name__)

#: Environment variable name for the incident-control kill switch on MCP images.
#: Modeled on `src.tools.client_tools.CLIENT_TOOL_KILL_SWITCH_ENV`: setting it to any
#: of `_TRUTHY` withdraws every MCP image without touching a community's config.yaml,
#: and without restarting anything, since it is read fresh on every call rather than
#: baked in at tool-discovery time.
MCP_IMAGES_KILL_SWITCH_ENV = "OSA_MCP_IMAGES_DISABLED"


def mcp_images_disabled() -> bool:
    """True when the kill switch env var is set to a truthy value.

    Checked live, like `client_tools_disabled`, so a deployment can flip this without
    a restart.
    """
    return os.environ.get(MCP_IMAGES_KILL_SWITCH_ENV, "").strip().lower() in _TRUTHY


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

#: Cache of discovered tools, keyed by (server name, url, allow_images). Guarded by
#: a lock because discovery can be entered from more than one worker thread. The
#: third key element matters: the same server discovered once for an Anthropic
#: request and once for an OpenRouter request must not share one cached, already-
#: wrapped tool list, or whichever request discovered first would decide whether
#: every later request of BOTH kinds gets images.
_tool_cache: dict[tuple[str, str, bool], tuple[float, list[BaseTool]]] = {}
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


def _accept_mcp_image(
    block: Any, *, accepted_so_far: int
) -> tuple[dict[str, Any] | None, str | None]:
    """One MCP `ImageContent` block, accepted as a content block or refused as text.

    Only `image/png` is accepted (NEMAR's `render_overview` is the only MCP tool that
    returns one today, and it renders PNG), and only within `MAX_IMAGES`,
    `MAX_IMAGE_BYTES` and `MAX_IMAGE_EDGE_PX` (`src.core.limits`). Width and height are
    read from the PNG itself (`png_dimensions`): unlike a browser execution's
    `ToolResultImage`, an MCP `ImageContent` never carries them.

    Returns `(content_block, None)` when accepted, in exactly the shape
    `ToolResultImage.to_content_block` builds -- the one spelling
    `tests/test_core/test_tool_result_image_transport.py` proves survives the real
    Anthropic payload builder -- or `(None, reason)` naming why it was not attached.
    """
    if accepted_so_far >= MAX_IMAGES:
        return None, f"over the MAX_IMAGES cap ({MAX_IMAGES})"
    mime_type = getattr(block, "mime_type", None)
    if mime_type != "image/png":
        return None, f"only image/png is accepted here (got {mime_type!r})"
    data = getattr(block, "data", None) or ""
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None, "its data is not valid base64"
    try:
        width, height = png_dimensions(decoded)
    except ValueError as err:
        return None, str(err)
    try:
        image = ToolResultImage(mime="image/png", data_base64=data, width=width, height=height)
    except ValidationError as err:
        # MAX_IMAGE_BYTES and MAX_IMAGE_EDGE_PX are both enforced by this model's own
        # validators (src.api.tool_results), so a rejection here is one of those two
        # caps; the message names which.
        return None, err.errors()[0]["msg"]
    return image.to_content_block(), None


def _content_of(result: Any, *, allow_images: bool) -> Any:
    """What the model should see for a tool result, images included when allowed.

    Identical to `_payload_of` whenever the result carries no image content, which
    is every NEMAR tool except `render_overview` today: a server that never returns
    a picture is unaffected by any of this.

    When it does, and `allow_images` says this model path accepts one
    (`src.assistants.community.CommunityAssistant`'s `allow_mcp_images`, threaded
    from the request's already-resolved provider choice -- this function does not
    decide or re-check that itself), the accepted images become real content
    blocks after `_accept_mcp_image`; anything refused, including every image when
    `allow_images` is False or the kill switch (`mcp_images_disabled`) is set,
    becomes a text line here saying so.

    Returns a scalar (dict or str, matching `_payload_of`) when nothing pictorial
    survives, and a list of content blocks -- one text block, then one per
    accepted image -- only when at least one image is actually attached. LangChain
    `_format_output` JSON-stringifies a malformed list wholesale
    (`langchain_core.tools.base`), which is why this never returns a list with
    nothing real in it.
    """
    if getattr(result, "is_error", False):
        return f"The tool reported an error: {_text_of(result) or 'no detail given'}"

    blocks = getattr(result, "content", None) or []
    image_blocks = [block for block in blocks if getattr(block, "type", None) == "image"]
    if not image_blocks:
        return _payload_of(result)

    structured = getattr(result, "structured_content", None)
    text_value = structured if structured is not None else _text_of(result)
    text = text_value if isinstance(text_value, str) else json.dumps(text_value)

    accepted: list[dict[str, Any]] = []
    notes: list[str] = []
    for index, block in enumerate(image_blocks):
        if not allow_images:
            content_block, reason = None, "images are not sent to this model"
        else:
            content_block, reason = _accept_mcp_image(block, accepted_so_far=len(accepted))
        if content_block is not None:
            accepted.append(content_block)
        else:
            notes.append(f"[image {index + 1} of {len(image_blocks)} not attached: {reason}]")

    if notes:
        text = f"{text}\n\n" + "\n".join(notes)
    if not accepted:
        return text
    return [{"type": "text", "text": text}, *accepted]


def _wrap_tool(server: McpServer, url: str, mcp_tool: Any, *, allow_images: bool) -> BaseTool:
    """One MCP tool as a LangChain `StructuredTool`.

    The name is prefixed with the server's name (`nemar_search_datasets`) so two
    servers offering a `search` cannot collide in one assistant's tool list.

    `allow_images` is baked in here, at wrap time, rather than read inside `_call`:
    it is the caller's already-made provider decision (see `_content_of`), and the
    point of passing it in rather than inspecting anything at call time is that
    there is nothing left here to sniff. The MCP kill switch is the one exception,
    checked fresh on every call (`mcp_images_disabled`) rather than baked in, so an
    incident does not have to wait out `TOOL_CACHE_TTL_S`.
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
        return _content_of(result, allow_images=allow_images and not mcp_images_disabled())

    return StructuredTool(
        name=f"{server.name}_{tool_name}",
        description=mcp_tool.description or f"{tool_name} on the {server.name} MCP server",
        # The server's own JSON Schema, verbatim. Not re-derived into a pydantic
        # model: the server is the authority on what it accepts, and every
        # translation step is a chance to disagree with it.
        args_schema=mcp_tool.input_schema,
        coroutine=_call,
    )


async def _discover(server: McpServer, url: str, *, allow_images: bool) -> list[BaseTool]:
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
            tools.append(_wrap_tool(server, url, mcp_tool, allow_images=allow_images))
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


def discover_mcp_tools(server: McpServer, *, allow_images: bool = False) -> list[BaseTool]:
    """Connect to `server`, list its tools, and return them as LangChain tools.

    Safe to call from synchronous code whether or not an event loop is already
    running on the calling thread (see the module docstring). Returns `[]` and
    logs on any failure; never raises.

    `allow_images` decides, for every tool this discovers, whether an MCP image
    result is attached as a real content block or reduced to a placeholder (see
    `_content_of`). It is part of the cache key below rather than a detail baked
    silently into a shared cached tool: the SAME server can be discovered once for
    an Anthropic request and once for an OpenRouter request, and each must get
    tools shaped for its own path, not whichever one happened to discover first.
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
    key = (server.name, url, allow_images)

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
        future = pool.submit(lambda: asyncio.run(_discover(server, url, allow_images=allow_images)))
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
