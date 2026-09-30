"""How a model's reply ended, read from each provider adapter's own metadata.

The pure reader is tested on the shapes the adapters leave. Then each provider path is
driven through its real client stack, at the layer the project's tests already stand in
for the network (botocore's ``before-send`` for Bedrock, a patched transport for Anthropic, the
local OpenRouter stand-in for LiteLLM), to check that the key the reader looks for is
the one the adapter really writes, on a streamed reply and on a complete one.
"""

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from src.api.config import Settings
from src.core.services.anthropic_llm import create_anthropic_llm
from src.core.services.bedrock_llm import _bedrock_client, create_bedrock_llm
from src.core.services.model_outcome import (
    CONTEXT_WINDOW_STOP_REASON,
    DECLINED_STOP_REASONS,
    MALFORMED_STOP_REASONS,
    STOP_REASON_KEYS,
    TRUNCATING_STOP_REASONS,
    stop_reason,
    truncation_reason,
)
from tests.helpers.anthropic_wire import reply_with, served_by
from tests.helpers.bedrock_wire import EVENT_STREAM, Wire, converse_stream


class TestTheReader:
    @pytest.mark.parametrize("key", STOP_REASON_KEYS)
    @pytest.mark.parametrize("reason", sorted(TRUNCATING_STOP_REASONS))
    def test_a_truncating_reason_under_any_providers_key(self, key: str, reason: str) -> None:
        assert truncation_reason({key: reason, "model_provider": "x"}) == reason

    @pytest.mark.parametrize(
        "metadata",
        [
            {"stopReason": "end_turn"},
            {"stopReason": "tool_use"},
            {"stopReason": "stop_sequence"},
            {"stop_reason": "end_turn"},
            {"stop_reason": "tool_use"},
            {"stop_reason": "refusal"},
            {"finish_reason": "stop"},
            {"finish_reason": "tool_calls"},
            {"finish_reason": "content_filter"},
            {},
            {"stopReason": None},
            {"stopReason": ""},
            {"stopReason": 7},
            {"model_name": "length"},
            None,
            "max_tokens",
            ["length"],
        ],
    )
    def test_a_reply_that_finished_is_not_truncated(self, metadata: object) -> None:
        assert truncation_reason(metadata) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("reason", sorted(TRUNCATING_STOP_REASONS))
    def test_a_reason_written_twice_by_a_chunk_merge_still_reads(self, reason: str) -> None:
        """langchain-core concatenates a string that two chunks both carry."""
        first = AIMessageChunk(content="", response_metadata={"finish_reason": reason})
        second = AIMessageChunk(content="", response_metadata={"finish_reason": reason})

        merged = first + second

        assert merged.response_metadata["finish_reason"] == reason * 2
        assert truncation_reason(merged.response_metadata) == reason

    @pytest.mark.parametrize("value", ["lengthy", "max_tokens_left", "length max_tokens", "len"])
    def test_a_reason_that_only_contains_one_is_not_truncated(self, value: str) -> None:
        assert truncation_reason({"finish_reason": value}) is None


class TestTheStopReasonWhateverItSays:
    @pytest.mark.parametrize("key", STOP_REASON_KEYS)
    @pytest.mark.parametrize(
        "reason",
        sorted(
            TRUNCATING_STOP_REASONS
            | DECLINED_STOP_REASONS
            | MALFORMED_STOP_REASONS
            | {"end_turn", "tool_use", "stop", "something_new"}
        ),
    )
    def test_it_is_read_under_any_providers_key(self, key: str, reason: str) -> None:
        assert stop_reason({key: reason, "model_provider": "x"}) == reason

    @pytest.mark.parametrize(
        "reason", sorted(DECLINED_STOP_REASONS | MALFORMED_STOP_REASONS | TRUNCATING_STOP_REASONS)
    )
    def test_a_named_reason_written_twice_by_a_chunk_merge_reads_once(self, reason: str) -> None:
        first = AIMessageChunk(content="", response_metadata={"stop_reason": reason})
        second = AIMessageChunk(content="", response_metadata={"stop_reason": reason})

        assert stop_reason((first + second).response_metadata) == reason

    @pytest.mark.parametrize("metadata", [{}, {"stopReason": None}, {"stopReason": ""}, None, "x"])
    def test_no_reason_is_none(self, metadata: object) -> None:
        assert stop_reason(metadata) is None  # type: ignore[arg-type]

    def test_the_context_window_is_a_truncating_reason_with_a_name_of_its_own(self) -> None:
        assert CONTEXT_WINDOW_STOP_REASON in TRUNCATING_STOP_REASONS

    def test_the_reasons_do_not_overlap(self) -> None:
        assert not TRUNCATING_STOP_REASONS & DECLINED_STOP_REASONS
        assert not TRUNCATING_STOP_REASONS & MALFORMED_STOP_REASONS
        assert not DECLINED_STOP_REASONS & MALFORMED_STOP_REASONS


