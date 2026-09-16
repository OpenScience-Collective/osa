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

import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
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
        """Tool calls plus extended thinking, through the same bound object.

        The old LiteLLM wrapper's public invoke()/bind_tools() path would
        have worked; what actually broke was its _generate()/_agenerate()
        delegating to self.llm._generate(), which raises AttributeError once
        bind_tools() has replaced self.llm with a RunnableBinding (a
        RunnableBinding does not expose _generate). CachingChatAnthropic
        avoids that failure mode entirely (see its class docstring), and
        this test exercises the same bind_tools() -> invoke() path to prove
        it end to end against the live endpoint.
        """
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


class TestPromptCaching:
    """Proves prompt caching actually works on the wire.

    No unit test can prove this: tests/test_core/test_anthropic_llm.py only
    asserts that a cache_control marker is present in the outgoing payload,
    never that the Claude Platform on AWS endpoint actually honors it. This
    sends the same large system prefix twice and checks that the second
    call's usage_metadata reports tokens read from cache.
    """

    def test_second_call_with_shared_system_prefix_reports_cache_read(self) -> None:
        # Haiku's minimum cacheable prefix is about 4096 tokens; repeat a
        # paragraph enough times to sit comfortably above that so this test
        # is not sensitive to the exact tokenizer count. A unique run marker
        # is mixed in so this test's cache entry cannot be a stale hit left
        # over from a previous run of this same test (which would let the
        # assertion pass without this run's own two calls proving anything).
        run_marker = uuid.uuid4().hex
        paragraph = (
            f"Run {run_marker}: the Open Science Assistant helps researchers "
            "work with BIDS, HED, and EEGLAB by answering precise, "
            "citation-backed questions for small research communities "
            "running their own lab servers. "
        )
        system_prompt = paragraph * 300

        # thinking=None keeps the generated output tiny and avoids any
        # budget/max_tokens interaction; caching (enable_caching defaults to
        # True) is exactly what this test is exercising.
        llm = create_anthropic_llm(model="claude-haiku-4-5", max_tokens=32, thinking=None)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="Reply with exactly: OK"),
        ]

        first = llm.invoke(messages)
        assert first.usage_metadata is not None
        first_cache_read = (first.usage_metadata.get("input_token_details") or {}).get(
            "cache_read"
        ) or 0
        assert first_cache_read == 0, (
            "First call with a freshly unique system prefix should not hit an "
            f"existing cache entry; got usage_metadata={first.usage_metadata!r}"
        )

        second = llm.invoke(messages)
        assert second.usage_metadata is not None
        input_token_details = second.usage_metadata.get("input_token_details") or {}
        cache_read = input_token_details.get("cache_read") or 0
        assert cache_read > 0, (
            "Expected a non-zero cache_read on the second call sharing the "
            f"same system prefix; got usage_metadata={second.usage_metadata!r}. "
            "Prompt caching is not taking effect against the live endpoint."
        )
