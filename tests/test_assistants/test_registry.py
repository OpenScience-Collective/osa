"""Tests for the assistant registry.

Tests cover:
- AssistantInfo dataclass validation
- Registry registration and lookup
- YAML configuration loading
- Error handling for invalid inputs
"""

from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock

import pytest

from src.assistants.registry import AssistantInfo, AssistantRegistry


class TestAssistantInfo:
    """Tests for AssistantInfo dataclass validation."""

    def test_valid_assistant_info(self) -> None:
        """Should create AssistantInfo with valid inputs."""
        factory = MagicMock()
        info = AssistantInfo(
            id="test",
            name="Test Assistant",
            description="A test assistant",
            factory=factory,
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
                factory=MagicMock(),
            )

    def test_whitespace_id_raises_error(self) -> None:
        """Should raise ValueError for whitespace-only id."""
        with pytest.raises(ValueError, match="id must be a non-empty string"):
            AssistantInfo(
                id="   ",
                name="Test",
                description="Test",
                factory=MagicMock(),
            )

    def test_empty_name_raises_error(self) -> None:
        """Should raise ValueError for empty name."""
        with pytest.raises(ValueError, match="name must be a non-empty string"):
            AssistantInfo(
                id="test",
                name="",
                description="Test",
                factory=MagicMock(),
            )

    def test_empty_description_raises_error(self) -> None:
        """Should raise ValueError for empty description."""
        with pytest.raises(ValueError, match="description must be a non-empty string"):
            AssistantInfo(
                id="test",
                name="Test",
                description="",
                factory=MagicMock(),
            )

    def test_non_callable_factory_raises_error(self) -> None:
        """Should raise ValueError for non-callable factory."""
        with pytest.raises(ValueError, match="factory must be callable"):
            AssistantInfo(
                id="test",
                name="Test",
                description="Test",
                factory="not_callable",  # type: ignore
            )

    def test_to_dict(self) -> None:
        """Should convert to dictionary correctly."""
        info = AssistantInfo(
            id="test",
            name="Test",
            description="A test assistant",
            factory=MagicMock(),
            status="beta",
        )
        result = info.to_dict()
        assert result["id"] == "test"
        assert result["name"] == "Test"
        assert result["description"] == "A test assistant"
        assert result["status"] == "beta"
        assert result["has_custom_router"] is False
        assert result["has_sync_config"] is False


class TestAssistantRegistry:
    """Tests for AssistantRegistry class."""

    def test_register_decorator(self) -> None:
        """Should register assistant via decorator."""
        registry = AssistantRegistry()

        @registry.register(
            id="test",
            name="Test",
            description="Test assistant",
        )
        def create_test(_model):
            return MagicMock()

        assert "test" in registry
        assert registry.get("test") is not None
        assert registry.get("test").name == "Test"

    def test_create_assistant_success(self) -> None:
        """Should create assistant instance successfully."""
        registry = AssistantRegistry()
        mock_agent = MagicMock()

        @registry.register(
            id="test",
            name="Test",
            description="Test assistant",
        )
        def create_test(model, **kwargs):  # noqa: ARG001
            return mock_agent

        model = MagicMock()
        result = registry.create_assistant("test", model=model)
        assert result is mock_agent

    def test_create_assistant_unregistered_raises_error(self) -> None:
        """Should raise ValueError with available assistants list for unregistered ID."""
        registry = AssistantRegistry()

        @registry.register(id="registered", name="Registered", description="...")
        def create_registered(_model):
            return MagicMock()

        with pytest.raises(ValueError) as exc_info:
            registry.create_assistant("nonexistent", model=MagicMock())

        assert "not registered" in str(exc_info.value)
        assert "registered" in str(exc_info.value)  # Shows available

    def test_create_assistant_coming_soon_raises_error(self) -> None:
        """Should reject assistants marked as coming_soon."""
        registry = AssistantRegistry()

        @registry.register(
            id="future",
            name="Future",
            description="Coming soon",
            status="coming_soon",
        )
        def create_future(_model):
            return MagicMock()

        with pytest.raises(ValueError, match="coming soon"):
            registry.create_assistant("future", model=MagicMock())

    def test_list_available_excludes_coming_soon(self) -> None:
        """list_available should only return assistants with status='available'."""
        registry = AssistantRegistry()

        @registry.register(
            id="available",
            name="Available",
            description="Available assistant",
            status="available",
        )
        def create_available(_model):
            return MagicMock()

        @registry.register(
            id="coming",
            name="Coming",
            description="Coming soon",
            status="coming_soon",
        )
        def create_coming(_model):
            return MagicMock()

        @registry.register(
            id="beta",
            name="Beta",
            description="Beta assistant",
            status="beta",
        )
        def create_beta(_model):
            return MagicMock()

        available = registry.list_available()
        available_ids = [a.id for a in available]

        assert "available" in available_ids
        assert "coming" not in available_ids
        # Beta is not "available" status
        assert "beta" not in available_ids

    def test_list_all_includes_all_statuses(self) -> None:
        """list_all should return all registered assistants."""
        registry = AssistantRegistry()

        @registry.register(id="one", name="One", description="...", status="available")
        def create_one(_model):
            return MagicMock()

        @registry.register(id="two", name="Two", description="...", status="coming_soon")
        def create_two(_model):
            return MagicMock()

        all_assistants = registry.list_all()
        all_ids = [a.id for a in all_assistants]

        assert "one" in all_ids
        assert "two" in all_ids
        assert len(all_assistants) == 2

    def test_get_returns_none_for_unregistered(self) -> None:
        """get() should return None for unregistered ID."""
        registry = AssistantRegistry()
        assert registry.get("nonexistent") is None

    def test_contains(self) -> None:
        """Should support 'in' operator."""
        registry = AssistantRegistry()

        @registry.register(id="test", name="Test", description="...")
        def create_test(_model):
            return MagicMock()

        assert "test" in registry
        assert "nonexistent" not in registry

    def test_len(self) -> None:
        """Should return number of registered assistants."""
        registry = AssistantRegistry()

        @registry.register(id="one", name="One", description="...")
        def create_one(_model):
            return MagicMock()

        @registry.register(id="two", name="Two", description="...")
        def create_two(_model):
            return MagicMock()

        assert len(registry) == 2


