"""Every tool a community's assistant binds gets a status label that says something.

While a reply is pending, the widget names what it is doing from the tool's name alone
(issue #538): "Searching datasets...", "Looking up documentation...", "Writing
code...". A tool whose name the widget cannot read falls back to "Working...", which is
true and says nothing. That is fine for a tool the widget has never heard of, and a
regression for one this repository ships.

So the tool names are not listed here: they are derived. For each community in the
registry a real `CommunityAssistant` is built from its config, as the API builds one,
and the tools it binds are read off it: the knowledge tools the config turns on, the
documentation tool, the page tool, the Python plugins, and the client tools a widget
that declares everything would get. MCP tools are served by a remote server this suite
does not reach (`-m "not network"`), so their names come from the community's own
system prompt, which `tests/test_assistants/test_nemar_mcp_wiring.py` holds to the
names the server actually serves.

Each name then goes through the widget's own classifier, evaluated by Bun from the
widget source (`classify_tool_activity.js`), with the community's id as the widget
would pass it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.assistants import discover_assistants, registry
from src.assistants.community import CommunityAssistant, PageContext
from src.core.config.community import FULL_OUTPUT_TOOL_NAME, CommunityConfig
from tests.helpers.chat_models import ScriptedChatModel

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER = Path(__file__).resolve().parent / "classify_tool_activity.js"

#: The label shape: capitalized words, then ASCII three dots, and nothing else.
LABEL = re.compile(r"^[A-Z][A-Za-z0-9 ]*\.\.\.$")

#: The words that make a tool's name about code, as the widget reads them.
CODE_WORDS = frozenset({"code", "python", "script"})


def _without_mcp(config: CommunityConfig) -> CommunityConfig:
    """The config with its MCP servers taken out, which would be reached over the
    network; their tools are named from the prompt instead (see `_mcp_tool_names`)."""
    if not config.extensions or not config.extensions.mcp_servers:
        return config
    extensions = config.extensions.model_copy(update={"mcp_servers": []})
    return config.model_copy(update={"extensions": extensions})


def _bound_tool_names(config: CommunityConfig) -> set[str]:
    declared = {tool.name for tool in (config.extensions.client_tools if config.extensions else [])}
    assistant = CommunityAssistant(
        model=ScriptedChatModel(responses=[]),
        config=_without_mcp(config),
        preload_docs=False,
        page_context=PageContext(url="https://example.org/page", title="A page"),
        citations=True,
        declared_client_tools=declared | {FULL_OUTPUT_TOOL_NAME},
    )
    return {tool.name for tool in assistant.tools}


def _mcp_tool_names(config: CommunityConfig) -> set[str]:
    """`<server>_<tool>` names the community's prompt tells the model to call."""
    if not config.extensions or not config.extensions.mcp_servers:
        return set()
    names: set[str] = set()
    for server in config.extensions.mcp_servers:
        names |= set(
            re.findall(rf"\b{re.escape(server.name)}_[a-z][a-z0-9_]*", config.system_prompt)
        )
    return names


@pytest.fixture(scope="module")
def every_tool() -> list[dict[str, str | bool]]:
    discover_assistants()
    entries: list[dict[str, str | bool]] = []
    mcp_seen = 0
    for info in registry.list_all():
        config = registry.get_community_config(info.id)
        assert config is not None, f"{info.id} has no config"
        mcp = _mcp_tool_names(config)
        mcp_seen += len(mcp)
        # The tools the community declares to run in the reader's browser runtime.
        runtime_tools = {
            tool.name for tool in (config.extensions.client_tools if config.extensions else [])
        }
        for name in sorted(_bound_tool_names(config) | mcp):
            entries.append(
                {"name": name, "community": config.id, "runtime_tool": name in runtime_tools}
            )
    # Not vacuous: the registry really does bind tools, of each source.
    assert len({e["community"] for e in entries}) >= 5, entries
    assert mcp_seen > 0, "no MCP tool names were found in any community prompt"
    assert any(e["name"].startswith("retrieve_") for e in entries)
    assert any(e["name"] == "execute_code" for e in entries)
    assert any(e["runtime_tool"] for e in entries), "no community declares a runtime client tool"
    return entries


@pytest.fixture(scope="module")
def classified(every_tool: list[dict[str, str | bool]]) -> list[dict]:
    bun = shutil.which("bun")
    if bun is None:
        if os.environ.get("CI"):
            pytest.fail("bun is not on PATH, and CI must run this test")
        pytest.skip("bun is not on PATH")
    result = subprocess.run(
        [bun, str(CLASSIFIER)],
        input=json.dumps(every_tool),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"the classifier failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_every_real_tool_gets_a_label_that_says_what_it_does(classified: list[dict]) -> None:
    generic = [
        f"{entry['community']}: {entry['name']}"
        for entry in classified
        if entry["running"]["kind"] == "other"
    ]
    assert not generic, (
        "these tools would read only 'Working...' in the widget; teach "
        "classifyToolActivity in frontend/osa-chat-widget.js their verb:\n  " + "\n  ".join(generic)
    )


def test_every_label_is_plain_words_and_never_the_raw_name(classified: list[dict]) -> None:
    for entry in classified:
        for phase in ("writing", "running"):
            label = entry[phase]["label"]
            assert LABEL.match(label), f"{entry['name']} ({phase}): {label!r}"
            assert "_" not in label and entry["name"] not in label, f"{entry['name']}: {label!r}"
            assert len(label) <= 56, f"{entry['name']}: {label!r} is too long for the bubble"


def test_the_code_tools_say_writing_then_running(classified: list[dict]) -> None:
    code = [entry for entry in classified if entry["running"]["kind"] == "code"]
    assert code, "no client tool that runs code was found in any community"
    for entry in code:
        assert entry["writing"]["label"] == "Writing code...", entry
        assert entry["running"]["label"] == "Running code...", entry


def test_only_code_reads_differently_while_it_is_written(classified: list[dict]) -> None:
    for entry in classified:
        if entry["running"]["kind"] != "code":
            assert entry["writing"] == entry["running"], entry


def test_code_is_said_only_of_tools_that_run_code(classified: list[dict]) -> None:
    """The widget says "Writing code..." of a tool whose name says it is code, and of
    every tool a community declares to run in the reader's browser. Running anything
    else (a query, say) is not running code."""
    for entry in classified:
        words = set(re.split(r"[^a-z0-9]+", entry["name"].lower()))
        if entry["running"]["kind"] == "code":
            assert words & CODE_WORDS or entry["runtime_tool"], (
                f"{entry['community']}: {entry['name']} reads as code, but its name has none "
                f"of {sorted(CODE_WORDS)} and it is not a declared runtime client tool"
            )
        if entry["runtime_tool"]:
            assert entry["running"]["kind"] == "code", entry
