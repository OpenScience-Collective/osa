"""``get_procedure`` and the "Known procedures" section, on the real NEMAR config.

The assistant is built from ``src/assistants/nemar/config.yaml`` with its MCP servers
emptied (as ``test_community.py`` does for tools that would reach the network). The tool
and the prompt section exist together or not at all: a turn that can run code (``execute_code``
is bound) gets both, and a quick turn or a widget that does not declare it gets neither.
Nothing here names NEMAR's procedures: ids, titles and documents come from the config.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.assistants.community import CommunityAssistant
from src.core.config.community import (
    EXECUTE_CODE_TOOL_NAME,
    GET_PROCEDURE_TOOL_NAME,
    PROPOSE_PLAN_TOOL_NAME,
    CommunityConfig,
    Tier,
)

NEMAR_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "assistants" / "nemar" / "config.yaml"
)
SECTION = "## Known procedures"
CODE_AND_PLAN = {EXECUTE_CODE_TOOL_NAME, PROPOSE_PLAN_TOOL_NAME}


@lru_cache(maxsize=1)
def _nemar() -> CommunityConfig:
    """The shipped config, without its MCP servers (they would reach the network)."""
    config = CommunityConfig.from_yaml(NEMAR_CONFIG_PATH)
    assert config.extensions is not None
    return config.model_copy(
        update={"extensions": config.extensions.model_copy(update={"mcp_servers": []})}
    )


def _assistant(
    declared: set[str] | None, tier: Tier | None = "analysis", config: CommunityConfig | None = None
) -> CommunityAssistant:
    return CommunityAssistant(
        model=MagicMock(),
        config=config or _nemar(),
        tier=tier,
        declared_client_tools=declared,
    )


def _tool(assistant: CommunityAssistant):
    return next((t for t in assistant.tools if t.name == GET_PROCEDURE_TOOL_NAME), None)


def _section(prompt: str) -> str:
    """The text of the section: from its heading to the next ``## `` heading or the end."""
    assert SECTION in prompt
    tail = prompt.split(SECTION, 1)[1]
    return tail.split("\n## ", 1)[0]


class TestACommunityThatCanRunCode:
    def test_the_tool_is_bound(self) -> None:
        assistant = _assistant(CODE_AND_PLAN)
        tool = _tool(assistant)
        assert tool is not None
        assert GET_PROCEDURE_TOOL_NAME not in assistant.client_tool_names

    def test_it_returns_a_procedures_document(self) -> None:
        config = _nemar()
        tool = _tool(_assistant(CODE_AND_PLAN))
        documents = config.procedure_documents()
        assert len(documents) >= 2
        for procedure_id, text in documents.items():
            assert tool.invoke({"procedure_id": procedure_id}) == text

    def test_an_unknown_id_is_a_plain_error_naming_every_known_id(self) -> None:
        tool = _tool(_assistant(CODE_AND_PLAN))
        answer = tool.invoke({"procedure_id": "no-such-procedure"})
        assert answer.startswith("Error: unknown procedure 'no-such-procedure'")
        for procedure in _nemar().procedures:
            assert f"'{procedure.id}'" in answer

    def test_an_id_with_stray_whitespace_still_finds_its_document(self) -> None:
        procedure_id = _nemar().procedures[0].id
        tool = _tool(_assistant(CODE_AND_PLAN))
        assert tool.invoke({"procedure_id": f" {procedure_id}\n"}) == (
            _nemar().procedure_documents()[procedure_id]
        )

    def test_the_prompt_lists_each_procedure_with_the_rule(self) -> None:
        prompt = _assistant(CODE_AND_PLAN).get_system_prompt()
        section = _section(prompt)
        for procedure in _nemar().procedures:
            assert f"`{procedure.id}`" in section
            assert procedure.title in section
            assert procedure.when in section
        assert f"call `{GET_PROCEDURE_TOOL_NAME}`" in section
        assert f"before `{PROPOSE_PLAN_TOOL_NAME}`" in section
        assert "as written" in section
        assert "plan-first step rules" in section

    def test_the_prompt_carries_the_list_not_the_documents(self) -> None:
        prompt = _assistant(CODE_AND_PLAN).get_system_prompt()
        for text in _nemar().procedure_documents().values():
            assert text not in prompt
            assert text.splitlines()[0] not in prompt

    def test_get_system_prompt_reads_the_stored_decision(self) -> None:
        """It rebuilds the prompt, and must come to the same answer each time."""
        assistant = _assistant(CODE_AND_PLAN)
        assert assistant.get_system_prompt() == assistant.get_system_prompt()
        assert SECTION in assistant.get_system_prompt()
        quick = _assistant({EXECUTE_CODE_TOOL_NAME}, tier="quick")
        assert SECTION not in quick.get_system_prompt()

    def test_the_section_sits_before_the_callers_additional_instructions(self) -> None:
        assistant = CommunityAssistant(
            model=MagicMock(),
            config=_nemar(),
            tier="analysis",
            declared_client_tools=CODE_AND_PLAN,
            additional_instructions="CALLER-INSTRUCTIONS-MARKER",
        )
        prompt = assistant.get_system_prompt()
        assert prompt.index(SECTION) < prompt.index("CALLER-INSTRUCTIONS-MARKER")


