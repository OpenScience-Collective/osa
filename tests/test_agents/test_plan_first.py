"""When the plan-first prompt section is sent (src.agents.plan_first).

It is sent exactly when the analysis model can follow it: an analysis turn, `propose_plan`
declared, client tools not disabled, and text configured. The NEMAR text is read from the
real `config.yaml`.
"""

from pathlib import Path

import pytest

from src.agents.plan_first import plan_first_section
from src.core.config.community import (
    EXECUTE_CODE_TOOL_NAME,
    PROPOSE_PLAN_TOOL_NAME,
    ClientToolConfig,
    CommunityConfig,
    ExtensionsConfig,
    PythonRuntimeConfig,
    RuntimeConfig,
)
from src.tools.client_tools import CLIENT_TOOL_KILL_SWITCH_ENV

TEXT = "Propose a plan with propose_plan, then run one step per execute_code."
DECLARED = {EXECUTE_CODE_TOOL_NAME, PROPOSE_PLAN_TOOL_NAME}
NEMAR_YAML = Path(__file__).parents[2] / "src" / "assistants" / "nemar" / "config.yaml"


def _config(plan_first_instructions: str | None = TEXT) -> CommunityConfig:
    return CommunityConfig(
        id="planfirst",
        name="Plan First",
        description="Plans before it runs",
        extensions=ExtensionsConfig(
            client_tools=[
                ClientToolConfig(
                    name=EXECUTE_CODE_TOOL_NAME, runtime="python", description="Run code."
                )
            ]
        ),
        runtime=RuntimeConfig(
            python=PythonRuntimeConfig(pyodide_version="314.0.6", lockfile="runtime/l.json")
        ),
        quick_model="haiku",
        analysis_model="sonnet",
        plan_first_instructions=plan_first_instructions,
    )


@pytest.fixture(autouse=True)
def _kill_switch_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLIENT_TOOL_KILL_SWITCH_ENV, raising=False)


class TestPlanFirstSection:
    def test_sent_for_an_analysis_turn_that_declares_the_tool(self) -> None:
        assert plan_first_section(_config(), "analysis", DECLARED) == TEXT

    def test_absent_on_a_quick_turn(self) -> None:
        assert plan_first_section(_config(), "quick", DECLARED) is None

    def test_absent_without_a_tier(self) -> None:
        assert plan_first_section(_config(), None, DECLARED) is None

    @pytest.mark.parametrize("declared", [None, set(), {EXECUTE_CODE_TOOL_NAME}])
    def test_absent_when_the_tool_is_not_declared(self, declared: set[str] | None) -> None:
        assert plan_first_section(_config(), "analysis", declared) is None

    def test_absent_under_the_kill_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CLIENT_TOOL_KILL_SWITCH_ENV, "1")
        assert plan_first_section(_config(), "analysis", DECLARED) is None

    def test_absent_when_the_community_sets_no_text(self) -> None:
        assert plan_first_section(_config(None), "analysis", DECLARED) is None

    def test_absent_for_a_community_without_a_config(self) -> None:
        assert plan_first_section(None, "analysis", DECLARED) is None


class TestNemarPlanFirstText:
    def test_it_names_the_tools_the_model_must_use(self) -> None:
        config = CommunityConfig.from_yaml(NEMAR_YAML)
        text = plan_first_section(config, "analysis", DECLARED)

        assert text is not None
        assert PROPOSE_PLAN_TOOL_NAME in text
        assert EXECUTE_CODE_TOOL_NAME in text
        for inspection_tool in (
            "nemar_describe_dataset",
            "nemar_get_events",
            "nemar_list_recordings",
        ):
            assert inspection_tool in text
        for outcome in ("approved", "changes_requested", "pause", "run_all"):
            assert outcome in text

    def test_it_names_no_memory_number(self) -> None:
        """The budget is a separate decision (E5.1); a number here would be a second one."""
        text = CommunityConfig.from_yaml(NEMAR_YAML).plan_first_instructions

        assert text is not None
        assert "MB" not in text
        assert "GB" not in text
