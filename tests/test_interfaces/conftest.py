"""Fixtures for interface protocol tests.

This module provides fixtures that yield all implementations of each protocol.
When new implementations are added (BIDS, EEGLAB), add them here to
automatically include them in all parameterized tests.
"""

import pytest
from langchain_core.language_models import FakeListChatModel

from src.agents.hed import HEDAssistant
from src.tools.hed import HED_DOCS

# ============================================================================
# Registry Implementations
# ============================================================================
# Add new registries here when they're implemented:
# from src.tools.bids import BIDS_DOCS
# from src.tools.eeglab import EEGLAB_DOCS

ALL_REGISTRIES = [
    HED_DOCS,
    # BIDS_DOCS,
    # EEGLAB_DOCS,
]


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
# Add new agent factories here when they're implemented.


def create_hed_agent():
    """Create a HED agent with fake model for testing."""
    model = FakeListChatModel(responses=["Test response"])
    return HEDAssistant(model=model, preload_docs=False)


# def create_bids_agent():
#     """Create a BIDS agent with fake model for testing."""
#     model = FakeListChatModel(responses=["Test response"])
#     return BIDSAssistant(model=model, preload_docs=False)


# def create_eeglab_agent():
#     """Create an EEGLAB agent with fake model for testing."""
#     model = FakeListChatModel(responses=["Test response"])
#     return EEGLABAssistant(model=model, preload_docs=False)


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
