"""Integration tests against the real Claude Platform on AWS endpoint.

These tests make real, paid API calls through create_anthropic_llm()'s
server mode. Keep prompts tiny so a run costs cents.

Skip condition: the plan for this module specified
``skipif(not os.getenv("ANTHROPIC_API_KEY"), ...)``, but in this worktree
ANTHROPIC_API_KEY (like ANTHROPIC_BASE_URL and ANTHROPIC_WORKSPACE_ID) lives
only in the .env file read by pydantic-settings; it is never exported into
the process environment, so os.getenv would never see it here and the
condition would always skip. create_anthropic_llm() itself reads these
credentials through Settings, so Settings is the correct source of truth
for "is server mode configured" and is used for the skip check instead.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from src.api.config import get_settings
from src.core.services.anthropic_llm import create_anthropic_llm

pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm,
    pytest.mark.skipif(
        not get_settings().anthropic_api_key,
        reason="ANTHROPIC_API_KEY is not configured (server mode requires it)",
    ),
]


def _extract_text(content: str | list) -> str:
    """Extract plain text from AIMessage content.

    Content is a plain string when thinking is off (or the model chose not
    to think for a trivial prompt), and a list of content blocks (a
    "thinking" block followed by a "text" block, or just "text") when
    thinking is on. Both shapes were observed while writing this test
    against the live endpoint, so extraction has to handle both.
    """
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


@tool
def get_secret_number() -> int:
    """Return the current secret number."""
    return 42


class TestHaikuDefaultThinking:
    """claude-haiku-4-5 with its default (budget-style) thinking configuration."""

    def test_answers_short_question(self) -> None:
        llm = create_anthropic_llm(model="claude-haiku-4-5")
        response = llm.invoke([HumanMessage(content="Reply with exactly: OK")])

        text = _extract_text(response.content)
        assert text.strip() != ""
        assert response.usage_metadata is not None
        assert response.usage_metadata["input_tokens"] > 0
        assert response.usage_metadata["output_tokens"] > 0

    def test_tool_call_round_trip_with_thinking_on(self) -> None:
        """Tool calls plus extended thinking: the old LiteLLM wrapper could not do this."""
        llm = create_anthropic_llm(model="claude-haiku-4-5")
        bound = llm.bind_tools([get_secret_number])

        messages: list[AIMessage | HumanMessage | ToolMessage] = [
            HumanMessage(
                content="Call get_secret_number and state the result in one short sentence."
            )
        ]
        first = bound.invoke(messages)
        assert first.tool_calls, f"Expected a tool call, got content: {first.content!r}"

        messages.append(first)
        for call in first.tool_calls:
            result = get_secret_number.invoke(call["args"])
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

        final = bound.invoke(messages)
        text = _extract_text(final.content)
        assert "42" in text


class TestSonnetAdaptiveThinking:
    """claude-sonnet-5 with its default (adaptive) thinking configuration."""

    def test_answers_short_question(self) -> None:
        llm = create_anthropic_llm(model="claude-sonnet-5")
        response = llm.invoke([HumanMessage(content="Reply with exactly: OK")])

        text = _extract_text(response.content)
        assert text.strip() != ""
        assert response.usage_metadata is not None
        assert response.usage_metadata["input_tokens"] > 0
        assert response.usage_metadata["output_tokens"] > 0
