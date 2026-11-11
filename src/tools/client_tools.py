"""Tools the server binds so the model can call them, but never executes.

Phase 1 of the browser-execution feature (epic #429, issue #430; see
.context/browser-execution-tool-design.md) teaches the graph to call a tool
it does not run itself: the model is bound a normal-looking tool, but any
call to it is routed to a `client_tools` graph node
(`src.agents.base.BaseAgent`) that parks the call for the browser to execute
in a second run, rather than to LangGraph's `ToolNode`. This module owns the
tool class and the two functions that decide, at bind time, which client
tools a given request is even allowed to see.

The browser side is phase 2 (#431): the widget's sealed Pyodide runtime and
its permission gate. NEMAR is the first community to declare a client tool
(`execute_code`), so `build_client_tools` returns a live tool for its
requests unless the kill switch below is set.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, field_validator

from src.core.config.community import (
    EXECUTE_CODE_TOOL_NAME,
    FULL_OUTPUT_TOOL_NAME,
    PROPOSE_PLAN_TOOL_NAME,
    START_ANALYSIS_TOOL_NAME,
    Tier,
)

if TYPE_CHECKING:
    from src.core.config.community import CommunityConfig

logger = logging.getLogger(__name__)

CLIENT_TOOL_KILL_SWITCH_ENV = "OSA_CLIENT_TOOLS_DISABLED"
"""Environment variable name. Any truthy value (see `client_tools_disabled`)
strips every client tool regardless of what a community's config.yaml
declares.

It made it safe to merge phase 1's graph plumbing before any browser could
answer a `pending_client_call`. Now that NEMAR offers `execute_code`, it is
the incident control: setting it withdraws every client tool, the runtime
config and the lock overlay from `/config`, and the wheel route, through the
server's environment rather than any community's config.
"""

TRUTHY = frozenset({"1", "true", "yes", "on"})


def env_flag_set(name: str) -> bool:
    """True when environment variable `name` holds one of `TRUTHY`, case-insensitively.

    Read on every call, so a kill switch built on it flips without a restart.
    """
    return os.environ.get(name, "").strip().lower() in TRUTHY


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


class GetFullOutputArgs(BaseModel):
    """Argument schema for the derived `get_full_output` client tool.

    A result in the conversation carries bounded streams and a summary; the
    untruncated streams, the full traceback and the run's figures stay in the
    reader's browser. This reads them back on demand, so the common path stays
    cheap and the rare one stays possible.
    """

    call_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="The call_id printed in the summary of the earlier run to read.",
    )
    stream: Literal["stdout", "stderr", "traceback", "figures"] = Field(
        "stdout",
        description=(
            "Which output to read. 'figures' re-attaches the run's images, which "
            "the conversation otherwise keeps only as placeholders."
        ),
    )
    offset: int = Field(
        0,
        ge=0,
        description=(
            "Character to start from. A long stream comes back one page at a time; "
            "pass the offset the previous answer gives to read the next page."
        ),
    )


_FULL_OUTPUT_DESCRIPTION = (
    "Read the complete output of an earlier browser execution. Results in this "
    "conversation carry clipped streams and a summary; when a summary says output "
    "was clipped or a traceback is available, call this with the call_id it prints. "
    "Output is kept only in the reader's browser tab, for its most recent runs, so "
    "it is unavailable after a reload."
)


class StartAnalysisArgs(BaseModel):
    """Argument schema for the derived `start_analysis` client tool.

    The call carries no work of its own: the reader's message is what the analysis model
    answers. `reason` exists only so a model has something short to write that is not
    prose for the reader, and it is bounded because it is stored in history.
    """

    reason: str = Field(
        "",
        max_length=300,
        description="One short phrase on why the reader's request needs code or a plot.",
    )


_START_ANALYSIS_DESCRIPTION = (
    "Hand this conversation to the analysis model, which can run code in the reader's "
    "browser. Call it when the reader asks you to run, plot, analyze or compute "
    "something. Calling it ends your turn: write nothing before or after the call, "
    "because the analysis model answers the reader instead."
)


MAX_PLAN_STEPS = 8
"""Most steps one plan may carry, and the most a reader may leave it with after edits."""

MAX_PLAN_STEP_TITLE_CHARS = 120
MAX_PLAN_STEP_DETAIL_CHARS = 600
MAX_PLAN_FINDINGS_CHARS = 2000
MAX_PLAN_PEAK_MB = 8192


class PlanStep(BaseModel):
    """One step of a proposed plan: what it does, in a line, and how, in a few."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=MAX_PLAN_STEP_TITLE_CHARS,
        description="Short name of the step, shown as its heading.",
    )
    detail: str = Field(
        ...,
        max_length=MAX_PLAN_STEP_DETAIL_CHARS,
        description="What the step's one code run does and what it produces.",
    )

    @field_validator("title")
    @classmethod
    def _one_line_title(cls, value: str) -> str:
        """Collapse every run of whitespace, newlines included, to one space.

        The title is a heading on the card and one line of the tool message that answers
        the call, so it may be neither blank nor span lines: a newline in it would add a
        line of its own to that message. Applied to the decision's steps as well as the
        proposal's (both are `PlanStep`), so an edited step stays comparable.
        """
        collapsed = " ".join(value.split())
        if not collapsed:
            raise ValueError("title must not be blank")
        return collapsed


