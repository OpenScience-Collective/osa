"""Tests for the assistant registry.

Tests cover:
- AssistantInfo dataclass validation
- Registry registration and lookup
- YAML configuration-based registration
- Assistant creation via CommunityAssistant
- Error handling for invalid inputs
"""

from unittest.mock import MagicMock

import pytest

from src.assistants.registry import AssistantInfo, AssistantRegistry
from src.core.config.community import CommunityConfig


@pytest.fixture
def temp_registry() -> AssistantRegistry:
    """Create a fresh registry for testing."""
    return AssistantRegistry()


class TestAssistantInfo:
    """Tests for AssistantInfo dataclass validation."""

    def test_valid_assistant_info(self) -> None:
        """Should create AssistantInfo with valid inputs."""
        info = AssistantInfo(
            id="test",
            name="Test Assistant",
            description="A test assistant",
        )
        assert info.id == "test"
        assert info.name == "Test Assistant"
        assert info.status == "available"

    def test_empty_id_raises_error(self) -> None:
        """Should raise ValueError for empty id."""
        with pytest.raises(ValueError, match="id must be a non-empty string"):
            AssistantInfo(
                id="",
                name="Test",
                description="Test",
            )

    def test_whitespace_id_raises_error(self) -> None:
        """Should raise ValueError for whitespace-only id."""
        with pytest.raises(ValueError, match="id must be a non-empty string"):
            AssistantInfo(
                id="   ",
                name="Test",
                description="Test",
            )

    def test_empty_name_raises_error(self) -> None:
        """Should raise ValueError for empty name."""
        with pytest.raises(ValueError, match="name must be a non-empty string"):
            AssistantInfo(
                id="test",
                name="",
                description="Test",
            )

    def test_empty_description_raises_error(self) -> None:
        """Should raise ValueError for empty description."""
        with pytest.raises(ValueError, match="description must be a non-empty string"):
            AssistantInfo(
                id="test",
                name="Test",
                description="",
            )

    def test_to_dict(self) -> None:
        """Should convert to dictionary correctly."""
        info = AssistantInfo(
            id="test",
            name="Test",
            description="A test assistant",
            status="beta",
        )
        result = info.to_dict()
        assert result["id"] == "test"
        assert result["name"] == "Test"
        assert result["description"] == "A test assistant"
        assert result["status"] == "beta"
        assert result["has_sync_config"] is False