# ---------------------------------------------------------------------------
# Bedrock Converse
# ---------------------------------------------------------------------------


@pytest.fixture
def bedrock_llm():
    _bedrock_client.cache_clear()
    settings = Settings(
        _env_file=None,
        bedrock_api_key="test-bedrock-key",
        bedrock_region="us-east-2",
        bedrock_max_output_tokens=16000,
    )
    yield create_bedrock_llm("openai.gpt-6-luna", settings=settings)
    _bedrock_client.cache_clear()


def _merge(chunks) -> AIMessageChunk:
    merged = None
    for chunk in chunks:
        merged = chunk if merged is None else merged + chunk
    assert merged is not None
    return merged


class TestBedrockConverse:
    @pytest.mark.parametrize(
        ("stop", "cut_off"),
        [("max_tokens", "max_tokens"), ("end_turn", None), ("tool_use", None)],
    )
    def test_a_streamed_reply(self, bedrock_llm, stop: str, cut_off: str | None) -> None:
        Wire(bedrock_llm, converse_stream(["An answ"], stop_reason=stop), EVENT_STREAM)

        merged = _merge(bedrock_llm.stream([HumanMessage(content="hi")]))

        assert truncation_reason(merged.response_metadata) == cut_off

    def test_reasoning_that_used_the_whole_budget(self, bedrock_llm) -> None:
        """The failure ADR 0014 records: no text at all, and a normal end of stream."""
        Wire(
            bedrock_llm,
            converse_stream([], reasoning=["hmm "] * 4, stop_reason="max_tokens"),
            EVENT_STREAM,
        )

        merged = _merge(bedrock_llm.stream([HumanMessage(content="hi")]))

        assert truncation_reason(merged.response_metadata) == "max_tokens"
        assert not any(b.get("type") == "text" for b in merged.content)

    def test_a_complete_reply(self, bedrock_llm) -> None:
        import json

        body = json.dumps(
            {
                "output": {"message": {"role": "assistant", "content": [{"text": "An answ"}]}},
                "stopReason": "max_tokens",
                "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
                "metrics": {"latencyMs": 1},
            }
        ).encode()
        Wire(bedrock_llm, body, "application/json")
        bedrock_llm.streaming = False

        reply = bedrock_llm.invoke([HumanMessage(content="hi")])

        assert truncation_reason(reply.response_metadata) == "max_tokens"


# ---------------------------------------------------------------------------
# Anthropic Messages
# ---------------------------------------------------------------------------


def _anthropic_llm():
    settings = Settings(_env_file=None)
    return create_anthropic_llm("claude-haiku-4-5", api_key="sk-ant-test", settings=settings)


class TestAnthropicMessages:
    @pytest.mark.parametrize(
        ("stop", "cut_off"),
        [("max_tokens", "max_tokens"), ("end_turn", None), ("tool_use", None)],
    )
    def test_a_streamed_reply(self, stop: str, cut_off: str | None) -> None:
        with served_by(reply_with(["An answ"], stop_reason=stop)):
            merged = _merge(_anthropic_llm().stream([HumanMessage(content="hi")]))

        assert truncation_reason(merged.response_metadata) == cut_off

    def test_thinking_that_used_the_whole_budget(self) -> None:
        with served_by(reply_with([], thinking=["hmm "] * 4, stop_reason="max_tokens")):
            merged = _merge(_anthropic_llm().stream([HumanMessage(content="hi")]))

        assert truncation_reason(merged.response_metadata) == "max_tokens"
        assert not any(b.get("type") == "text" for b in merged.content)

    def test_a_complete_reply(self) -> None:
        with served_by(reply_with(["An answ"], stop_reason="max_tokens")):
            reply = _anthropic_llm().invoke([HumanMessage(content="hi")])

        assert truncation_reason(reply.response_metadata) == "max_tokens"
