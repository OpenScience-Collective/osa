"""Tests for client-executed tool plumbing (src.tools.client_tools).

Phase 1 (epic #429, issue #430) teaches the server to bind a tool it never
executes itself. These tests cover the tool class's refusal to run, the
kill switch, and `build_client_tools`' configured-AND-declared intersection.
The graph routing that parks a client tool's call instead of invoking it is
covered separately in tests/test_agents/test_base.py.
"""

import pytest
from pydantic import ValidationError

from src.core.config.community import (
    FULL_OUTPUT_TOOL_NAME,
    ClientToolConfig,
    CommunityConfig,
    ExtensionsConfig,
    PythonRuntimeConfig,
    RuntimeConfig,
    RuntimeLimits,
)
from src.tools.client_tools import (
    CLIENT_TOOL_KILL_SWITCH_ENV,
    ClientTool,
    ClientToolNotExecutableError,
    ExecuteCodeArgs,
    GetFullOutputArgs,
    _args_schema_for_runtime,
    build_client_tools,
    client_tools_disabled,
)


def _community_with_client_tools(
    *,
    names: list[str] | None = None,
    requires_permission: bool = True,
) -> CommunityConfig:
    """A community configured with one or more python-runtime client tools."""
    tool_names = names if names is not None else ["execute_code"]
    return CommunityConfig(
        id="test",
        name="Test",
        description="Test",
        extensions=ExtensionsConfig(
            client_tools=[
                ClientToolConfig(
                    name=name,
                    runtime="python",
                    description=f"Run {name}.",
                    requires_permission=requires_permission,
                )
                for name in tool_names
            ]
        ),
        runtime=RuntimeConfig(
            python=PythonRuntimeConfig(
                pyodide_version="0.28.3",
                lockfile="pyodide-lock-2026-01.json",
                limits=RuntimeLimits(),
            )
        ),
    )


class TestClientTool:
    """Tests for the ClientTool BaseTool subclass."""

    def test_run_raises_not_executable(self) -> None:
        """_run must never actually execute anything."""
        tool = ClientTool(name="execute_code", description="Run code.", args_schema=ExecuteCodeArgs)
        with pytest.raises(ClientToolNotExecutableError):
            tool._run(code="1+1", description="add")

    async def test_arun_raises_not_executable(self) -> None:
        """_arun must never actually execute anything."""
        tool = ClientTool(name="execute_code", description="Run code.", args_schema=ExecuteCodeArgs)
        with pytest.raises(ClientToolNotExecutableError):
            await tool._arun(code="1+1", description="add")

    def test_invoke_through_public_api_also_raises(self) -> None:
        """LangChain's own .invoke() entry point must not swallow this."""
        tool = ClientTool(name="execute_code", description="Run code.", args_schema=ExecuteCodeArgs)
        with pytest.raises(ClientToolNotExecutableError):
            tool.invoke({"code": "1+1", "description": "add"})

    def test_defaults_requires_permission_true(self) -> None:
        """requires_permission should default to True (safe default)."""
        tool = ClientTool(name="execute_code", description="Run code.", args_schema=ExecuteCodeArgs)
        assert tool.requires_permission is True

    def test_requires_permission_can_be_false(self) -> None:
        """requires_permission should be settable to False."""
        tool = ClientTool(
            name="execute_code",
            description="Run code.",
            args_schema=ExecuteCodeArgs,
            requires_permission=False,
        )
        assert tool.requires_permission is False

    def test_has_the_configured_name_and_description(self) -> None:
        """The tool the model sees carries the config's name/description verbatim."""
        tool = ClientTool(
            name="execute_code",
            description="Run Python in the browser.",
            args_schema=ExecuteCodeArgs,
        )
        assert tool.name == "execute_code"
        assert tool.description == "Run Python in the browser."
        assert tool.args_schema is ExecuteCodeArgs


class TestArgsSchemaForRuntime:
    """Tests for _args_schema_for_runtime."""

    def test_python_runtime_returns_execute_code_args(self) -> None:
        """The only phase 1 runtime should resolve to ExecuteCodeArgs."""
        assert _args_schema_for_runtime("python") is ExecuteCodeArgs

    def test_unknown_runtime_raises_clear_error(self) -> None:
        """An unmapped runtime should fail loudly, not bind an unvalidated tool."""
        with pytest.raises(ValueError, match="Unknown client tool runtime"):
            _args_schema_for_runtime("javascript")


class TestExecuteCodeArgs:
    """Tests for the shared runtime: python args schema."""

    def test_code_is_required(self) -> None:
        with pytest.raises(ValidationError, match="code"):
            ExecuteCodeArgs(description="Adds one and one.")  # type: ignore[call-arg]

    def test_description_is_required(self) -> None:
        with pytest.raises(ValidationError, match="description"):
            ExecuteCodeArgs(code="1 + 1")  # type: ignore[call-arg]

    def test_accepts_both_fields(self) -> None:
        args = ExecuteCodeArgs(code="1 + 1", description="Adds one and one.")
        assert args.code == "1 + 1"
        assert args.description == "Adds one and one."


