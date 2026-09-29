"""Integration tests against the real Amazon Bedrock endpoint.

These tests make real, paid calls through ``create_bedrock_llm()``. The prompts are
tiny, and the models cost cents per million tokens, so a run costs a fraction of a
cent. Like ``test_anthropic_platform.py`` they read the credential through Settings
(it lives in ``.env``, not the process environment) to decide whether to skip.

What is asserted here is what no offline test can show: that the service accepts the
requests OSA builds. Each assertion targets something learned the hard way against the
live service: a second model call in a tool loop, Luna's automatic cache, and the
tagged citations coming back for a real reply. Model wording is never asserted on.
"""

import re
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from src.api.config import get_settings
from src.core.services.anthropic_models import BEDROCK_MODELS
from src.core.services.bedrock_llm import create_bedrock_llm
from src.core.services.tagged_citations import CITATION_INSTRUCTION
from src.tools.citations import build_search_result

pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm,
    pytest.mark.skipif(
        not get_settings().bedrock_api_key,
        reason="AWS_BEARER_TOKEN_BEDROCK is not configured",
    ),
]

ALL_MODELS = sorted(BEDROCK_MODELS)


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


@pytest.mark.parametrize("model_id", ALL_MODELS)
class TestEveryModel:
    def test_answers_a_short_question(self, model_id: str) -> None:
        llm = create_bedrock_llm(model_id)

        reply = llm.invoke([HumanMessage(content="Reply with exactly: OK")])

        assert _text(reply.content).strip() != ""
        assert reply.usage_metadata["input_tokens"] > 0
        assert reply.usage_metadata["output_tokens"] > 0

    def test_a_second_call_in_a_tool_loop_is_accepted(self, model_id: str) -> None:
        """The history sent back after a tool call carries the model's reasoning.

        GPT-6 Luna answers 400 to reasoning text in an assistant turn and the other
        two reject its signature, so this fails unless the reasoning is left out.
        """
        bound = create_bedrock_llm(model_id).bind_tools([get_secret_number])
        messages: list = [
            HumanMessage(content="Call get_secret_number and state the result in one sentence.")
        ]

        first = bound.invoke(messages)
        assert first.tool_calls, f"expected a tool call, got {first.content!r}"
        messages.append(first)
        for call in first.tool_calls:
            messages.append(
                ToolMessage(
                    content=str(get_secret_number.invoke(call["args"])), tool_call_id=call["id"]
                )
            )
        final = bound.invoke(messages)

        assert "42" in _text(final.content)

    def test_a_reply_never_shows_a_citation_tag(self, model_id: str) -> None:
        """Whatever the model writes, no [src:N] reaches the reader, and every citation
        it earned names a source that was in the conversation."""
        llm = create_bedrock_llm(model_id)
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
        results = [build_search_result(url, title, text) for url, (title, text) in sources.items()]
        messages = [
            SystemMessage(content="You are the HED assistant.\n\n" + CITATION_INSTRUCTION),
            HumanMessage(content="What does Sensory-event mark, and how do I group tags?"),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_secret_number", "args": {}, "id": "call-1"}],
            ),
            ToolMessage(content=results, tool_call_id="call-1", name="get_secret_number"),
        ]

        reply = llm.bind_tools([get_secret_number]).invoke(messages)

        assert not re.search(r"\[src:", _text(reply.content), re.IGNORECASE)
        cited = {
            citation["source"]
            for block in reply.content
            if isinstance(block, dict)
            for citation in block.get("citations", [])
        }
        assert cited <= set(sources)


class TestLunaCaching:
    def test_a_repeated_prompt_prefix_is_read_back_from_cache(self) -> None:
        """Luna caches on its own; the second call reports the tokens it read.

        The prefix is unique to the run so a cache entry from an earlier run cannot
        satisfy it, and long enough to be worth caching.
        """
        llm = create_bedrock_llm("openai.gpt-6-luna", max_tokens=200)
        prefix = f"Run {uuid.uuid4().hex}. The Open Science Assistant helps researchers. " * 400
        messages = [SystemMessage(content=prefix), HumanMessage(content="Reply with exactly: OK")]

        first = llm.invoke(messages)
        second = llm.invoke(messages)

        assert first.usage_metadata["input_token_details"]["cache_read"] == 0
        assert first.usage_metadata["input_token_details"]["cache_creation"] > 0
        assert second.usage_metadata["input_token_details"]["cache_read"] > 0

    def test_input_tokens_count_the_cached_ones_once(self) -> None:
        """OSA prices ordinary = input - cache_read - cache_creation, so input_tokens must
        be the whole prompt. Bedrock's own inputTokens excludes cached tokens (2 of 9,216
        in the run that established this); langchain-aws adds them back. If either side
        changed, the same prompt would report different totals on its two calls, or a
        total below the cached count, and cost would be over- or under-stated."""
        llm = create_bedrock_llm("openai.gpt-6-luna", max_tokens=200)
        prefix = f"Run {uuid.uuid4().hex}. The Open Science Assistant helps researchers. " * 400
        messages = [SystemMessage(content=prefix), HumanMessage(content="Reply with exactly: OK")]

        first = llm.invoke(messages).usage_metadata
        second = llm.invoke(messages).usage_metadata

        cached = second["input_token_details"]["cache_read"]
        assert cached > 0
        assert second["input_tokens"] >= cached
        assert first["input_tokens"] == pytest.approx(second["input_tokens"], abs=16)
        assert first["input_tokens"] == pytest.approx(
            first["input_token_details"]["cache_creation"], rel=0.01
        )
