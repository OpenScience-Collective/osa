"""Integration tests against the real OpenRouter service.

These make real, paid calls through ``create_openrouter_llm()`` and need
``OPENROUTER_API_KEY_FOR_TESTING`` (skipped without it, like the other OpenRouter
tests in ``test_litellm_llm.py``). The prompts are tiny and the models cost cents per
million tokens.

What is asserted is what no offline test can show: that OpenRouter accepts the
requests OSA builds (tagged tool text, cache markers) and reports what the metrics
read (cache tokens, reasoning tokens). ``tests/test_core/test_litellm_chat.py`` covers
the same behavior against a local fake of OpenRouter's wire format; if this file and
that one disagree, the fake has drifted from the service. Model wording is never
asserted on.
"""

import os
import re
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from src.core.services.litellm_llm import create_openrouter_llm
from src.core.services.tagged_citations import CITATION_INSTRUCTION
from src.tools.citations import build_search_result

API_KEY = os.getenv("OPENROUTER_API_KEY_FOR_TESTING")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm,
    pytest.mark.skipif(not API_KEY, reason="OPENROUTER_API_KEY_FOR_TESTING not set"),
]

#: One Anthropic slug (which OpenRouter caches on request) and the non-Anthropic
#: models the Bedrock work put on OpenRouter for callers with their own key.
SLUGS = ["anthropic/claude-haiku-4.5", "openai/gpt-6-luna", "openai/gpt-oss-120b"]


def _text(content: str | list) -> str:
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


def _llm(slug: str, **kwargs):
    provider = "Anthropic" if slug.startswith("anthropic/") else None
    return create_openrouter_llm(model=slug, api_key=API_KEY, provider=provider, **kwargs)


@pytest.mark.parametrize("slug", SLUGS)
class TestEveryModel:
    def test_a_second_call_in_a_tool_loop_is_accepted(self, slug: str) -> None:
        bound = _llm(slug).bind_tools([get_secret_number])
        messages: list = [
            HumanMessage(content="Call get_secret_number and state the result in one sentence.")
        ]

        first = bound.invoke(messages)
        assert first.tool_calls, f"expected a tool call, got {first.content!r}"
        messages.append(first)
        for call in first.tool_calls:
            messages.append(ToolMessage(content="42", tool_call_id=call["id"]))
        final = bound.invoke(messages)

        assert "42" in _text(final.content)

    def test_a_reply_never_shows_a_citation_tag(self, slug: str) -> None:
        """Whatever the model writes, no [src:N] reaches the reader, and every citation
        it earned names a source that was in the conversation."""
        sources = {
            "https://hedtags.org/sensory": (
                "Sensory-event",
                "The Sensory-event tag marks a stimulus.",
            ),
            "https://hedtags.org/basics": (
                "HED basics",
                "Parentheses group tags describing one thing.",
            ),
        }
        messages = [
            SystemMessage(content="You are the HED assistant.\n\n" + CITATION_INSTRUCTION),
            HumanMessage(content="What does Sensory-event mark, and how do I group tags?"),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_secret_number", "args": {}, "id": "call-1"}],
            ),
            ToolMessage(
                content=[
                    build_search_result(url, title, text) for url, (title, text) in sources.items()
                ],
                tool_call_id="call-1",
                name="get_secret_number",
            ),
        ]

        reply = _llm(slug).bind_tools([get_secret_number]).invoke(messages)

        assert not re.search(r"\[src:", _text(reply.content), re.IGNORECASE)
        content = reply.content if isinstance(reply.content, list) else []
        cited = {
            citation["source"]
            for block in content
            if isinstance(block, dict)
            for citation in block.get("citations", [])
        }
        assert cited <= set(sources)

    def test_the_stream_never_shows_a_citation_tag(self, slug: str) -> None:
        messages = [
            SystemMessage(content="You are the HED assistant.\n\n" + CITATION_INSTRUCTION),
            HumanMessage(content="What does Sensory-event mark?"),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_secret_number", "args": {}, "id": "call-1"}],
            ),
            ToolMessage(
                content=[
                    build_search_result(
                        "https://hedtags.org/sensory",
                        "Sensory-event",
                        "The Sensory-event tag marks a stimulus.",
                    )
                ],
                tool_call_id="call-1",
                name="get_secret_number",
            ),
        ]

        pieces = [
            _text(chunk.content)
            for chunk in _llm(slug).bind_tools([get_secret_number]).stream(messages)
        ]

        assert not any("[src" in piece.lower() for piece in pieces), pieces


class TestUsageDetails:
    def test_an_anthropic_slug_reports_cache_reads_on_a_repeated_prefix(self) -> None:
        """The metrics price cache reads at a tenth; LiteLLM alone reports only totals.

        The prefix is unique to the run and long enough to be cacheable on Haiku
        (2,048 tokens).
        """
        llm = _llm("anthropic/claude-haiku-4.5", max_tokens=50)
        prefix = f"Run {uuid.uuid4().hex}. The Open Science Assistant helps researchers. " * 400
        messages = [SystemMessage(content=prefix), HumanMessage(content="Reply with exactly: OK")]

        llm.invoke(messages)
        second = llm.invoke(messages)

        details = second.usage_metadata.get("input_token_details", {})
        assert details.get("cache_read", 0) > 0, second.usage_metadata

    def test_a_reasoning_model_reports_its_reasoning_tokens(self) -> None:
        llm = _llm("openai/gpt-6-luna", max_tokens=2000)

        reply = llm.invoke([HumanMessage(content="What is 17 times 23? Think it through.")])

        assert reply.usage_metadata["output_tokens"] > 0
        details = reply.usage_metadata.get("output_token_details", {})
        assert details.get("reasoning", 0) > 0, reply.usage_metadata