class TestAssistantInfoWithNoneFactory:
    """Tests for AssistantInfo with None factory (YAML-only configs)."""

    def test_none_factory_allowed(self) -> None:
        """Should allow None factory for YAML-only configurations."""
        info = AssistantInfo(
            id="test",
            name="Test Assistant",
            description="A test assistant",
            factory=None,
        )
        assert info.factory is None

    def test_create_assistant_with_none_factory_raises_error(self) -> None:
        """Should raise ValueError when creating assistant with no factory."""
        registry = AssistantRegistry()
        registry._assistants["test"] = AssistantInfo(
            id="test",
            name="Test",
            description="Test",
            factory=None,
        )

        with pytest.raises(ValueError, match="has no factory implementation"):
            registry.create_assistant("test", model=MagicMock())


class TestYAMLLoading:
    """Tests for YAML configuration loading."""

    def test_load_from_yaml_creates_registrations(self) -> None:
        """Should create AssistantInfo from YAML config."""
        yaml_content = """
communities:
  - id: test-community
    name: Test Community
    description: A test community
    status: available
    github:
      repos:
        - test-org/test-repo
    citations:
      queries:
        - "test query"
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            registry = AssistantRegistry()
            loaded = registry.load_from_yaml(yaml_path)

            assert "test-community" in loaded
            assert "test-community" in registry

            info = registry.get("test-community")
            assert info is not None
            assert info.name == "Test Community"
            assert info.description == "A test community"
            assert info.factory is None  # No Python implementation
            assert info.community_config is not None
            assert info.sync_config["github_repos"] == ["test-org/test-repo"]
        finally:
            yaml_path.unlink()

    def test_load_from_yaml_missing_file_returns_empty(self) -> None:
        """Should return empty list for missing YAML file."""
        registry = AssistantRegistry()
        loaded = registry.load_from_yaml("/nonexistent/path.yaml")
        assert loaded == []

    def test_load_from_yaml_merges_with_existing(self) -> None:
        """Should merge YAML config into existing decorator registration."""
        yaml_content = """
communities:
  - id: existing
    name: Existing Community
    description: From YAML
    github:
      repos:
        - yaml-org/yaml-repo
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            registry = AssistantRegistry()

            # First register via decorator
            @registry.register(
                id="existing",
                name="Existing",
                description="From decorator",
            )
            def create_existing(_model):
                return MagicMock()

            # Then load YAML (should merge)
            registry.load_from_yaml(yaml_path)

            info = registry.get("existing")
            assert info is not None
            # Factory from decorator is preserved
            assert info.factory is not None
            # Community config from YAML is added
            assert info.community_config is not None
            assert info.sync_config["github_repos"] == ["yaml-org/yaml-repo"]
        finally:
            yaml_path.unlink()

    def test_decorator_merges_with_yaml(self) -> None:
        """Decorator should merge with pre-existing YAML registration."""
        yaml_content = """
communities:
  - id: yaml-first
    name: YAML First
    description: From YAML
    github:
      repos:
        - yaml-org/yaml-repo
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            registry = AssistantRegistry()

            # First load YAML
            registry.load_from_yaml(yaml_path)
            info_before = registry.get("yaml-first")
            assert info_before is not None
            assert info_before.factory is None

            # Then register via decorator
            @registry.register(
                id="yaml-first",
                name="YAML First",
                description="From decorator",
            )
            def create_yaml_first(_model):
                return MagicMock()

            info_after = registry.get("yaml-first")
            assert info_after is not None
            # Factory now set from decorator
            assert info_after.factory is not None
            # Community config from YAML is preserved
            assert info_after.community_config is not None
        finally:
            yaml_path.unlink()

    def test_get_community_config(self) -> None:
        """Should return community config for registered assistant."""
        yaml_content = """
communities:
  - id: config-test
    name: Config Test
    description: Test getting config
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            registry = AssistantRegistry()
            registry.load_from_yaml(yaml_path)

            config = registry.get_community_config("config-test")
            assert config is not None
            assert config.id == "config-test"
            assert config.name == "Config Test"

            # Non-existent returns None
            assert registry.get_community_config("nonexistent") is None
        finally:
            yaml_path.unlink()
