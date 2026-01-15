"""LangGraph agent definitions for Open Science Assistant."""

from src.agents.base import BaseAgent, SimpleAgent, ToolAgent
from src.agents.state import BaseAgentState, RouterState, SpecialistState

# HEDAssistant is now in src.assistants.hed, but we re-export for backward compat
# Import lazily to avoid circular imports
# Use: from src.assistants.hed import HEDAssistant

__all__ = [
    "BaseAgent",
    "SimpleAgent",
    "ToolAgent",
    "BaseAgentState",
    "RouterState",
    "SpecialistState",
]