class TestClientToolsDisabled:
    """Tests for client_tools_disabled / CLIENT_TOOL_KILL_SWITCH_ENV."""

    def test_unset_is_not_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        assert client_tools_disabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on", " on "])
    def test_truthy_values_disable(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(CLIENT_TOOL_KILL_SWITCH_ENV, value)
        assert client_tools_disabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsy_values_do_not_disable(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(CLIENT_TOOL_KILL_SWITCH_ENV, value)
        assert client_tools_disabled() is False


class TestBuildClientTools:
    """Tests for build_client_tools' configured-AND-declared intersection."""

    def test_kill_switch_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even a fully matching request yields nothing once the switch is set."""
        monkeypatch.setenv(CLIENT_TOOL_KILL_SWITCH_ENV, "1")
        config = _community_with_client_tools()
        assert build_client_tools(config, {"execute_code"}) == []

    def test_nothing_configured_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        config = CommunityConfig(id="test", name="Test", description="Test")
        assert build_client_tools(config, {"execute_code"}) == []

    def test_declared_none_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        config = _community_with_client_tools()
        assert build_client_tools(config, None) == []

    def test_declared_empty_set_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        config = _community_with_client_tools()
        assert build_client_tools(config, set()) == []

    def test_declared_disjoint_from_configured_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        config = _community_with_client_tools()
        assert build_client_tools(config, {"some_other_tool"}) == []

    def test_returns_exactly_the_intersection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Configured has two tools, declared has one overlap: only it is bound."""
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        config = _community_with_client_tools(names=["execute_code", "other_tool"])
        tools = build_client_tools(config, {"execute_code", "not_configured"})
        assert len(tools) == 1
        assert tools[0].name == "execute_code"
        assert isinstance(tools[0], ClientTool)
        assert tools[0].args_schema is ExecuteCodeArgs

    def test_full_overlap_returns_everything_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        config = _community_with_client_tools(names=["execute_code", "other_tool"])
        tools = build_client_tools(config, {"execute_code", "other_tool"})
        assert {t.name for t in tools} == {"execute_code", "other_tool"}

    def test_requires_permission_is_carried_through_from_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        config = _community_with_client_tools(requires_permission=False)
        tools = build_client_tools(config, {"execute_code"})
        assert tools[0].requires_permission is False

    def test_description_is_carried_through_from_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        config = _community_with_client_tools(names=["execute_code"])
        tools = build_client_tools(config, {"execute_code"})
        assert tools[0].description == "Run execute_code."


class TestGetFullOutput:
    """The derived tool that reads back output a browser run kept locally.

    It is bound, never configured, so the conditions under which it appears are the
    whole contract: beside a python tool, and only for a caller that declared it.
    """

    def test_bound_beside_a_python_tool_when_declared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        tools = build_client_tools(
            _community_with_client_tools(), {"execute_code", FULL_OUTPUT_TOOL_NAME}
        )
        assert [t.name for t in tools] == ["execute_code", FULL_OUTPUT_TOOL_NAME]

    def test_not_bound_for_a_caller_that_did_not_declare_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An older widget can run execute_code but cannot answer get_full_output.

        Binding it anyway would let the model call it, park the call, and leave the
        reply hanging on a browser that will never respond.
        """
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        tools = build_client_tools(_community_with_client_tools(), {"execute_code"})
        assert FULL_OUTPUT_TOOL_NAME not in [t.name for t in tools]

    def test_not_bound_without_a_python_tool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With nothing bound that runs code, there is no browser output to read."""
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        tools = build_client_tools(_community_with_client_tools(), {FULL_OUTPUT_TOOL_NAME})
        assert tools == []

    def test_the_kill_switch_strips_it_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CLIENT_TOOL_KILL_SWITCH_ENV, "1")
        tools = build_client_tools(
            _community_with_client_tools(), {"execute_code", FULL_OUTPUT_TOOL_NAME}
        )
        assert tools == []

    def test_it_needs_no_permission_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """It reads output the person's own browser already holds and runs nothing.

        Asserted against a community whose execute_code DOES require permission, so
        the derived tool is not simply inheriting a False from the config.
        """
        monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)
        tools = build_client_tools(
            _community_with_client_tools(requires_permission=True),
            {"execute_code", FULL_OUTPUT_TOOL_NAME},
        )
        by_name = {t.name: t for t in tools}
        assert by_name["execute_code"].requires_permission is True
        assert by_name[FULL_OUTPUT_TOOL_NAME].requires_permission is False
        assert by_name[FULL_OUTPUT_TOOL_NAME].args_schema is GetFullOutputArgs

    def test_a_community_cannot_configure_the_reserved_name(self) -> None:
        """A configured tool under this name would sit beside the derived one, and the
        model would see two tools called get_full_output with different arguments."""
        with pytest.raises(ValidationError, match="reserved"):
            ClientToolConfig(
                name=FULL_OUTPUT_TOOL_NAME,
                runtime="python",
                description="Shadow the derived tool.",
            )


class TestGetFullOutputArgs:
    """The schema is what the provider validates the model's call against."""

    def test_defaults_to_the_first_page_of_stdout(self) -> None:
        args = GetFullOutputArgs(call_id="toolu_01abc")
        assert (args.stream, args.offset) == ("stdout", 0)

    def test_accepts_every_stream_the_browser_serves(self) -> None:
        for stream in ("stdout", "stderr", "traceback", "figures"):
            assert GetFullOutputArgs(call_id="toolu_01abc", stream=stream).stream == stream

    @pytest.mark.parametrize(
        "fields",
        [
            {"call_id": ""},
            {"call_id": "x" * 257},
            {"call_id": "toolu_01abc", "stream": "everything"},
            {"call_id": "toolu_01abc", "offset": -1},
        ],
    )
    def test_rejects_what_the_browser_cannot_serve(self, fields: dict) -> None:
        with pytest.raises(ValidationError):
            GetFullOutputArgs(**fields)
