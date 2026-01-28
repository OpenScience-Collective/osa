"""Tests for EEGLAB-specific behaviors.

EEGLAB Phase 1 uses the standard YAML-based configuration without custom tools
or unique behavioral logic. All standard configuration validation is handled by
test_community_yaml_generic.py parametrized tests.

This file is kept minimal for Phase 1 and may be expanded in future phases
if EEGLAB-specific behavioral tests are needed (e.g., custom validation tools,
unique workflow logic not captured in YAML).

For generic YAML validation tests, see:
- tests/test_assistants/test_community_yaml_generic.py
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    """Ensure registry is populated before tests."""
    from src.assistants import discover_assistants, registry

    registry._assistants.clear()
    discover_assistants()


class TestEEGLABBehaviors:
    """Tests for EEGLAB-specific behavioral logic not covered by YAML config.

    Phase 1: No custom tools or behaviors yet.
    Future phases may add tests here for EEGLAB-specific functionality.
    """

    def test_eeglab_uses_standard_community_assistant(self) -> None:
        """EEGLAB Phase 1 uses the standard CommunityAssistant class."""
        from src.assistants import registry
        from src.assistants.community import CommunityAssistant

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        assert isinstance(assistant, CommunityAssistant)
        assert assistant.config.id == "eeglab"