class TestAssistantRegistry:
    """Tests for AssistantRegistry class."""

    def test_register_from_config(self, temp_registry: AssistantRegistry) -> None:
        """Should register assistant from CommunityConfig."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test assistant",
        )

        temp_registry.register_from_config(config)

        assert "test" in temp_registry
        assert temp_registry.get("test") is not None
        assert temp_registry.get("test").name == "Test"

    def test_create_assistant_success(self, temp_registry: AssistantRegistry) -> None:
        """Should create assistant instance successfully."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test assistant",
        )
        temp_registry.register_from_config(config)

        mock_model = MagicMock()
        result = temp_registry.create_assistant("test", model=mock_model, preload_docs=False)

        # Should be a CommunityAssistant
        from src.assistants.community import CommunityAssistant

        assert isinstance(result, CommunityAssistant)

    def test_create_assistant_unregistered_raises_error(
        self, temp_registry: AssistantRegistry
    ) -> None:
        """Should raise ValueError with available assistants list for unregistered ID."""
        config = CommunityConfig(
            id="registered",
            name="Registered",
            description="...",
        )
        temp_registry.register_from_config(config)

        with pytest.raises(ValueError) as exc_info:
            temp_registry.create_assistant("nonexistent", model=MagicMock())

        assert "not registered" in str(exc_info.value)
        assert "registered" in str(exc_info.value)  # Shows available

    def test_create_assistant_coming_soon_raises_error(
        self, temp_registry: AssistantRegistry
    ) -> None:
        """Should reject assistants marked as coming_soon."""
        config = CommunityConfig(
            id="future",
            name="Future",
            description="Coming soon",
            status="coming_soon",
        )
        temp_registry.register_from_config(config)

        with pytest.raises(ValueError, match="coming soon"):
            temp_registry.create_assistant("future", model=MagicMock())

    def test_list_available_excludes_coming_soon(self, temp_registry: AssistantRegistry) -> None:
        """list_available should only return assistants with status='available'."""
        config_available = CommunityConfig(
            id="available",
            name="Available",
            description="Available assistant",
            status="available",
        )
        config_coming = CommunityConfig(
            id="coming",
            name="Coming",
            description="Coming soon",
            status="coming_soon",
        )
        config_beta = CommunityConfig(
            id="beta",
            name="Beta",
            description="Beta assistant",
            status="beta",
        )

        temp_registry.register_from_config(config_available)
        temp_registry.register_from_config(config_coming)
        temp_registry.register_from_config(config_beta)

        available = temp_registry.list_available()
        available_ids = [a.id for a in available]

        assert "available" in available_ids
        assert "coming" not in available_ids
        # Beta is not "available" status
        assert "beta" not in available_ids

    def test_list_all_includes_all_statuses(self, temp_registry: AssistantRegistry) -> None:
        """list_all should return all registered assistants."""
        config1 = CommunityConfig(
            id="one",
            name="One",
            description="...",
            status="available",
        )
        config2 = CommunityConfig(
            id="two",
            name="Two",
            description="...",
            status="coming_soon",
        )

        temp_registry.register_from_config(config1)
        temp_registry.register_from_config(config2)

        all_assistants = temp_registry.list_all()
        all_ids = [a.id for a in all_assistants]

        assert "one" in all_ids
        assert "two" in all_ids
        assert len(all_assistants) == 2

    def test_get_returns_none_for_unregistered(self, temp_registry: AssistantRegistry) -> None:
        """get() should return None for unregistered ID."""
        assert temp_registry.get("nonexistent") is None

    def test_contains(self, temp_registry: AssistantRegistry) -> None:
        """Should support 'in' operator."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="...",
        )
        temp_registry.register_from_config(config)

        assert "test" in temp_registry
        assert "nonexistent" not in temp_registry

    def test_len(self, temp_registry: AssistantRegistry) -> None:
        """Should return number of registered assistants."""
        config1 = CommunityConfig(id="one", name="One", description="...")
        config2 = CommunityConfig(id="two", name="Two", description="...")

        temp_registry.register_from_config(config1)
        temp_registry.register_from_config(config2)

        assert len(temp_registry) == 2


class TestAssistantCreation:
    """Tests for creating assistants from config."""

    def test_create_assistant_from_config(self, temp_registry: AssistantRegistry) -> None:
        """Should create CommunityAssistant from config."""
        config = CommunityConfig(
            id="yaml-test",
            name="YAML Test",
            description="A community defined in YAML",
            github={"repos": ["org/test-repo"]},
            citations={"queries": ["test query"]},
        )

        temp_registry.register_from_config(config)

        # Verify entry exists
        info = temp_registry.get("yaml-test")
        assert info is not None
        assert info.community_config is not None

        # Create mock model
        mock_model = MagicMock()

        # Create assistant - should use CommunityAssistant
        assistant = temp_registry.create_assistant(
            "yaml-test", model=mock_model, preload_docs=False
        )

        # Verify it's a CommunityAssistant
        from src.assistants.community import CommunityAssistant

        assert isinstance(assistant, CommunityAssistant)
        assert assistant.config.id == "yaml-test"
        assert assistant.config.name == "YAML Test"

    def test_create_assistant_fails_without_config(self, temp_registry: AssistantRegistry) -> None:
        """Should raise error when no community config."""
        # Manually add an entry with no community_config
        temp_registry._assistants["broken-entry"] = AssistantInfo(
            id="broken-entry",
            name="Broken",
            description="No config",
            status="available",
            community_config=None,
        )

        mock_model = MagicMock()

        with pytest.raises(ValueError, match="no community config"):
            temp_registry.create_assistant("broken-entry", model=mock_model)


class TestGetCommunityConfig:
    """Tests for get_community_config method."""

    def test_returns_config_for_registered(self, temp_registry: AssistantRegistry) -> None:
        """Should return community config for registered assistant."""
        config = CommunityConfig(
            id="config-test",
            name="Config Test",
            description="Test getting config",
        )
        temp_registry.register_from_config(config)

        result = temp_registry.get_community_config("config-test")
        assert result is not None
        assert result.id == "config-test"
        assert result.name == "Config Test"

    def test_returns_none_for_unregistered(self, temp_registry: AssistantRegistry) -> None:
        """Should return None for unregistered assistant."""
        assert temp_registry.get_community_config("nonexistent") is None


class TestCustomSystemPrompt:
    """Tests for custom system_prompt in configurations."""

    def test_custom_system_prompt_used(self, temp_registry: AssistantRegistry) -> None:
        """Should use custom system_prompt from config."""
        config = CommunityConfig(
            id="custom-prompt-test",
            name="Custom Prompt Test",
            description="Testing custom system prompts",
            system_prompt="""You are a specialized assistant for {name}.
