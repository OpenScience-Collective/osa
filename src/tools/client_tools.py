"""Tools the server binds so the model can call them, but never executes.

Phase 1 of the browser-execution feature (epic #429, issue #430; see
.context/browser-execution-tool-design.md) teaches the graph to call a tool
it does not run itself: the model is bound a normal-looking tool, but any
call to it is routed to a `client_tools` graph node
(`src.agents.base.BaseAgent`) that parks the call for the browser to execute
in a second run, rather than to LangGraph's `ToolNode`. This module owns the
tool class and the two functions that decide, at bind time, which client
tools a given request is even allowed to see.

Phase 1 ships with no community enabling this (no shipped config.yaml
declares `extensions.client_tools`), so `build_client_tools` returns `[]`
for every real request today. It exists so phase 2 (#431), which builds the
browser-side executor and the permission gate, has a tested server contract
to run against.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.core.config.community import CommunityConfig

logger = logging.getLogger(__name__)

CLIENT_TOOL_KILL_SWITCH_ENV = "OSA_CLIENT_TOOLS_DISABLED"
"""Environment variable name. Any truthy value (see `client_tools_disabled`)
strips every client tool regardless of what a community's config.yaml
declares.

`develop` auto-deploys on every push, and a client tool with no executor
parks every stream that calls it -- this switch is what makes it safe to
merge the graph plumbing before phase 2 (#431) exists to answer a
`pending_client_call`.
"""

_TRUTHY = {"1", "true", "yes", "on"}


class ClientToolNotExecutableError(RuntimeError):
    """Raised if a `ClientTool`'s `_run` or `_arun` is ever actually reached.

    In correct operation this never happens: `BaseAgent._should_use_tools`
    routes any assistant message whose `tool_calls` include a client tool
    name to the `client_tools` node instead of the plain `tools` node, so
    LangGraph's `ToolNode` never invokes a `ClientTool` directly. Reaching
    this is therefore a routing bug -- for example, a `ToolNode` built over
    every tool (client and server alike) invoked on a message that contains
    a client tool call -- not a runtime condition to recover from.
    """


class ExecuteCodeArgs(BaseModel):
    """Argument schema for a `runtime: "python"` client tool.

    Every client tool a community declares with `runtime: "python"` in its
    config.yaml (see `PythonRuntimeConfig` in `src.core.config.community`)
    shares this shape: the model writes `code` to run in the browser's
    Python runtime, and a short, human-readable `description` of what that
    code does. `description` is required, not just encouraged, because
    phase 2's permission gate shows it to the person before the code runs;
    a tool call with no summary would otherwise render a blank gate.
    """

    code: str = Field(..., description="Python source to execute in the browser runtime.")
    description: str = Field(
        ...,
        description=(
            "Short, human-readable summary of what this code does. Shown to "
            "the user in the permission gate before it runs."
        ),
    )


# Maps a client tool's configured `runtime` value to the pydantic model
# describing that runtime's call arguments. Phase 1 ships exactly one
# runtime ("python"); the indirection through a mapping, rather than a
# hardcoded schema in `build_client_tools`, is what lets a future runtime
# add its own argument shape without changing that function's logic, and is
# what makes an unmapped runtime fail loudly (see `_args_schema_for_runtime`)
# instead of silently binding a tool with no argument validation.
_RUNTIME_ARGS_SCHEMAS: dict[str, type[BaseModel]] = {
    "python": ExecuteCodeArgs,
}


def _args_schema_for_runtime(runtime: str) -> type[BaseModel]:
    """Return the pydantic args schema for a client tool's `runtime` value.

    Args:
        runtime: A `ClientToolConfig.runtime` value (e.g. `"python"`).

    Returns:
        The pydantic model class describing that runtime's call arguments.

    Raises:
        ValueError: If `runtime` has no registered args schema. Today
            `ClientToolConfig.runtime` is typed `Literal["python"]`, so this
            should be unreachable through normal config loading; it exists
            so a future runtime added to that `Literal` without a matching
            entry in `_RUNTIME_ARGS_SCHEMAS` fails at tool-build time with a
            clear message, rather than at the first tool call.
    """
    try:
        return _RUNTIME_ARGS_SCHEMAS[runtime]
    except KeyError:
        known = ", ".join(sorted(_RUNTIME_ARGS_SCHEMAS)) or "(none)"
        raise ValueError(
            f"Unknown client tool runtime '{runtime}'; no argument schema is "
            f"registered for it. Known runtimes: {known}"
        ) from None


class ClientTool(BaseTool):
    """A tool the server binds so the model can call it, but never executes.

    The model sees this exactly like any other bound tool: `name`,
    `description`, and `args_schema` all come from the community's
    `ClientToolConfig` entry. Its `_run` and `_arun` are unreachable in
    correct operation; see `ClientToolNotExecutableError`.
    """

    requires_permission: bool = True
    """Whether the browser must show a permission gate before running a call
    to this tool. Copied from the community's `ClientToolConfig.
    requires_permission` at bind time, so the `client_tools` graph node can
    put it straight onto `pending_client_call` without a second lookup back
    into config."""

    def _run(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ClientToolNotExecutableError(
            f"Client tool '{self.name}' has no server-side executor; the graph "
            "must route its calls to the client_tools node instead of invoking "
            "this tool directly."
        )

    async def _arun(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ClientToolNotExecutableError(
            f"Client tool '{self.name}' has no server-side executor; the graph "
            "must route its calls to the client_tools node instead of invoking "
            "this tool directly."
        )


def client_tools_disabled() -> bool:
    """True when the kill switch env var is set to a truthy value.

    Checked live (not cached) so a deployment can flip the switch without a
    restart. Truthy values are `1`, `true`, `yes`, `on`, matched
    case-insensitively; anything else (including unset) is `False`.
    """
    return os.environ.get(CLIENT_TOOL_KILL_SWITCH_ENV, "").strip().lower() in _TRUTHY


def build_client_tools(config: CommunityConfig, declared: set[str] | None) -> list[ClientTool]:
    """Client tools that are BOTH configured on the community AND declared by the caller.

    Returns `[]` when the kill switch is set, when the config declares none,
    or when `declared` is `None` or empty. Binding only the intersection --
    rather than binding everything configured and checking `declared` again
    at the point a `tool_request` would be emitted -- is what makes "the
    server must never emit a tool_request for a capability the client did
    not claim" structurally true: the model is never bound a tool it cannot
    call, so there is no second call site that has to remember to re-check
    `declared`.

    A caller that declares nothing (an old cached widget that predates this
    feature, for instance, or `/ask`, which strips client tools entirely)
    gets exactly today's behavior: no client tools bound, no `tool_request`
    possible.

    Args:
        config: The community's parsed configuration.
        declared: Client tool names the caller's request body claims it can
            execute. `None` or empty means "declares nothing".

    Returns:
        A `ClientTool` for every entry in `config.extensions.client_tools`
        whose name is also in `declared`, in that list's order.
    """
    configured = list(config.extensions.client_tools) if config.extensions else []

    if client_tools_disabled():
        # Logged only when the switch actually took something away. The kill switch is
        # an incident-response control, so an operator who flips it during a rolling
        # deploy needs server-side proof it reached every instance; a synthetic probe
        # request is not proof. Guarding on `configured` keeps this silent for the
        # communities that were never going to bind a client tool anyway.
        if configured:
            logger.warning(
                "Client tools disabled by %s; stripped %d configured tool(s) from %s",
                CLIENT_TOOL_KILL_SWITCH_ENV,
                len(configured),
                config.id,
                extra={"community_id": config.id, "reason": "kill_switch"},
            )
        return []

    if not configured:
        return []

    if not declared:
        return []

    tools: list[ClientTool] = []
    for entry in configured:
        if entry.name not in declared:
            continue
        args_schema = _args_schema_for_runtime(entry.runtime)
        tools.append(
            ClientTool(
                name=entry.name,
                description=entry.description,
                args_schema=args_schema,
                requires_permission=entry.requires_permission,
            )
        )
    if configured and not tools:
        # Every configured tool was filtered out by what the caller declared. That is
        # the ordinary case for an old cached widget, and it is also what a renamed
        # tool looks like, so it is worth seeing rather than guessing at. Widgets are
        # pinned by SRI hash and persist indefinitely, which makes this drift long
        # lived when it happens.
        logger.info(
            "No client tools bound for %s: configured %s, caller declared %s",
            config.id,
            sorted(entry.name for entry in configured),
            sorted(declared),
            extra={"community_id": config.id, "reason": "declaration_mismatch"},
        )

    return tools
