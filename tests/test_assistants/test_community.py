"""Tests for the generic CommunityAssistant class."""

from unittest.mock import MagicMock

import pytest

from src.assistants.community import (
    COMMUNITY_SYSTEM_PROMPT_TEMPLATE,
    CommunityAssistant,
    create_community_assistant,
)
from src.core.config.community import (
    CitationConfig,
    CommunityConfig,
    GitHubConfig,
)


class TestCommunityAssistant:
    """Tests for CommunityAssistant class."""

    @pytest.fixture
    def mock_model(self) -> MagicMock:
        """Create a mock language model."""
        return MagicMock()

    @pytest.fixture
    def minimal_config(self) -> CommunityConfig:
        """Create minimal community config."""
        return CommunityConfig(
            id="test-community",
            name="Test Community",
            description="A test community for unit tests",
        )

    @pytest.fixture
    def full_config(self) -> CommunityConfig:
        """Create full community config with all options."""
        return CommunityConfig(
            id="full-test",
            name="Full Test Community",
            description="A fully configured test community",
            github=GitHubConfig(
                repos=["org/repo1", "org/repo2"],
            ),
            citations=CitationConfig(
                queries=["test query"],
                dois=["10.1234/test"],
            ),
        )

    def test_creates_assistant_with_minimal_config(
        self, mock_model: MagicMock, minimal_config: CommunityConfig
    ) -> None:
        """Should create assistant with minimal configuration."""
        assistant = CommunityAssistant(model=mock_model, config=minimal_config)

        assert assistant.config == minimal_config
        assert "Test Community" in assistant.get_system_prompt()

    def test_creates_assistant_with_full_config(
        self, mock_model: MagicMock, full_config: CommunityConfig
    ) -> None:
        """Should create assistant with full configuration."""
        assistant = CommunityAssistant(model=mock_model, config=full_config)

        assert assistant.config == full_config
        # Should have knowledge tools
        assert len(assistant.tools) > 0

    def test_system_prompt_includes_name_and_description(
        self, mock_model: MagicMock, minimal_config: CommunityConfig
    ) -> None:
        """System prompt should include community name and description."""
        assistant = CommunityAssistant(model=mock_model, config=minimal_config)
        prompt = assistant.get_system_prompt()

        assert "Test Community" in prompt
        assert "A test community for unit tests" in prompt

    def test_system_prompt_includes_additional_instructions(
        self, mock_model: MagicMock, minimal_config: CommunityConfig
    ) -> None:
        """System prompt should include additional instructions if provided."""
        additional = "Always use metric units."
        assistant = CommunityAssistant(
            model=mock_model,
            config=minimal_config,
            additional_instructions=additional,
        )
        prompt = assistant.get_system_prompt()

        assert additional in prompt

    def test_creates_github_tools_when_repos_configured(
        self, mock_model: MagicMock, full_config: CommunityConfig
    ) -> None:
        """Should create GitHub search tools when repos are configured."""
        assistant = CommunityAssistant(model=mock_model, config=full_config)

        tool_names = [t.name for t in assistant.tools]
        assert "search_full-test_discussions" in tool_names
        assert "list_full-test_recent" in tool_names

    def test_creates_papers_tool_when_citations_configured(
        self, mock_model: MagicMock, full_config: CommunityConfig
    ) -> None:
        """Should create papers search tool when citations are configured."""
        assistant = CommunityAssistant(model=mock_model, config=full_config)

        tool_names = [t.name for t in assistant.tools]
        assert "search_full-test_papers" in tool_names

    def test_no_github_tools_without_repos(
        self, mock_model: MagicMock, minimal_config: CommunityConfig
    ) -> None:
        """Should not create GitHub tools when no repos configured."""
        assistant = CommunityAssistant(model=mock_model, config=minimal_config)

        tool_names = [t.name for t in assistant.tools]
        assert not any("discussions" in name for name in tool_names)
        assert not any("recent" in name for name in tool_names)

    def test_no_papers_tool_without_citations(
        self, mock_model: MagicMock, minimal_config: CommunityConfig
    ) -> None:
        """Should not create papers tool when no citations configured."""
        assistant = CommunityAssistant(model=mock_model, config=minimal_config)

        tool_names = [t.name for t in assistant.tools]
        assert not any("papers" in name for name in tool_names)

    def test_accepts_additional_tools(
        self, mock_model: MagicMock, minimal_config: CommunityConfig
    ) -> None:
        """Should include additional tools if provided."""
        mock_tool = MagicMock()
        mock_tool.name = "custom_tool"

        assistant = CommunityAssistant(
            model=mock_model,
            config=minimal_config,
            additional_tools=[mock_tool],
        )

        assert mock_tool in assistant.tools


class TestCreateCommunityAssistant:
    """Tests for create_community_assistant factory function."""

    def test_creates_assistant(self) -> None:
        """Should create CommunityAssistant instance."""
        mock_model = MagicMock()
        config = CommunityConfig(
            id="factory-test",
            name="Factory Test",
            description="Test description",
        )

        assistant = create_community_assistant(model=mock_model, config=config)

        assert isinstance(assistant, CommunityAssistant)
        assert assistant.config == config

    def test_passes_kwargs_to_assistant(self) -> None:
        """Should pass kwargs to CommunityAssistant."""
        mock_model = MagicMock()
        config = CommunityConfig(
            id="kwargs-test",
            name="Kwargs Test",
            description="Test description",
        )

        assistant = create_community_assistant(
            model=mock_model,
            config=config,
            additional_instructions="Extra info",
        )

        assert "Extra info" in assistant.get_system_prompt()


class TestSystemPromptTemplate:
    """Tests for the system prompt template."""

    def test_template_has_required_placeholders(self) -> None:
        """Template should have all required placeholders."""
        assert "{name}" in COMMUNITY_SYSTEM_PROMPT_TEMPLATE
        assert "{description}" in COMMUNITY_SYSTEM_PROMPT_TEMPLATE
        assert "{additional_instructions}" in COMMUNITY_SYSTEM_PROMPT_TEMPLATE

    def test_template_includes_guidelines(self) -> None:
        """Template should include important guidelines."""
        assert "Discovery, Not Authority" in COMMUNITY_SYSTEM_PROMPT_TEMPLATE
        assert "Be Helpful" in COMMUNITY_SYSTEM_PROMPT_TEMPLATE
        assert "Cite Sources" in COMMUNITY_SYSTEM_PROMPT_TEMPLATE
