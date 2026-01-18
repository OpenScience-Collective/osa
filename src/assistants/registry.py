"""Assistant registry for OSA.

Provides both decorator-based and YAML-based registration for assistants.
Each assistant can register itself when imported (decorator), or be
loaded from a YAML configuration file.

Example (decorator):
    ```python
    from src.assistants.registry import registry

    @registry.register(
        id="hed",
        name="HED",
        description="Hierarchical Event Descriptors",
    )
    def create_hed_assistant(model, **kwargs):
        return HEDAssistant(model=model, **kwargs)
    ```

Example (YAML):
    ```python
    from src.assistants.registry import registry

    # Load communities from YAML config
    registry.load_from_yaml("registries/communities.yaml")
    ```
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from fastapi import APIRouter
    from langchain_core.language_models import BaseChatModel

    from src.core.config.community import CommunityConfig

logger = logging.getLogger(__name__)

# Type alias for assistant status
AssistantStatus = Literal["available", "beta", "coming_soon"]


@dataclass
class AssistantInfo:
    """Metadata and factory for a registered assistant.

    Contains all information needed to create and configure an assistant.
    """

    id: str
    """Unique identifier (e.g., 'hed', 'bids', 'eeglab')."""

    name: str
    """Display name (e.g., 'HED', 'BIDS', 'EEGLAB')."""

    description: str
    """Short description for discovery and help text."""

    factory: Callable[..., Any] | None = None
    """Factory function to create assistant instances.

    Signature: (model: BaseChatModel, **kwargs) -> BaseAgent
    Can be None if only config is loaded from YAML (no assistant impl yet).
    """

    status: AssistantStatus = "available"
    """Status: 'available', 'beta', or 'coming_soon'."""

    router_factory: Callable[[], "APIRouter"] | None = None
    """Optional factory for custom API router.

    Note: Routers must be manually registered in api/main.py.
    This field stores the factory for discovery; auto-mounting is not implemented.
    """

    sync_config: dict[str, Any] = field(default_factory=dict)
    """Configuration for knowledge sync.

    Expected keys:
    - github_repos: list[str] - Repos to sync (e.g., ['hed-standard/hed-specification'])
    - paper_queries: list[str] - Queries for paper search
    """

    community_config: "CommunityConfig | None" = None
    """Full community configuration from YAML.

    Contains documentation sources, GitHub repos, citations, and extension points.
    This is the rich config object; sync_config is derived from it for compatibility.
    """

    def __post_init__(self) -> None:
        """Validate required fields after initialization."""
        if not self.id or not self.id.strip():
            raise ValueError("id must be a non-empty string")
        if not self.name or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if not self.description or not self.description.strip():
            raise ValueError("description must be a non-empty string")
        # Factory can be None for YAML-only configs (no implementation yet)
        if self.factory is not None and not callable(self.factory):
            raise ValueError("factory must be callable")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "has_custom_router": self.router_factory is not None,
            "has_sync_config": bool(self.sync_config),
        }


class AssistantRegistry:
    """Global registry for OSA assistants.

    Provides decorator-based registration and factory access.
    Thread-safe for read operations after initial registration.
    """

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._assistants: dict[str, AssistantInfo] = {}

    def register(
        self,
        id: str,
        name: str,
        description: str,
        status: AssistantStatus = "available",
        router_factory: Callable[[], "APIRouter"] | None = None,
        sync_config: dict[str, Any] | None = None,
    ) -> Callable[[Callable], Callable]:
        """Decorator to register an assistant factory.

        Args:
            id: Unique identifier for the assistant (kebab-case).
            name: Display name for the assistant.
            description: Short description.
            status: Availability status.
            router_factory: Optional custom router factory.
            sync_config: Optional knowledge sync configuration.

        Returns:
            Decorator that registers the factory function.

        Example:
            ```python
            @registry.register(
                id="hed",
                name="HED",
                description="HED annotation assistant",
            )
            def create_hed_assistant(model, **kwargs):
                return HEDAssistant(model=model, **kwargs)
            ```
        """

        def decorator(factory: Callable) -> Callable:
            if id in self._assistants:
                # Merge with existing registration (likely from YAML)
                existing = self._assistants[id]
                existing.factory = factory
                existing.router_factory = router_factory
                # Prefer decorator's sync_config if provided, else keep YAML's
                if sync_config:
                    existing.sync_config = sync_config
                logger.info("Merged factory into existing registration: %s (%s)", id, name)
            else:
                self._assistants[id] = AssistantInfo(
                    id=id,
                    name=name,
                    description=description,
                    factory=factory,
                    status=status,
                    router_factory=router_factory,
                    sync_config=sync_config or {},
                )
                logger.info("Registered assistant: %s (%s)", id, name)
            return factory

        return decorator

    def get(self, id: str) -> AssistantInfo | None:
        """Get assistant info by ID.

        Args:
            id: Assistant identifier.

        Returns:
            AssistantInfo if found, None otherwise.
        """
        return self._assistants.get(id)

    def list_all(self) -> list[AssistantInfo]:
        """List all registered assistants.

        Returns:
            List of all AssistantInfo objects.
        """
        return list(self._assistants.values())

    def list_available(self) -> list[AssistantInfo]:
        """List only available assistants (status='available').

        Returns:
            List of available AssistantInfo objects.
        """
        return [a for a in self._assistants.values() if a.status == "available"]

    def create_assistant(
        self,
        id: str,
        model: "BaseChatModel",
        **kwargs: Any,
    ) -> Any:
        """Create an assistant instance.

        Args:
            id: Assistant identifier.
            model: Language model instance.
            **kwargs: Additional arguments for the factory.

        Returns:
            Configured assistant instance.

        Raises:
            ValueError: If assistant ID is not registered or cannot be created.
        """
        info = self.get(id)
        if not info:
            available = [a.id for a in self.list_all()]
            raise ValueError(f"Assistant '{id}' not registered. Available: {available}")

        if info.status == "coming_soon":
            raise ValueError(f"Assistant '{id}' is coming soon but not yet available")

        # If there's a custom factory, use it
        if info.factory is not None:
            return info.factory(model=model, **kwargs)

        # Otherwise, use CommunityAssistant if we have YAML config
        if info.community_config is not None:
            from src.assistants.community import create_community_assistant

            return create_community_assistant(
                model=model,
                config=info.community_config,
                **kwargs,
            )

        # No factory and no community config - can't create
        raise ValueError(f"Assistant '{id}' has no factory implementation and no YAML config.")

    def load_from_yaml(self, yaml_path: Path | str) -> list[str]:
        """Load community configurations from YAML file.

        This populates the registry with community metadata from YAML.
        If a community already exists (registered via decorator), the
        YAML config is merged in (community_config and sync_config updated).

        Args:
            yaml_path: Path to communities.yaml file.

        Returns:
            List of community IDs loaded from YAML.

        Raises:
            FileNotFoundError: If YAML file doesn't exist.
            ValidationError: If YAML structure is invalid.
        """
        from src.core.config.community import CommunitiesConfig

        path = Path(yaml_path)
        if not path.exists():
            logger.warning("Communities YAML not found: %s", path)
            return []

        config = CommunitiesConfig.from_yaml(path)
        loaded: list[str] = []

        for community in config.communities:
            if community.id in self._assistants:
                # Merge YAML config into existing registration
                existing = self._assistants[community.id]
                existing.community_config = community
                # Update sync_config from YAML if not already set
                if not existing.sync_config:
                    existing.sync_config = community.get_sync_config()
                logger.debug("Merged YAML config for existing assistant: %s", community.id)
            else:
                # Create new registration from YAML (no factory yet)
                self._assistants[community.id] = AssistantInfo(
                    id=community.id,
                    name=community.name,
                    description=community.description,
                    factory=None,  # No implementation yet
                    status=community.status,
                    sync_config=community.get_sync_config(),
                    community_config=community,
                )
                logger.info("Loaded community from YAML: %s (%s)", community.id, community.name)

            loaded.append(community.id)

        logger.info("Loaded %d communities from YAML: %s", len(loaded), ", ".join(loaded))
        return loaded

    def get_community_config(self, id: str) -> "CommunityConfig | None":
        """Get the full community configuration for an assistant.

        Args:
            id: Assistant/community identifier.

        Returns:
            CommunityConfig if available, None otherwise.
        """
        info = self.get(id)
        return info.community_config if info else None

    def __contains__(self, id: str) -> bool:
        """Check if assistant is registered."""
        return id in self._assistants

    def __len__(self) -> int:
        """Return number of registered assistants."""
        return len(self._assistants)


# Global registry instance
registry = AssistantRegistry()