Description: {description}
Repos: {repo_list}
DOIs: {paper_dois}""",
            github={"repos": ["org/test-repo"]},
            citations={"dois": ["10.1234/test.doi"]},
        )

        temp_registry.register_from_config(config)

        mock_model = MagicMock()
        assistant = temp_registry.create_assistant(
            "custom-prompt-test", model=mock_model, preload_docs=False
        )

        # Verify custom prompt with substitutions
        prompt = assistant.get_system_prompt()
        assert "You are a specialized assistant for Custom Prompt Test" in prompt
        assert "Description: Testing custom system prompts" in prompt
        assert "org/test-repo" in prompt
        assert "10.1234/test.doi" in prompt

    def test_default_prompt_without_custom(self, temp_registry: AssistantRegistry) -> None:
        """Should use default prompt when no custom system_prompt provided."""
        config = CommunityConfig(
            id="default-prompt-test",
            name="Default Prompt Test",
            description="Testing default prompts",
        )

        temp_registry.register_from_config(config)

        mock_model = MagicMock()
        assistant = temp_registry.create_assistant(
            "default-prompt-test", model=mock_model, preload_docs=False
        )

        # Verify default template is used
        prompt = assistant.get_system_prompt()
        assert "You are an expert assistant for Default Prompt Test" in prompt
        assert "## Your Role" in prompt  # From default template


class TestHEDIntegration:
    """Integration tests for HED assistant via registry."""

    @pytest.fixture(autouse=True)
    def setup_registry(self) -> None:
        """Ensure registry is populated before tests."""
        from src.assistants import discover_assistants, registry

        registry._assistants.clear()
        discover_assistants()

    def test_hed_registered(self) -> None:
        """HED should be registered in the registry."""
        from src.assistants import registry

        assert "hed" in registry

    def test_hed_has_community_config(self) -> None:
        """HED should have community config from YAML."""
        from src.assistants import registry

        info = registry.get("hed")
        assert info is not None
        assert info.name == "HED (Hierarchical Event Descriptors)"
        assert info.community_config is not None

    def test_hed_creates_community_assistant(self) -> None:
        """HED should create CommunityAssistant instance."""
        from src.assistants import registry
        from src.assistants.community import CommunityAssistant

        mock_model = MagicMock()
        assistant = registry.create_assistant("hed", model=mock_model, preload_docs=False)

        # Should be CommunityAssistant (not a custom HEDAssistant)
        assert isinstance(assistant, CommunityAssistant)

    def test_hed_has_custom_system_prompt(self) -> None:
        """HED should use custom system_prompt from YAML."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("hed", model=mock_model, preload_docs=False)

        prompt = assistant.get_system_prompt()

        # Should have HED-specific content
        assert "Hierarchical Event Descriptors" in prompt
        assert "hedtags.org" in prompt
        assert "validate_hed_string" in prompt

    def test_hed_has_plugin_tools(self) -> None:
        """HED should have tools loaded from Python plugin."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("hed", model=mock_model, preload_docs=False)

        # Get tool names
        tool_names = [t.name for t in assistant.tools]

        # Should have HED-specific tools from plugin
        assert "validate_hed_string" in tool_names
        assert "suggest_hed_tags" in tool_names
        assert "get_hed_schema_versions" in tool_names
        assert "retrieve_hed_docs" in tool_names

        # Should also have generic knowledge tools
        assert "search_hed_discussions" in tool_names
        assert "list_hed_recent" in tool_names
        assert "search_hed_papers" in tool_names
