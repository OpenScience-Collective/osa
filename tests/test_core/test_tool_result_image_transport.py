"""What a tool result can carry into the model's context.

The browser-execution design note has ``execute_code`` run in the user's
browser and hand its output back as a tool result, "images included", and then
lists that claim among its open questions as stated without being verified.
Verifying it matters before Phase 1 fixes the tool-result contract: if an image
cannot ride on a tool message, plots have to reach the model some other way and
both the server contract and the widget change shape.

These tests assert the real payload this repository would send: a real
``CachingChatAnthropic`` from ``create_anthropic_llm``, and the real
``_get_request_payload`` that every generate and stream path calls. What they
do not prove is that the endpoint accepts the payload or that the model reads
the picture, which is
``tests/test_integration/test_anthropic_platform.py::TestToolResultImages``.
"""

import base64

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.api.config import Settings
from src.core.services.anthropic_llm import CachingChatAnthropic, create_anthropic_llm
from src.core.services.anthropic_models import IMAGE_MEDIA_TYPES
from tests.helpers.images import bar_chart_png

TOOL_CALL_ID = "toolu_01aaaaaaaaaaaaaaaaaaaaaa"

PNG_BASE64 = base64.b64encode(bar_chart_png([0.45, 0.2, 0.7, 1.0, 0.35])).decode()

# The spellings a producer could plausibly use for the same PNG: the Anthropic
# native block, LangChain's v1 and v0 standard image blocks, and the OpenAI
# style data URL. Every one of them has to arrive as one Anthropic image block,
# or a widget that picked the wrong spelling would silently drop the figure.
IMAGE_BLOCK_SPELLINGS: dict[str, dict] = {
    "anthropic_native": {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": PNG_BASE64},
    },
    "langchain_v1_standard": {"type": "image", "base64": PNG_BASE64, "mime_type": "image/png"},
    "langchain_v0_standard": {
        "type": "image",
        "source_type": "base64",
        "data": PNG_BASE64,
        "mime_type": "image/png",
    },
    "data_url": {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{PNG_BASE64}"},
    },
}


def _settings() -> Settings:
    """Anthropic settings fixed in the test, never read from the worktree .env."""
    return Settings(
        anthropic_api_key="test-server-key",
        anthropic_base_url="https://aws-anthropic.example.test",
        anthropic_workspace_id="wrkspc_test123",
        anthropic_max_output_tokens=8000,
        anthropic_cache_ttl="5m",
    )


def _tool_result(tool_content: list[dict], *, status: str = "success") -> dict:
    """Send one browser tool round trip through the payload builder.

    Returns the ``tool_result`` block as it would leave this process.
    """
    llm = create_anthropic_llm(model="claude-haiku-4-5", thinking=None, settings=_settings())
    # Assert the class, as TestCachingChatAnthropicPayload._llm does: caching is
    # on by default, and if that default is ever flipped these tests would go on
    # passing while measuring a plain ChatAnthropic instead of what ships.
    assert isinstance(llm, CachingChatAnthropic)
    payload = llm._get_request_payload(
        [
            SystemMessage(content="You help with EEG analysis."),
            HumanMessage(content="Plot the alpha power."),
            AIMessage(
                content=[{"type": "text", "text": "Running it."}],
                tool_calls=[
                    {
                        "name": "execute_code",
                        "args": {"code": "plot_alpha()"},
                        "id": TOOL_CALL_ID,
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content=tool_content, tool_call_id=TOOL_CALL_ID, status=status),
        ]
    )
    blocks = [
        block
        for message in payload["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert len(blocks) == 1, f"expected exactly one tool_result block, got {len(blocks)}"
    return blocks[0]


@pytest.mark.parametrize("spelling", sorted(IMAGE_BLOCK_SPELLINGS))
def test_an_image_survives_on_a_tool_message(spelling: str) -> None:
    """Every accepted spelling arrives as an Anthropic image block in the result."""
    block = _tool_result(
        [{"type": "text", "text": "peak 10.2 Hz"}, IMAGE_BLOCK_SPELLINGS[spelling]]
    )

    assert block["tool_use_id"] == TOOL_CALL_ID
    assert block["is_error"] is False
    text, image = block["content"]
    assert text == {"type": "text", "text": "peak 10.2 Hz"}
    assert image == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": PNG_BASE64},
    }


def test_an_unknown_block_type_is_passed_through_untouched() -> None:
    """The control for the test above: not everything becomes an image block.

    Without this, a converter that rewrote every block into an image would
    satisfy the parametrized test, and so would one that ignored the block type
    entirely. It also states the property the next test depends on: this layer
    forwards what it is given rather than validating it.
    """
    unknown = {"type": "imagex", "base64": PNG_BASE64, "mime_type": "image/png"}

    block = _tool_result([{"type": "text", "text": "peak 10.2 Hz"}, unknown])

    assert block["content"][1] == unknown


def test_an_unaccepted_media_type_reaches_the_request_unchallenged() -> None:
    """SVG is forwarded, so the producer is the only thing that can refuse it.

    This is the trap the IMAGE_MEDIA_TYPES declaration exists for. A widget
    that calls ``savefig(format="svg")`` gets a figure that looks correct
    locally, passes every layer in this process, and fails as a 400 from the
    endpoint after the analysis has already run.
    """
    svg = {"type": "image", "base64": PNG_BASE64, "mime_type": "image/svg+xml"}

    block = _tool_result([svg])

    assert block["content"][0]["source"]["media_type"] == "image/svg+xml"
    assert "image/svg+xml" not in IMAGE_MEDIA_TYPES


def test_a_failed_execution_is_marked_as_an_error() -> None:
    """A traceback comes back on the same channel, flagged rather than narrated.

    Phase 1's contract needs the failure path to be distinguishable from a
    successful run that happened to print a traceback.
    """
    block = _tool_result(
        [{"type": "text", "text": "ZeroDivisionError: division by zero"}], status="error"
    )

    assert block["is_error"] is True


def test_no_cache_marker_lands_inside_the_tool_result() -> None:
    """The caching wrapper and the tool result do not collide.

    A forward guard, deliberately. On the first-party Anthropic transport this
    repository uses, langchain-anthropic puts ``cache_control`` in the top-level
    request parameter and never on a message content block; block-level
    placement is the Bedrock and Vertex path, which this repository does not
    take. So the invariant holds today for a reason outside the tool-result code
    and this test cannot currently fail.

    It is worth keeping because both halves of that reason are changeable: the
    API does not accept ``cache_control`` on a tool_result's sub-blocks at all
    (langchain-anthropic hoists any it finds there up to the tool_result), and a
    marker strategy that moved onto message content would put a marker exactly
    where a figure travels. This is what would notice.
    """
    block = _tool_result(
        [{"type": "text", "text": "peak 10.2 Hz"}, IMAGE_BLOCK_SPELLINGS["anthropic_native"]]
    )

    assert "cache_control" not in block
    assert all("cache_control" not in sub_block for sub_block in block["content"])
