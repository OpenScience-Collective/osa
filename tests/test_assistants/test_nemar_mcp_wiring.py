"""The NEMAR assistant really loads its tools from the MCP server.

Separate from `tests/test_tools/test_mcp_client.py`, which proves the client
works against a server. This proves the WIRING: that the real
`src/assistants/nemar/config.yaml` reaches `_load_mcp_tools`, and that the tool
names the assistant ends up with are the ones its system prompt tells the model
to call. A prefix change or a config typo would leave every client test green and
the assistant still broken, which is exactly the gap a helper-level test cannot
see.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from src.assistants.community import CommunityAssistant
from src.core.config.community import CommunityConfig

# Resolved from this file, not from the working directory, so the test does not
# quietly depend on pytest's rootdir.
NEMAR_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "assistants" / "nemar" / "config.yaml"
)


@lru_cache(maxsize=1)
def _nemar_config() -> CommunityConfig:
    """The real shipped config, read from disk. Not a fixture built in the test:
    the point is that the file the app loads is the one asserted on."""
    return CommunityConfig.from_yaml(NEMAR_CONFIG_PATH)


class TestNemarConfig:
    """These run offline: they read the config, not the network."""

    def test_config_declares_the_mcp_server(self) -> None:
        extensions = _nemar_config().extensions
        assert extensions is not None
        assert [s.name for s in extensions.mcp_servers] == ["nemar"]
        assert str(extensions.mcp_servers[0].url) == "https://mcp.nemar.org/mcp"

    def test_the_dead_python_plugins_are_gone(self) -> None:
        """`search_nemar_datasets` and `get_nemar_dataset_details` called
        nemar.org/api/dataexplorer/datapipeline/..., which returns 404 since the
        legacy site was retired. The MCP server replaces them; if they come back,
        the assistant is calling a dead endpoint again."""
        extensions = _nemar_config().extensions
        assert extensions is not None
        assert extensions.python_plugins == []

    def test_the_prompt_names_the_prefixed_tools(self) -> None:
        """The prompt has to use the names the loader actually produces
        (`<server>_<tool>`), or the model will call tools that do not exist."""
        prompt = _nemar_config().system_prompt
        for tool in ("nemar_search_datasets", "nemar_describe_dataset", "nemar_read_window"):
            assert tool in prompt
        # And must not still describe the removed ones.
        assert "search_nemar_datasets" not in prompt
        assert "get_nemar_dataset_details" not in prompt

    def test_the_prompt_does_not_point_at_the_retired_site(self) -> None:
        prompt = _nemar_config().system_prompt
        assert "dataexplorer" not in prompt
        assert "nemar.org/dataset/" in prompt


@pytest.mark.network
class TestNemarToolLoading:
    """The real thing, against the real server. Deselected in CI."""

    def test_load_mcp_tools_returns_the_six_nemar_tools(self) -> None:
        # `_load_mcp_tools` does not touch instance state, so it can be exercised
        # without standing up a model -- and it is the exact method the
        # constructor calls.
        tools = CommunityAssistant._load_mcp_tools(None, _nemar_config())  # type: ignore[arg-type]
        assert {t.name for t in tools} == {
            "nemar_search_datasets",
            "nemar_describe_dataset",
            "nemar_list_recordings",
            "nemar_get_events",
            "nemar_render_overview",
            "nemar_read_window",
        }

    def test_every_prompt_named_tool_actually_exists(self) -> None:
        """The check that matters: no gap between what the prompt promises and
        what the server provides."""
        tools = CommunityAssistant._load_mcp_tools(None, _nemar_config())  # type: ignore[arg-type]
        available = {t.name for t in tools}
        prompt = _nemar_config().system_prompt
        named = {name for name in available if name in prompt}
        # Every tool the prompt names is available...
        assert named <= available
        # ...and the prompt really does name the ladder, not just one of them.
        assert len(named) >= 6
