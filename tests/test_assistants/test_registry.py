"""Tests for the assistant registry.

Tests cover:
- AssistantInfo dataclass validation
- Registry registration and lookup
- Error handling for invalid inputs
"""

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
