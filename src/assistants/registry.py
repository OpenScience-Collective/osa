"""Assistant registry for OSA.

Provides YAML-based registration for assistants. Each community is registered
from its own config.yaml file, discovered by the discover_assistants() function.

Example:
    ```python
    from src.assistants import registry, discover_assistants

    # Discover and register all assistants from config.yaml files
    discover_assistants()

    # List available assistants
    for assistant in registry.list_available():
        print(f"{assistant.id}: {assistant.description}")

    # Create an assistant
    assistant = registry.create_assistant("hed", model=llm)
    ```
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from src.core.config.community import CommunityConfig

logger = logging.getLogger(__name__)

# Type alias for assistant status
AssistantStatus = Literal["available", "beta", "coming_soon"]


@dataclass
class AssistantInfo:
    """Metadata for a registered assistant.

    Contains all information needed to create and configure an assistant.
    """

    id: str
    """Unique identifier (e.g., 'hed', 'bids', 'eeglab')."""

    name: str
    """Display name (e.g., 'HED', 'BIDS', 'EEGLAB')."""

    description: str
    """Short description for discovery and help text."""

    status: AssistantStatus = "available"
    """Status: 'available', 'beta', or 'coming_soon'."""

    sync_config: dict[str, Any] = field(default_factory=dict)
    """Configuration for knowledge sync.

    Expected keys:
    - github_repos: list[str] - Repos to sync (e.g., ['hed-standard/hed-specification'])
    - paper_queries: list[str] - Queries for paper search
    - paper_dois: list[str] - DOIs for citation tracking
    """

    community_config: "CommunityConfig | None" = None
    """Full community configuration from YAML.

    Contains documentation sources, GitHub repos, citations, and extension points.
    This is the source of truth for creating assistants.
    """

    def __post_init__(self) -> None:
        """Validate required fields after initialization."""
        if not self.id or not self.id.strip():
            raise ValueError("id must be a non-empty string")
        if not self.name or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if not self.description or not self.description.strip():
            raise ValueError("description must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "has_sync_config": bool(self.sync_config),
        }


class AssistantRegistry:
    """Global registry for OSA assistants.

    Provides YAML-based registration and assistant creation.
    Thread-safe for read operations after initial registration.
    """

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._assistants: dict[str, AssistantInfo] = {}

    def register_from_config(self, config: "CommunityConfig") -> None:
        """Register an assistant from a CommunityConfig.

        This is the primary registration method, called by discover_assistants()
        for each community's config.yaml file.

        Args:
            config: Parsed CommunityConfig from a config.yaml file.
        """
        if config.id in self._assistants:
            logger.warning("Overwriting existing registration for: %s", config.id)

        self._assistants[config.id] = AssistantInfo(
            id=config.id,
            name=config.name,
            description=config.description,
            status=config.status,
            sync_config=config.get_sync_config(),
            community_config=config,
        )
        logger.debug("Registered assistant from config: %s (%s)", config.id, config.name)

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

        All assistants are created using CommunityAssistant, which uses
        the YAML config as the single source of truth.

        Args:
            id: Assistant identifier.
            model: Language model instance.
            **kwargs: Additional arguments for CommunityAssistant.
                - preload_docs: Whether to preload docs (default: True)
                - page_context: PageContext for widget embedding
                - additional_tools: Extra tools to include
                - additional_instructions: Extra text for system prompt

        Returns:
            Configured CommunityAssistant instance.

        Raises:
            ValueError: If assistant ID is not registered or cannot be created.
        """
        info = self.get(id)
        if not info:
            available = [a.id for a in self.list_all()]
            raise ValueError(f"Assistant '{id}' not registered. Available: {available}")

        if info.status == "coming_soon":
            raise ValueError(f"Assistant '{id}' is coming soon but not yet available")

        if info.community_config is None:
            raise ValueError(f"Assistant '{id}' has no community config.")

        from src.assistants.community import create_community_assistant

        return create_community_assistant(
            model=model,
            config=info.community_config,
            **kwargs,
        )

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