class ProposePlanArgs(BaseModel):
    """Argument schema for the derived `propose_plan` client tool.

    The plan is shown to the reader before any code runs, and the reader may edit it.
    Every field is bounded because the call is stored in history and rendered in the
    widget. `est_peak_mb` is only validated here; what it is compared with is the memory
    budget (plan E5.1), not this tool.
    """

    findings: str = Field(
        ...,
        max_length=MAX_PLAN_FINDINGS_CHARS,
        description="A short paragraph on what the inspection found, which the plan rests on.",
    )
    steps: list[PlanStep] = Field(
        ...,
        min_length=1,
        max_length=MAX_PLAN_STEPS,
        description="The ordered steps; each one runs as exactly one execute_code call.",
    )
    est_peak_mb: int = Field(
        ...,
        ge=0,
        le=MAX_PLAN_PEAK_MB,
        description="Estimated peak memory, in megabytes, of the plan's largest step.",
    )


_PROPOSE_PLAN_DESCRIPTION = (
    "Show the reader a plan for an analysis and wait for their decision before running "
    "any code. Call it once, after inspecting the data, with what you found, 3 to 6 "
    "steps (each will be one execute_code call) and the estimated peak memory in "
    "megabytes of the largest step. The answer tells you whether the reader approved "
    "(with the steps as they left them, which you follow as written, and whether to "
    "pause after each step or run them all) or asked for changes (revise the plan and "
    "call this again). It runs no code."
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
    return env_flag_set(CLIENT_TOOL_KILL_SWITCH_ENV)


def build_client_tools(
    config: CommunityConfig,
    declared: set[str] | None,
    tier: Tier | None = None,
) -> list[ClientTool]:
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

    `tier` is the model tier of a routed turn (`src.agents.model_routing`). A quick turn
    binds the community's client tools minus `execute_code` and `get_full_output`, which
    belong to the analysis model, plus `start_analysis`, the call that hands the
    conversation over. `start_analysis` is bound only here, never because a caller
    declared it: it is not configured and not in `declared`'s vocabulary, so no request
    can switch it on. An analysis turn, and a turn with no tier, bind exactly what they
    always did.

    Args:
        config: The community's parsed configuration.
        declared: Client tool names the caller's request body claims it can
            execute. `None` or empty means "declares nothing".
        tier: The turn's model tier, or None when the community does not route.

    Returns:
        A `ClientTool` for every entry in `config.extensions.client_tools`
        whose name is also in `declared`, in that list's order, followed by the
        derived `get_full_output` tool when a python tool was bound and the
        caller declared it, then `propose_plan` on an analysis turn under the
        same two conditions.
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
    python_bound = False
    for entry in configured:
        if entry.name not in declared:
            continue
        if tier == "quick" and entry.name == EXECUTE_CODE_TOOL_NAME:
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
        python_bound = python_bound or entry.runtime == "python"

    # Derived, not configured. Bound only beside a python tool, because without
    # one there is no browser output to read, and only when the caller declares
    # it, because an old cached widget that cannot answer it would otherwise park
    # every such call forever. No permission gate: it reads output the person's
    # own browser already holds and runs nothing.
    if python_bound and FULL_OUTPUT_TOOL_NAME in declared and tier != "quick":
        tools.append(
            ClientTool(
                name=FULL_OUTPUT_TOOL_NAME,
                description=_FULL_OUTPUT_DESCRIPTION,
                args_schema=GetFullOutputArgs,
                requires_permission=False,
            )
        )
    # Derived, like get_full_output, and only where a plan leads to code: a python tool
    # is bound, the caller declares it, and the turn is on the analysis model. Never on
    # a quick turn or a turn with no tier, which have no code to plan. No permission
    # gate here: the call runs nothing, and the reader's approval is the answer to it.
    if python_bound and PROPOSE_PLAN_TOOL_NAME in declared and tier == "analysis":
        tools.append(
            ClientTool(
                name=PROPOSE_PLAN_TOOL_NAME,
                description=_PROPOSE_PLAN_DESCRIPTION,
                args_schema=ProposePlanArgs,
                requires_permission=False,
            )
        )
    if tier == "quick":
        # Derived, like get_full_output, and for the same reason it has no permission
        # gate: it runs nothing. The graph parks the call, and the stream layer turns
        # that parked call into a handover instead of a tool_request.
        tools.append(
            ClientTool(
                name=START_ANALYSIS_TOOL_NAME,
                description=_START_ANALYSIS_DESCRIPTION,
                args_schema=StartAnalysisArgs,
                requires_permission=False,
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