class TestAnAssistantWithoutAPlanTool:
    def test_the_section_says_before_running_code_and_never_names_propose_plan(self) -> None:
        assistant = _assistant({EXECUTE_CODE_TOOL_NAME}, tier="analysis")
        assert _tool(assistant) is not None
        section = _section(assistant.get_system_prompt())
        assert "before running code" in section
        assert PROPOSE_PLAN_TOOL_NAME not in section
        assert "plan-first" not in section

    def test_a_turn_with_no_tier_names_propose_plan_only_if_it_is_bound(self) -> None:
        """No tier means no routing, so ``propose_plan`` is not bound even when declared."""
        assistant = _assistant(CODE_AND_PLAN, tier=None)
        assert _tool(assistant) is not None
        assert PROPOSE_PLAN_TOOL_NAME not in _section(assistant.get_system_prompt())


class TestNoToolAndNoSection:
    def test_a_quick_turn_gets_neither(self) -> None:
        assistant = _assistant(CODE_AND_PLAN, tier="quick")
        assert EXECUTE_CODE_TOOL_NAME not in assistant.client_tool_names
        assert _tool(assistant) is None
        assert SECTION not in assistant.get_system_prompt()

    @pytest.mark.parametrize("declared", [None, set(), {PROPOSE_PLAN_TOOL_NAME}])
    def test_a_widget_that_does_not_declare_execute_code_gets_neither(
        self, declared: set[str] | None
    ) -> None:
        assistant = _assistant(declared)
        assert _tool(assistant) is None
        assert SECTION not in assistant.get_system_prompt()
        assert GET_PROCEDURE_TOOL_NAME not in assistant.get_system_prompt()


class TestAConfigWithoutTheDocuments:
    def test_building_the_assistant_raises(self) -> None:
        """Procedures declared but never loaded (a config built another way)."""
        loaded = _nemar()
        unloaded = CommunityConfig(
            id=loaded.id,
            name=loaded.name,
            description=loaded.description,
            procedures=loaded.procedures,
        )
        for tier, declared in (("analysis", CODE_AND_PLAN), ("quick", CODE_AND_PLAN), (None, None)):
            with pytest.raises(ValueError, match="documents were not loaded"):
                _assistant(declared, tier=tier, config=unloaded)  # type: ignore[arg-type]


class TestACommunityWithoutProcedures:
    def test_nothing_changes(self) -> None:
        config = CommunityConfig(id="plain", name="Plain", description="No procedures")
        assistant = _assistant(CODE_AND_PLAN, config=config)
        assert _tool(assistant) is None
        assert SECTION not in assistant.get_system_prompt()
        assert GET_PROCEDURE_TOOL_NAME not in assistant.get_system_prompt()

    def test_every_shipped_community_without_procedures_has_no_section(self) -> None:
        for config_path in sorted(NEMAR_CONFIG_PATH.parent.parent.glob("*/config.yaml")):
            config = CommunityConfig.from_yaml(config_path)
            if config.procedures:
                continue
            assert SECTION not in _assistant(CODE_AND_PLAN, config=config).get_system_prompt()
