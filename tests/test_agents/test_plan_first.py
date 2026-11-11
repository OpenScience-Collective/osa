"""When the plan-first prompt section is sent (src.agents.plan_first).

It is sent exactly when the analysis model can follow it: an analysis turn, `propose_plan`
declared, client tools not disabled, and text configured. The NEMAR text is read from the
real `config.yaml`.
"""

import re
from pathlib import Path

import pytest

from scripts import plan_consistency as pc
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
# The example step titles in NEMAR's text and the operation each must normalize to in
# scripts/plan_consistency.py, the check that measures whether plans agree (#714).
EXAMPLE_TITLE_OPERATIONS = {
    "Read the recording": "load",
    "Inspect the event names": "load",
    "Filter and clean the recording": "filter",
    "Epoch around the events": "epoch",
    "Compute the band power": "spectrum",
    "Plot the result": "plot",
}


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
    @pytest.fixture
    def flat_nemar_text(self) -> str:
        """The NEMAR plan-first section as the prompt gets it, whitespace collapsed."""
        config = CommunityConfig.from_yaml(NEMAR_YAML)
        text = plan_first_section(config, "analysis", DECLARED)

        assert text is not None
        return " ".join(text.split())

    def test_it_names_the_tools_the_model_must_use(self) -> None:
        config = CommunityConfig.from_yaml(NEMAR_YAML)
        text = plan_first_section(config, "analysis", DECLARED)

        assert text is not None
        assert PROPOSE_PLAN_TOOL_NAME in text
        assert EXECUTE_CODE_TOOL_NAME in text
        # The tools live on NEMAR's MCP server, so there is no listing to read here. The
        # system prompt's tool ladder is the list `test_nemar_mcp_wiring` ties, against
        # the live server, to the tools that exist; every tool this text names must be on it.
        named = set(re.findall(r"nemar_[a-z_]+", text))
        assert named, "the text names no inspection tool"
        assert named <= set(re.findall(r"nemar_[a-z_]+", config.system_prompt or ""))
        for outcome in ("approved", "changes_requested", "pause", "run_all"):
            assert outcome in text

    def test_it_names_no_memory_number(self) -> None:
        """The reader's budget is the one figure (E5.1), stated by the request's own section."""
        text = CommunityConfig.from_yaml(NEMAR_YAML).plan_first_instructions

        assert text is not None
        assert "MB" not in text
        assert "GB" not in text

    def test_the_estimate_must_fit_the_readers_budget(self) -> None:
        """est_peak_mb is held to the budget the prompt states, and to nothing else."""
        text = CommunityConfig.from_yaml(NEMAR_YAML).plan_first_instructions

        assert text is not None
        flat = " ".join(text.split())
        assert "`est_peak_mb` must not exceed the reader's browser memory budget" in flat
        # The heading it points at is the one the prompt renders for a request with a budget.
        assert "Browser memory budget" in flat

    def test_it_says_how_to_cut_a_plan_into_steps(self, flat_nemar_text: str) -> None:
        """One step per kind of work, in a fixed order, so a request gets the same steps (#714)."""
        flat = flat_nemar_text
        assert "Use one step per kind, in this order" in flat
        assert "read the recording; inspect; preprocess; epoch; compute; plot" in flat
        assert "leave out every kind the request does not need" in flat
        assert (
            "Inspect is a step only when the request depends on something you cannot know yet, "
            "such as channel or event names the `nemar_*` calls in item 1 did not return; "
            "otherwise there is no inspection step." in flat
        )
        assert (
            "Preprocess is ONE step for all filtering, re-referencing and artifact rejection."
            in flat
        )
        assert "Never fold plotting into computing" in flat
        assert (
            "never fold preparation (read, preprocess, epoch) into the step that follows it" in flat
        )

    def test_a_kind_is_split_only_to_fit_the_memory_budget(self, flat_nemar_text: str) -> None:
        """The never-split rule defers to item 4, so the two do not contradict (#714 review)."""
        flat = flat_nemar_text
        assert (
            "Never split one kind of work across steps, except as item 4 requires to fit the "
            "memory budget." in flat
        )
        # The exception points at a rule that exists and says what it requires.
        assert "split it into steps that read the data in pieces, so each fits" in flat

    def test_the_text_quotes_exactly_the_example_titles(self, flat_nemar_text: str) -> None:
        """The titles quoted in the cutting block are the ones the normalizer test covers."""
        flat = flat_nemar_text
        block = flat[flat.index("name each step for what it actually does:") :]
        block = block[: block.index("4. **Call")]
        assert re.findall(r'"([^"]+)"', block) == list(EXAMPLE_TITLE_OPERATIONS)

    @pytest.mark.parametrize(("title", "operation"), EXAMPLE_TITLE_OPERATIONS.items())
    def test_each_example_title_normalizes_to_its_operation(self, title, operation) -> None:
        assert pc.normalize_step({"title": title, "detail": ""}) == operation

    def test_the_preprocess_title_is_pinned(self, flat_nemar_text: str) -> None:
        """'Filter and re-reference...' would normalize to rereference, so the text pins the title."""
        assert (
            'Whatever it contains, title it "Filter and clean the recording" and put the '
            "specifics (re-referencing, rejection) in its `detail`." in flat_nemar_text
        )

    def test_a_short_request_keeps_its_own_kinds_and_is_not_padded_to_three(
        self, flat_nemar_text: str
    ) -> None:
        assert (
            "The 3 to 6 steps of item 4 is applied after the kinds are chosen: a request that "
            "needs only two kinds, such as read and plot, gets two steps, and you never split "
            "a kind of work to reach 3." in flat_nemar_text
        )

    def test_the_three_to_six_steps_rule_is_kept(self, flat_nemar_text: str) -> None:
        assert "as `findings`, 3 to 6 steps, each a short `title` and a `detail`" in flat_nemar_text

    def test_the_earlier_rules_survive_the_renumbering(self, flat_nemar_text: str) -> None:
        flat = flat_nemar_text
        assert "you may fix it once and run it again" in flat
        assert "Never run code that is not a step of the approved plan." in flat
        assert "Calling it ends your turn; write nothing after the call." in flat
