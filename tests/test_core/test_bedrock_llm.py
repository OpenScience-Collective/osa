"""Tests for the Bedrock chat models.

Nothing here reaches AWS. Requests are intercepted at botocore's transport hook
(``before-send``), after the client has serialized, authenticated and addressed
them, and answered with fixture bytes: the same idea as an ``httpx`` response
fixture, one layer down. So what is asserted about a request (URL, bearer header,
body) is what the real client would have sent, and the reply goes through the real
parser, including the binary event stream used for streaming.
"""

import json
import struct
import zlib
from typing import Any

import pytest
from botocore.awsrequest import AWSResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.api.config import Settings
from src.core.services.anthropic_models import BEDROCK_MODELS
from src.core.services.bedrock_llm import (
    TaggedCitationChatBedrock,
    _bedrock_client,
    create_bedrock_llm,
)
from src.core.services.tagged_citations import CITATION_INSTRUCTION
from src.tools.citations import build_search_result

FAKE_KEY = "test-bedrock-key"


@pytest.fixture(autouse=True)
def _fresh_clients():
    """Clients are cached per (service, Region, key); a test hooks the one it gets, so
    each test starts from clients nobody has hooked."""
    _bedrock_client.cache_clear()
    yield
    _bedrock_client.cache_clear()


