"""Component interfaces for OSA.

This module defines Protocol classes that establish contracts for:
- Document registries (HED, BIDS, EEGLAB, etc.)
- Document pages
- Tools
- Agents

By implementing these protocols, components become automatically testable
with the parameterized test suite in tests/test_interfaces/.
"""

from src.interfaces.protocols import (
    AgentProtocol,
    DocPageProtocol,
    DocRegistryProtocol,
    RetrievedDocProtocol,
    ToolProtocol,
)

__all__ = [
    "DocPageProtocol",
    "DocRegistryProtocol",
    "RetrievedDocProtocol",
    "ToolProtocol",
    "AgentProtocol",
]
