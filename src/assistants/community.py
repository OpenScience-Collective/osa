"""Generic CommunityAssistant for YAML-configured communities.

This module provides a generic assistant that can be created from YAML
configuration alone, without requiring custom Python code.

For communities that need specialized tools (like HED's validation),
Python plugins can be loaded via the extensions configuration.
"""

import importlib
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool

from src.agents.base import ToolAgent
from src.core.config.community import CommunityConfig
from src.tools.knowledge import create_knowledge_tools

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


# Default system prompt template for generic communities
COMMUNITY_SYSTEM_PROMPT_TEMPLATE = """You are an expert assistant for {name}.

{description}

## Your Role

You help users understand and work with {name} by:
1. Answering questions about concepts, best practices, and usage
2. Providing guidance based on official documentation
3. Linking to relevant GitHub discussions and academic papers for further reading

## Important Guidelines

**Documentation First**: Always base your answers on official documentation when available.
Use the documentation retrieval tool to fetch relevant content before answering.

**Discovery, Not Authority**: When referencing GitHub discussions or papers:
- Present them as "related resources" or "further reading"
- Say: "There's a related discussion, see: [link]"
- Do NOT use discussion content to formulate authoritative answers

**Be Precise**: If you're unsure about something, say so. It's better to acknowledge
uncertainty than to provide incorrect information.

**Cite Sources**: When referencing documentation, include links so users can verify
and explore further.

{additional_instructions}
"""


class CommunityAssistant(ToolAgent):
    """Generic assistant for any YAML-configured community.

    This assistant provides standard functionality for any community:
    - Documentation retrieval (if configured)
    - GitHub discussion search (if repos configured)
    - Paper search (if citations configured)
    - Python plugin tools (if extensions configured)

    Args:
        model: The language model to use.
        config: Community configuration from YAML.
        additional_tools: Extra tools to include beyond auto-generated ones.
        additional_instructions: Extra text to add to the system prompt.
    """

    def __init__(
        self,
        model: "BaseChatModel",
        config: CommunityConfig,
        additional_tools: list[BaseTool] | None = None,
        additional_instructions: str = "",
    ) -> None:
        """Initialize the community assistant."""
        self.config = config
        self.additional_instructions = additional_instructions

        # Build tools from config
        tools = self._build_tools(config)

        # Add any additional tools
        if additional_tools:
            tools.extend(additional_tools)

        # Load plugin tools from extensions
        plugin_tools = self._load_plugin_tools(config)
        tools.extend(plugin_tools)

        # Generate system prompt
        system_prompt = self._build_system_prompt(config, additional_instructions)

        super().__init__(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
        )

    def _build_tools(self, config: CommunityConfig) -> list[BaseTool]:
        """Build standard tools based on community configuration."""
        tools: list[BaseTool] = []

        # Get repos from GitHub config if available
        repos = config.github.repos if config.github else None

        # Determine what tools to include based on config
        has_github = config.github and config.github.repos
        has_citations = config.citations and (config.citations.queries or config.citations.dois)

        # Create knowledge tools
        knowledge_tools = create_knowledge_tools(
            community_id=config.id,
            community_name=config.name,
            repos=repos,
            include_discussions=bool(has_github),
            include_recent=bool(has_github),
            include_papers=bool(has_citations),
        )
        tools.extend(knowledge_tools)

        return tools

    def _load_plugin_tools(self, config: CommunityConfig) -> list[BaseTool]:
        """Load tools from Python plugin extensions."""
        tools: list[BaseTool] = []

        if not config.extensions or not config.extensions.python_plugins:
            return tools

        for plugin in config.extensions.python_plugins:
            try:
                module = importlib.import_module(plugin.module)

                # If specific tools are listed, load only those
                if plugin.tools:
                    for tool_name in plugin.tools:
                        if hasattr(module, tool_name):
                            tool_obj = getattr(module, tool_name)
                            if isinstance(tool_obj, BaseTool):
                                tools.append(tool_obj)
                            elif callable(tool_obj):
                                # It might be a tool-decorated function
                                tools.append(tool_obj)
                            else:
                                logger.warning(
                                    "Plugin %s.%s is not a valid tool",
                                    plugin.module,
                                    tool_name,
                                )
                        else:
                            logger.warning(
                                "Tool %s not found in plugin %s",
                                tool_name,
                                plugin.module,
                            )
                else:
                    # Load all tools from the module (look for __all__ or BaseTool instances)
                    tool_names = getattr(module, "__all__", [])
                    for name in tool_names:
                        obj = getattr(module, name, None)
                        if isinstance(obj, BaseTool) or (callable(obj) and hasattr(obj, "name")):
                            tools.append(obj)

                logger.info(
                    "Loaded %d tools from plugin %s",
                    len(tools),
                    plugin.module,
                )

            except ImportError as e:
                logger.error("Failed to import plugin %s: %s", plugin.module, e)
            except Exception as e:
                logger.error("Error loading plugin %s: %s", plugin.module, e)

        return tools

    def _build_system_prompt(
        self,
        config: CommunityConfig,
        additional_instructions: str,
    ) -> str:
        """Build the system prompt from configuration."""
        return COMMUNITY_SYSTEM_PROMPT_TEMPLATE.format(
            name=config.name,
            description=config.description,
            additional_instructions=additional_instructions,
        )

    def get_system_prompt(self) -> str:
        """Return the system prompt for this assistant."""
        return self._build_system_prompt(self.config, self.additional_instructions)


def create_community_assistant(
    model: "BaseChatModel",
    config: CommunityConfig,
    **kwargs,
) -> CommunityAssistant:
    """Factory function to create a generic community assistant.

    Args:
        model: The language model to use.
        config: Community configuration from YAML.
        **kwargs: Additional arguments passed to CommunityAssistant.

    Returns:
        Configured CommunityAssistant instance.
    """
    return CommunityAssistant(model=model, config=config, **kwargs)
