"""Fixtures for interface protocol tests.

This module provides fixtures that yield all implementations of each protocol.
When new implementations are added (BIDS, EEGLAB), add them here to
automatically include them in all parameterized tests.
"""

import pytest
from langchain_core.language_models import FakeListChatModel

from src.assistants import discover_assistants
from src.assistants import registry as assistant_registry

# Discover assistants at module load
discover_assistants()


# ============================================================================
# Registry Implementations
# ============================================================================
# Get registries dynamically from discovered assistants


def get_all_registries():
    """Get doc registries from all registered assistants."""
    registries = []
    for info in assistant_registry.list_all():
        if info.community_config:
            registries.append(info.community_config.get_doc_registry())
    return registries


ALL_REGISTRIES = get_all_registries()


@pytest.fixture(params=ALL_REGISTRIES, ids=lambda r: r.name)
def registry(request):
    """Fixture that yields each document registry.

    Tests using this fixture will run once for each registry.
    """
    return request.param


# ============================================================================
# Agent Factory Functions
# ============================================================================
# These functions create agents with a fake model for testing.


def create_hed_agent():
    """Create a HED agent with fake model for testing."""
    model = FakeListChatModel(responses=["Test response"])
    return assistant_registry.create_assistant("hed", model=model, preload_docs=False)


# def create_bids_agent():
#     """Create a BIDS agent with fake model for testing."""
#     model = FakeListChatModel(responses=["Test response"])
#     return assistant_registry.create_assistant("bids", model=model, preload_docs=False)


# def create_eeglab_agent():
#     """Create an EEGLAB agent with fake model for testing."""
#     model = FakeListChatModel(responses=["Test response"])
#     return assistant_registry.create_assistant("eeglab", model=model, preload_docs=False)


AGENT_FACTORIES = [
    ("hed", create_hed_agent),
    # ("bids", create_bids_agent),
    # ("eeglab", create_eeglab_agent),
]


@pytest.fixture(params=AGENT_FACTORIES, ids=lambda x: x[0])
def agent(request):
    """Fixture that yields each agent type.

    Tests using this fixture will run once for each agent.
    The agent is created fresh for each test.
    """
    _, factory = request.param
    return factory()