def _settings(**overrides: object) -> Settings:
    """Settings with explicit Bedrock fields, so no test reads the developer's .env."""
    values: dict[str, object] = {
        "bedrock_api_key": FAKE_KEY,
        "bedrock_region": "us-east-2",
        "bedrock_max_output_tokens": 16000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


class _Raw:
    """The minimum urllib3-shaped body botocore reads a response from."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def stream(self, *_args: Any, **_kwargs: Any):
        yield self._body

    def read(self, *_args: Any, **_kwargs: Any) -> bytes:
        return self._body

    def close(self) -> None:
        """Nothing to release: the body is already in memory."""


class _Wire:
    """Captures what a client sends and answers with a fixed response."""

    def __init__(self, llm: TaggedCitationChatBedrock, body: bytes, content_type: str) -> None:
        self.requests: list[Any] = []
        self._body = body
        self._content_type = content_type
        llm.client.meta.events.register("before-send.bedrock-runtime.*", self._answer)

    def _answer(self, request: Any, **_kwargs: Any) -> AWSResponse:
        self.requests.append(request)
        return AWSResponse(request.url, 200, {"content-type": self._content_type}, _Raw(self._body))

    @property
    def sent(self) -> Any:
        return self.requests[-1]

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.sent.body)


def _converse_reply(text: str) -> bytes:
    return json.dumps(
        {
            "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
                "cacheReadInputTokens": 6,
                "cacheWriteInputTokens": 0,
            },
            "metrics": {"latencyMs": 1},
        }
    ).encode()


def _frame(event_type: str, payload: dict[str, Any]) -> bytes:
    """One AWS event-stream message: the framing ConverseStream replies in."""

    def header(name: str, value: str) -> bytes:
        raw_name, raw_value = name.encode(), value.encode()
        return (
            struct.pack("B", len(raw_name))
            + raw_name
            + b"\x07"
            + struct.pack(">H", len(raw_value))
            + raw_value
        )

    headers = (
        header(":event-type", event_type)
        + header(":content-type", "application/json")
        + header(":message-type", "event")
    )
    body = json.dumps(payload).encode()
    total = 12 + len(headers) + len(body) + 4
    prelude = struct.pack(">II", total, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude))
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


def _stream_reply(text_deltas: list[str], block_index: int = 0) -> bytes:
    frames = [_frame("messageStart", {"role": "assistant"})]
    frames += [
        _frame(
            "contentBlockDelta",
            {"contentBlockIndex": block_index, "delta": {"text": delta}},
        )
        for delta in text_deltas
    ]
    frames += [
        _frame("contentBlockStop", {"contentBlockIndex": block_index}),
        _frame("messageStop", {"stopReason": "end_turn"}),
        _frame(
            "metadata",
            {
                "usage": {"inputTokens": 20, "outputTokens": 7, "totalTokens": 27},
                "metrics": {"latencyMs": 1},
            },
        ),
    ]
    return b"".join(frames)


def _conversation() -> list:
    results = [
        build_search_result(
            "https://hedtags.org/sensory",
            "Sensory-event",
            "The Sensory-event tag marks a sensory stimulus presented to the participant.",
        ),
        build_search_result(
            "https://hedtags.org/basics",
            "HED basics",
            "Parentheses group tags that describe one thing together.",
        ),
    ]
    return [
        SystemMessage(content="You are the HED assistant.\n\n" + CITATION_INSTRUCTION),
        HumanMessage(content="What does Sensory-event mark?"),
        AIMessage(content="", tool_calls=[{"name": "search_docs", "args": {"q": "s"}, "id": "c1"}]),
        ToolMessage(content=results, tool_call_id="c1", name="search_docs"),
    ]


def _search_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search the docs",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
    }


class TestCreateBedrockLlm:
    def test_builds_the_tagged_citation_model(self) -> None:
        llm = create_bedrock_llm("openai.gpt-6-luna", settings=_settings())
        assert isinstance(llm, TaggedCitationChatBedrock)

    @pytest.mark.parametrize("model_id", sorted(BEDROCK_MODELS))
    def test_calls_the_models_invoke_id_in_its_region(self, model_id: str) -> None:
        spec = BEDROCK_MODELS[model_id]
        llm = create_bedrock_llm(model_id, settings=_settings())
        assert llm.model_id == spec.invoke_id
        assert llm.client.meta.region_name == (spec.region or "us-east-2")

    def test_a_model_pinned_to_a_region_ignores_the_settings_region(self) -> None:
        llm = create_bedrock_llm("qwen.qwen3-next-80b-a3b", settings=_settings())
        assert llm.client.meta.region_name == "us-east-1"

    def test_the_settings_region_is_used_otherwise(self) -> None:
        llm = create_bedrock_llm(
            "openai.gpt-oss-120b", settings=_settings(bedrock_region="us-west-2")
        )
        assert llm.client.meta.region_name == "us-west-2"

    def test_luna_runs_at_maximum_effort(self) -> None:
        llm = create_bedrock_llm("openai.gpt-6-luna", settings=_settings())
        assert llm.additional_model_request_fields == {"reasoning": {"effort": "max"}}

    def test_max_tokens_comes_from_settings_unless_given(self) -> None:
        settings = _settings(bedrock_max_output_tokens=9000)
        assert create_bedrock_llm("openai.gpt-oss-120b", settings=settings).max_tokens == 9000
        given = create_bedrock_llm("openai.gpt-oss-120b", max_tokens=123, settings=settings)
        assert given.max_tokens == 123

    def test_it_streams(self) -> None:
        """The router streams with astream_events, which needs the model to stream."""
        assert create_bedrock_llm("openai.gpt-oss-120b", settings=_settings()).streaming is True

    def test_temperature_is_sent_only_to_models_that_take_it(self) -> None:
        settings = _settings()
        assert (
            create_bedrock_llm("openai.gpt-6-luna", temperature=0.1, settings=settings).temperature
            is None
        )
        assert (
            create_bedrock_llm(
                "openai.gpt-oss-120b", temperature=0.1, settings=settings
            ).temperature
            == 0.1
        )
        assert (
            create_bedrock_llm(
                "qwen.qwen3-next-80b-a3b", temperature=0.1, settings=settings
            ).temperature
            == 0.1
        )

    def test_a_claude_model_is_refused(self) -> None:
        with pytest.raises(ValueError, match="create_anthropic_llm"):
            create_bedrock_llm("claude-haiku-4-5", settings=_settings())

    def test_a_missing_key_is_a_clear_error(self) -> None:
        with pytest.raises(RuntimeError, match="AWS_BEARER_TOKEN_BEDROCK"):
            create_bedrock_llm("openai.gpt-6-luna", settings=_settings(bedrock_api_key=None))

    def test_the_key_is_not_written_to_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The key belongs to one client; a process-wide variable would leak it to others."""
        monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
        create_bedrock_llm("openai.gpt-6-luna", settings=_settings())
        import os

        assert "AWS_BEARER_TOKEN_BEDROCK" not in os.environ


class TestClientsAreReused:
    def test_the_same_key_and_region_share_one_client(self) -> None:
        first = create_bedrock_llm("openai.gpt-6-luna", settings=_settings())
        second = create_bedrock_llm("openai.gpt-6-luna", settings=_settings())

        assert first.client is second.client
        assert first is not second, "each request still gets a model of its own"

    def test_models_in_one_region_share_the_client(self) -> None:
        luna = create_bedrock_llm("openai.gpt-6-luna", settings=_settings())
        oss = create_bedrock_llm("openai.gpt-oss-120b", settings=_settings())
        assert luna.client is oss.client

    def test_another_region_or_key_gets_its_own_client(self) -> None:
        base = create_bedrock_llm("openai.gpt-6-luna", settings=_settings())
        other_region = create_bedrock_llm(
            "openai.gpt-6-luna", settings=_settings(bedrock_region="us-west-2")
        )
        other_key = create_bedrock_llm(
            "openai.gpt-6-luna", settings=_settings(bedrock_api_key="another-key")
        )
        assert other_region.client is not base.client
        assert other_key.client is not base.client


class TestTheRequestOnTheWire:
    def _llm(self, model_id: str = "openai.gpt-6-luna", **settings: object):
        """A model answering with one complete Converse reply.

        Models built by ``create_bedrock_llm`` stream every call, so streaming is
        switched off here to exercise the complete-reply path (``_generate``);
        ``TestStreaming`` covers the streamed one.
        """
        llm = create_bedrock_llm(model_id, settings=_settings(**settings))
        llm.streaming = False
        return llm

    def test_authenticates_with_the_bearer_key_and_signs_nothing(self) -> None:
        """A bearer request carries the key as is; there is no SigV4 signature to build.

        Construction is also what keeps a developer's `aws login` default profile
        from breaking the client: the ambient credential chain is never walked.
        """
        llm = self._llm()
        wire = _Wire(llm, _converse_reply("OK"), "application/json")

        llm.invoke([HumanMessage(content="hi")])

        authorization = wire.sent.headers["Authorization"]
        if isinstance(authorization, bytes):
            authorization = authorization.decode()
        assert authorization == f"Bearer {FAKE_KEY}"
        assert "X-Amz-Date" not in wire.sent.headers

    def test_addresses_the_invoke_id_in_the_models_region(self) -> None:
        llm = self._llm("qwen.qwen3-next-80b-a3b")
        wire = _Wire(llm, _converse_reply("OK"), "application/json")

        llm.invoke([HumanMessage(content="hi")])

        assert wire.sent.url.startswith(
            "https://bedrock-runtime.us-east-1.amazonaws.com/model/qwen.qwen3-next-80b-a3b/converse"
        )

    def test_sends_luna_its_effort_and_no_sampling_parameter(self) -> None:
        llm = self._llm()
        wire = _Wire(llm, _converse_reply("OK"), "application/json")

        llm.invoke([HumanMessage(content="hi")])

        assert wire.body["additionalModelRequestFields"] == {"reasoning": {"effort": "max"}}
        assert "temperature" not in wire.body.get("inferenceConfig", {})
        assert "cachePoint" not in json.dumps(wire.body)

    def test_search_results_go_out_as_tagged_text(self) -> None:
        llm = self._llm()
        wire = _Wire(llm, _converse_reply("OK"), "application/json")

        llm.bind_tools([_search_tool()]).invoke(_conversation())

        sent = json.dumps(wire.body)
        assert "searchResult" not in sent
        assert "[src:1] Sensory-event" in sent
        assert "[src:2] HED basics" in sent

    def test_a_complete_reply_comes_back_with_citations_and_no_tags(self) -> None:
        llm = self._llm()
        _Wire(
            llm,
            _converse_reply("It marks a stimulus.[src:1] Group them.[src:2]"),
            "application/json",
        )

        reply = llm.bind_tools([_search_tool()]).invoke(_conversation())

        assert [b["text"] for b in reply.content] == ["It marks a stimulus.", " Group them."]
        assert [b["citations"][0]["source"] for b in reply.content] == [
            "https://hedtags.org/sensory",
            "https://hedtags.org/basics",
        ]

    def test_cache_tokens_reach_usage_metadata_where_the_metrics_read_them(self) -> None:
        llm = self._llm()
        _Wire(llm, _converse_reply("OK"), "application/json")

        reply = llm.invoke([HumanMessage(content="hi")])

        assert reply.usage_metadata["input_tokens"] == 16  # 10 fresh + 6 read, the true total
        assert reply.usage_metadata["input_token_details"]["cache_read"] == 6


class TestStreaming:
    def _stream(self, deltas: list[str]) -> tuple[list, Any]:
        llm = create_bedrock_llm("openai.gpt-6-luna", settings=_settings())
        _Wire(llm, _stream_reply(deltas), "application/vnd.amazon.eventstream")
        merged = None
        chunks = []
        for chunk in llm.bind_tools([_search_tool()]).stream(_conversation()):
            chunks.append(chunk)
            merged = chunk if merged is None else merged + chunk
        return chunks, merged

    def test_tags_are_cut_out_wherever_the_stream_splits_them(self) -> None:
        deltas = ["It marks a stim", "ulus.[sr", "c:1] Group th", "em.[src:2", "]", " Done."]
        _, merged = self._stream(deltas)

        text = "".join(b.get("text", "") for b in merged.content)
        assert "[src" not in text
        assert text == "It marks a stimulus. Group them. Done."

    def test_each_cited_claim_ends_up_in_its_own_block(self) -> None:
        deltas = ["It marks a stimulus.[src:1]", " Group them.[src:2]", " Done."]
        _, merged = self._stream(deltas)

        blocks = [b for b in merged.content if b.get("type") == "text"]
        assert [b["text"] for b in blocks] == ["It marks a stimulus.", " Group them.", " Done."]
        assert [len(b.get("citations", [])) for b in blocks] == [1, 1, 0]

    def test_the_answer_streams_before_it_ends(self) -> None:
        """Text is released as it arrives, not held until a tag shows up."""
        chunks, _ = self._stream(["First part. ", "Second part. ", "Third part."])
        released = [
            c for c in chunks if any(b.get("text") for b in c.content if isinstance(b, dict))
        ]
        assert len(released) >= 3

    def test_usage_arrives_with_the_stream(self) -> None:
        _, merged = self._stream(["Hello."])
        assert merged.usage_metadata["input_tokens"] == 20
        assert merged.usage_metadata["output_tokens"] == 7

    def test_a_tag_naming_no_source_never_reaches_the_reader(self) -> None:
        _, merged = self._stream(["A claim.[src:9] Another.[src:1]"])
        text = "".join(b.get("text", "") for b in merged.content)
        assert "[src" not in text
        cited = [c["source"] for b in merged.content for c in b.get("citations", [])]
        assert cited == ["https://hedtags.org/sensory"]


class TestTokenCallbacks:
    """LangGraph's astream_events reads tokens from callbacks, not from what stream() returns."""

    async def _events(self, deltas: list[str]) -> list[str]:
        llm = create_bedrock_llm("openai.gpt-6-luna", settings=_settings())
        _Wire(llm, _stream_reply(deltas), "application/vnd.amazon.eventstream")
        tokens: list[str] = []
        async for event in llm.bind_tools([_search_tool()]).astream_events(_conversation()):
            if event["event"] == "on_chat_model_stream":
                content = event["data"]["chunk"].content
                tokens.append(
                    content
                    if isinstance(content, str)
                    else "".join(b.get("text", "") for b in content if isinstance(b, dict))
                )
        return tokens

    async def test_no_tag_reaches_a_token_callback(self) -> None:
        tokens = await self._events(
            ["It marks a stim", "ulus.[sr", "c:1] Group th", "em.[src:2", "]", " Done."]
        )

        assert tokens
        assert not any("[src" in token for token in tokens), tokens
        assert "".join(tokens) == "It marks a stimulus. Group them. Done."


class TestReplayingHistory:
    """A tool loop and a model switch both send earlier turns back to the model."""

    def _history(self) -> list:
        """A turn a Bedrock model wrote: reasoning, then a tool call."""
        earlier_turn = AIMessage(
            content=[
                {"type": "reasoning_content", "reasoning_content": {"text": "I should search."}},
                {"type": "thinking", "thinking": "", "signature": "from-a-claude-turn"},
                {"type": "text", "text": "Let me look that up."},
            ],
            tool_calls=[{"name": "search_docs", "args": {"q": "s"}, "id": "c1"}],
        )
        return [
            HumanMessage(content="What does Sensory-event mark?"),
            earlier_turn,
            ToolMessage(content="It marks a stimulus.", tool_call_id="c1", name="search_docs"),
        ]

    def test_reasoning_is_not_sent_back_on_a_complete_reply(self) -> None:
        """Luna answers 400 to reasoning text in an assistant turn, so a second call fails."""
        llm = create_bedrock_llm("openai.gpt-6-luna", settings=_settings())
        llm.streaming = False
        wire = _Wire(llm, _converse_reply("OK"), "application/json")

        llm.bind_tools([_search_tool()]).invoke(self._history())

        sent = json.dumps(wire.body["messages"])
        assert "reasoningContent" not in sent
        assert "Let me look that up." in sent

    def test_reasoning_is_not_sent_back_on_a_streamed_reply(self) -> None:
        llm = create_bedrock_llm("openai.gpt-oss-120b", settings=_settings())
        wire = _Wire(llm, _stream_reply(["OK"]), "application/vnd.amazon.eventstream")

        list(llm.bind_tools([_search_tool()]).stream(self._history()))

        assert "reasoningContent" not in json.dumps(wire.body["messages"])

    def test_the_callers_history_is_left_alone(self) -> None:
        history = self._history()
        llm = create_bedrock_llm("openai.gpt-oss-120b", settings=_settings())
        _Wire(llm, _stream_reply(["OK"]), "application/vnd.amazon.eventstream")

        list(llm.bind_tools([_search_tool()]).stream(history))

        assert [b["type"] for b in history[1].content] == ["reasoning_content", "thinking", "text"]
