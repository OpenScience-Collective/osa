"""Tests for the OpenRouter chat model: cache breakpoints, tagged citations, usage details.

Two levels, as in ``test_bedrock_llm.py``:

- ``to_message_dicts`` is a pure function; its tests need nothing but messages.
- Everything that depends on what LiteLLM and the OpenAI client actually send and
  parse runs against ``FakeOpenRouter``, a local HTTP server that speaks OpenRouter's
  chat-completions wire format. The real LiteLLM stack runs; only the network hop is
  replaced. (A real OpenRouter call is in ``TestOpenRouterLive`` of
  ``test_litellm_llm.py`` and ``tests/test_integration/test_openrouter_citations.py``,
  which need a key.)
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from src.core.services.litellm_chat import (
    TaggedCitationChatLiteLLM,
    to_message_dicts,
    usage_details,
)
from src.core.services.litellm_llm import create_openrouter_llm
from src.core.services.tagged_citations import CITATION_INSTRUCTION
from src.tools.citations import build_search_result

SCHEMA_URL = "https://hedtags.org/schema"
SENSORY_URL = "https://hedtags.org/sensory"
SCHEMA_TEXT = "HED tags are assembled from a schema. The schema is versioned."
SENSORY_TEXT = "The Sensory-event tag marks a sensory stimulus presented to the participant."

_CACHE = {"type": "ephemeral"}


@tool
def search_docs(query: str) -> str:
    """Search the documentation."""
    return query


# ---------------------------------------------------------------------------
# to_message_dicts: what a conversation looks like on the wire
# ---------------------------------------------------------------------------


class TestCacheBreakpoints:
    def test_system_message_and_last_message_carry_the_marker(self) -> None:
        result = to_message_dicts(
            [SystemMessage(content="You are helpful."), HumanMessage(content="Hello")]
        )

        assert result[0] == {
            "role": "system",
            "content": [{"type": "text", "text": "You are helpful.", "cache_control": _CACHE}],
        }
        assert result[1] == {
            "role": "user",
            "content": [{"type": "text", "text": "Hello", "cache_control": _CACHE}],
        }

    def test_middle_messages_carry_none(self) -> None:
        result = to_message_dicts(
            [
                HumanMessage(content="first"),
                AIMessage(content="second"),
                HumanMessage(content="third"),
            ]
        )
        assert result[0]["content"] == "first"
        assert result[1]["content"] == "second"
        assert result[2]["content"][0]["cache_control"] == _CACHE

    def test_every_system_message_is_marked(self) -> None:
        result = to_message_dicts(
            [SystemMessage(content="one"), SystemMessage(content="two"), HumanMessage(content="q")]
        )
        assert result[0]["content"][0]["cache_control"] == _CACHE
        assert result[1]["content"][0]["cache_control"] == _CACHE

    def test_no_markers_when_caching_is_off(self) -> None:
        result = to_message_dicts(
            [SystemMessage(content="sys"), HumanMessage(content="hi")], cache=False
        )
        assert result == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]

    def test_a_single_message_gets_no_trailing_marker(self) -> None:
        assert to_message_dicts([HumanMessage(content="Hello")])[0]["content"] == "Hello"

    def test_a_lone_system_message_is_marked_once(self) -> None:
        result = to_message_dicts([SystemMessage(content="System prompt.")])
        assert result[0]["content"] == [
            {"type": "text", "text": "System prompt.", "cache_control": _CACHE}
        ]

    def test_the_marker_lands_on_a_tool_result(self) -> None:
        """The agentic loop case: the conversation so far is re-read at the cache rate."""
        result = to_message_dicts(
            [
                SystemMessage(content="You are a HED expert."),
                HumanMessage(content="What is BCI?"),
                AIMessage(
                    content="", tool_calls=[{"id": "c1", "name": "search", "args": {"q": "BCI"}}]
                ),
                ToolMessage(content="BCI is Brain-Computer Interface.", tool_call_id="c1"),
            ]
        )
        assert result[3]["content"][0]["text"] == "BCI is Brain-Computer Interface."
        assert result[3]["content"][0]["cache_control"] == _CACHE
        assert "cache_control" not in str(result[1])

    def test_an_assistant_turn_that_only_called_tools_is_skipped(self) -> None:
        result = to_message_dicts(
            [
                SystemMessage(content="System prompt."),
                HumanMessage(content="What is BCI?"),
                AIMessage(
                    content="", tool_calls=[{"id": "c1", "name": "search", "args": {"q": "BCI"}}]
                ),
            ]
        )
        assert result[1]["content"][0]["cache_control"] == _CACHE

    def test_an_empty_tool_result_is_not_marked(self) -> None:
        result = to_message_dicts(
            [SystemMessage(content="System prompt."), ToolMessage(content="", tool_call_id="c1")]
        )
        assert result[1]["content"] == ""


class TestMessageDicts:
    def test_tool_calls_are_serialized_for_the_chat_api(self) -> None:
        result = to_message_dicts(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "call_1", "name": "search_docs", "args": {"q": "BCI"}},
                        {"id": "call_2", "name": "fetch_page", "args": {"url": "https://x.org"}},
                    ],
                )
            ]
        )

        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == ""
        calls = result[0]["tool_calls"]
        assert [c["id"] for c in calls] == ["call_1", "call_2"]
        assert all(c["type"] == "function" for c in calls)
        assert json.loads(calls[0]["function"]["arguments"]) == {"q": "BCI"}

    def test_an_assistant_turn_without_tool_calls_has_no_tool_calls_key(self) -> None:
        result = to_message_dicts([AIMessage(content="Here is the answer.")], cache=False)
        assert result == [{"role": "assistant", "content": "Here is the answer."}]

    def test_a_tool_result_carries_its_call_id(self) -> None:
        result = to_message_dicts(
            [ToolMessage(content="Found 3 documents.", tool_call_id="call_123")]
        )
        assert result == [
            {"role": "tool", "tool_call_id": "call_123", "content": "Found 3 documents."}
        ]

    def test_a_block_list_is_reduced_to_its_text(self) -> None:
        """A turn another model wrote: reasoning and tool-use blocks are not sent back."""
        blocks = [
            {"type": "thinking", "thinking": "hmm", "signature": "abc"},
            {"type": "text", "text": "The answer."},
            {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
        ]
        result = to_message_dicts([AIMessage(content=blocks)], cache=False)
        assert result[0]["content"] == "The answer."

    def test_a_full_tool_sequence(self) -> None:
        result = to_message_dicts(
            [
                SystemMessage(content="You are a HED expert."),
                HumanMessage(content="What is BCI in HED?"),
                AIMessage(
                    content="",
                    tool_calls=[{"id": "call_abc", "name": "search_docs", "args": {"q": "BCI"}}],
                ),
                ToolMessage(
                    content="BCI stands for Brain-Computer Interface.", tool_call_id="call_abc"
                ),
                AIMessage(content="BCI stands for Brain-Computer Interface in HED."),
            ]
        )
        assert [m["role"] for m in result] == ["system", "user", "assistant", "tool", "assistant"]
        assert "tool_calls" in result[2]
        assert result[3]["tool_call_id"] == "call_abc"
        assert result[4]["content"][0]["cache_control"] == _CACHE

    def test_none_input_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be None"):
            to_message_dicts(None)  # type: ignore[arg-type]

    def test_a_non_list_is_refused(self) -> None:
        with pytest.raises(TypeError, match="Expected list of messages"):
            to_message_dicts("not a list")  # type: ignore[arg-type]

    def test_a_tool_result_without_a_call_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no tool_call_id"):
            to_message_dicts([ToolMessage.model_construct(content="x", tool_call_id="")])

    def test_a_malformed_tool_call_is_refused(self) -> None:
        message = AIMessage.model_construct(content="", tool_calls=[{"id": "c1", "name": "x"}])
        with pytest.raises(ValueError, match="Malformed tool_call"):
            to_message_dicts([message])


class TestUsageDetails:
    def test_cache_and_reasoning_counts_are_picked_out(self) -> None:
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 12},
        }
        assert usage_details(usage) == {
            "input": {"cache_read": 40, "cache_creation": 20},
            "output": {"reasoning": 12},
        }

    def test_zero_and_missing_counts_are_left_out(self) -> None:
        usage = {
            "prompt_tokens_details": {"cached_tokens": 0, "text_tokens": None},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
        assert usage_details(usage) == {}
        assert usage_details(None) == {}
        assert usage_details({}) == {}

    def test_anthropic_style_cache_writes_are_read_too(self) -> None:
        assert usage_details({"cache_creation_input_tokens": 7}) == {"input": {"cache_creation": 7}}


# ---------------------------------------------------------------------------
# A fake OpenRouter, so the real LiteLLM stack runs
# ---------------------------------------------------------------------------


def _chunk(delta: dict[str, Any], finish: str | None = None, usage: dict | None = None) -> dict:
    body: dict[str, Any] = {
        "id": "gen-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "openai/gpt-6-luna",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        body["usage"] = usage
    return body


def _stream_of(*texts: str, usage: dict | None = None) -> list[dict]:
    """A streamed reply: the given text chunks, then the finish chunk (carrying usage)."""
    events = [_chunk({"role": "assistant", "content": ""})]
    events += [_chunk({"content": text}) for text in texts]
    events.append(_chunk({}, "stop", usage=usage))
    return events


def _tool_call_stream(name: str, arguments: str) -> list[dict]:
    call = {"index": 0, "id": "call_1", "type": "function", "function": {"name": name}}
    return [
        _chunk(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{**call, "function": {"name": name, "arguments": ""}}],
            }
        ),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": arguments}}]}),
        _chunk({}, "tool_calls"),
    ]


def _whole_reply(text: str, usage: dict | None = None) -> dict:
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": "openai/gpt-6-luna",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class FakeOpenRouter:
    """Serves queued replies to ``POST /api/v1/chat/completions`` and records each request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self._replies: list[Any] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (http.server's interface)
                length = int(self.headers["content-length"])
                request = json.loads(self.rfile.read(length))
                outer.requests.append(request)
                outer.headers.append({k.lower(): v for k, v in self.headers.items()})
                reply = outer._replies.pop(0)
                if request.get("stream"):
                    payload = "".join(f"data: {json.dumps(e)}\n\n" for e in reply)
                    payload += "data: [DONE]\n\n"
                    content_type = "text/event-stream"
                else:
                    payload = json.dumps(reply)
                    content_type = "application/json"
                data = payload.encode()
                self.send_response(200)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/api/v1"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def reply(self, *replies: Any) -> None:
        self._replies.extend(replies)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def openrouter(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOpenRouter]:
    server = FakeOpenRouter()
    monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
    yield server
    server.close()


def _llm(model: str = "openai/gpt-6-luna", **kwargs: Any) -> TaggedCitationChatLiteLLM:
    llm = create_openrouter_llm(model=model, api_key="sk-or-test", **kwargs)
    assert isinstance(llm, TaggedCitationChatLiteLLM)
    return llm


def _conversation(*, answer: AIMessage | None = None) -> list:
    """System prompt, a question, a search call, and its two tagged-source results."""
    messages: list = [
        SystemMessage(content="You are the HED assistant.\n\n" + CITATION_INSTRUCTION),
        HumanMessage(content="What does Sensory-event mark, and how are tags built?"),
        AIMessage(
            content="", tool_calls=[{"id": "call_1", "name": "search_docs", "args": {"query": "x"}}]
        ),
        ToolMessage(
            content=[
                build_search_result(SCHEMA_URL, "The HED schema", SCHEMA_TEXT),
                build_search_result(SENSORY_URL, "Sensory-event", SENSORY_TEXT),
            ],
            tool_call_id="call_1",
            name="search_docs",
        ),
    ]
    if answer is not None:
        messages.append(answer)
    return messages


def _text_of(content: str | list) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    )


def _sources_of(content: str | list) -> list[str]:
    if isinstance(content, str):
        return []
    return [c["source"] for b in content if isinstance(b, dict) for c in b.get("citations", [])]


ANSWER = "Tags come from a schema.[src:1] Sensory-event marks a stimulus.[src:2]"


class TestWhatIsSent:
    def test_an_anthropic_request_carries_cache_breakpoints(
        self, openrouter: FakeOpenRouter
    ) -> None:
        openrouter.reply(_stream_of("ok"))

        _llm("anthropic/claude-haiku-4.5").invoke(_conversation())

        messages = openrouter.requests[0]["messages"]
        assert messages[0]["content"][0]["cache_control"] == _CACHE
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["content"][0]["cache_control"] == _CACHE

    def test_a_model_that_cannot_use_the_markers_is_not_sent_them(
        self, openrouter: FakeOpenRouter
    ) -> None:
        """LiteLLM drops cache_control for non-Anthropic slugs; nothing here depends on it."""
        openrouter.reply(_stream_of("ok"))

        _llm("openai/gpt-oss-120b").invoke(_conversation())

        assert "cache_control" not in json.dumps(openrouter.requests[0]["messages"])

    def test_caching_off_sends_no_markers(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_stream_of("ok"))

        _llm("anthropic/claude-haiku-4.5", enable_caching=False).invoke(_conversation())

        assert "cache_control" not in json.dumps(openrouter.requests[0]["messages"])

    def test_search_results_arrive_as_tagged_text(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_stream_of("ok"))

        _llm(enable_caching=False).invoke(_conversation())

        tool_message = openrouter.requests[0]["messages"][-1]
        assert tool_message["role"] == "tool"
        text = tool_message["content"]
        assert isinstance(text, str), "some providers take nothing but a string here"
        assert "[src:1] The HED schema" in text
        assert f"Source: {SCHEMA_URL}" in text
        assert "[src:2] Sensory-event" in text
        assert "search_result" not in text

    def test_an_earlier_cited_reply_is_shown_with_its_tags(
        self, openrouter: FakeOpenRouter
    ) -> None:
        openrouter.reply(_stream_of("ok"))
        earlier = AIMessage(
            content=[
                {
                    "type": "text",
                    "text": "Tags come from a schema.",
                    "citations": [{"type": "search_result_location", "source": SCHEMA_URL}],
                }
            ]
        )

        _llm(enable_caching=False).invoke(
            [*_conversation(answer=earlier), HumanMessage(content="And the schema version?")]
        )

        assistant = openrouter.requests[0]["messages"][-2]
        assert assistant == {"role": "assistant", "content": "Tags come from a schema.[src:1]"}

    def test_tools_are_sent_and_a_tool_call_comes_back(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_tool_call_stream("search_docs", '{"query": "sensory"}'))

        reply = _llm().bind_tools([search_docs]).invoke([HumanMessage(content="Find sensory")])

        assert openrouter.requests[0]["tools"][0]["function"]["name"] == "search_docs"
        assert reply.tool_calls[0]["name"] == "search_docs"
        assert reply.tool_calls[0]["args"] == {"query": "sensory"}


class TestTagsBecomeCitations:
    """The reply a reader gets: no tag text, and a citation for each tag that named a source."""

    def _check(self, message: AIMessage) -> None:
        assert (
            _text_of(message.content) == "Tags come from a schema. Sensory-event marks a stimulus."
        )
        assert "[src" not in _text_of(message.content)
        assert _sources_of(message.content) == [SCHEMA_URL, SENSORY_URL]

    def test_a_streamed_reply(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_stream_of(ANSWER))

        merged = None
        for chunk in _llm().stream(_conversation()):
            merged = chunk if merged is None else merged + chunk

        self._check(merged)

    @pytest.mark.parametrize("size", [1, 2, 3, 5, 8, 13])
    def test_however_the_stream_is_cut(self, openrouter: FakeOpenRouter, size: int) -> None:
        openrouter.reply(_stream_of(*[ANSWER[i : i + size] for i in range(0, len(ANSWER), size)]))

        seen: list[str] = []
        merged = None
        for chunk in _llm().stream(_conversation()):
            seen.append(_text_of(chunk.content))
            merged = chunk if merged is None else merged + chunk

        assert not any("[src" in text for text in seen), seen
        self._check(merged)

    async def test_an_async_streamed_reply(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_stream_of(*[ANSWER[i : i + 4] for i in range(0, len(ANSWER), 4)]))

        merged = None
        async for chunk in _llm().astream(_conversation()):
            merged = chunk if merged is None else merged + chunk

        self._check(merged)

    def test_invoke_gives_the_same_answer_as_streaming(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_stream_of(ANSWER))

        self._check(_llm().invoke(_conversation()))

    async def test_ainvoke_gives_the_same_answer_as_streaming(
        self, openrouter: FakeOpenRouter
    ) -> None:
        openrouter.reply(_stream_of(ANSWER))

        self._check(await _llm().ainvoke(_conversation()))

    def test_a_complete_reply_from_a_non_streaming_model(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_whole_reply(ANSWER))
        llm = _llm()
        llm.streaming = False

        self._check(llm.invoke(_conversation()))

    async def test_no_tag_reaches_a_token_callback(self, openrouter: FakeOpenRouter) -> None:
        """LangGraph's astream_events reads tokens from the callbacks, not from the return."""
        openrouter.reply(_stream_of(*[ANSWER[i : i + 3] for i in range(0, len(ANSWER), 3)]))

        tokens: list[str] = []
        async for event in _llm().astream_events(_conversation()):
            if event["event"] == "on_chat_model_stream":
                tokens.append(_text_of(event["data"]["chunk"].content))

        assert tokens
        assert not any("[src" in token for token in tokens)
        assert "".join(tokens) == "Tags come from a schema. Sensory-event marks a stimulus."

    def test_a_tag_that_names_no_source_is_dropped(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_stream_of("A claim.[src:9] Another.[src:1]"))

        reply = _llm().invoke(_conversation())

        assert _text_of(reply.content) == "A claim. Another."
        assert _sources_of(reply.content) == [SCHEMA_URL]

    def test_a_reply_without_tags_is_returned_as_written(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_stream_of("No sources ", "were used."))

        reply = _llm().invoke(_conversation())

        assert _text_of(reply.content) == "No sources were used."
        assert _sources_of(reply.content) == []

    def test_a_tool_call_alongside_text_survives(self, openrouter: FakeOpenRouter) -> None:
        call = {
            "index": 0,
            "id": "call_9",
            "type": "function",
            "function": {"name": "search_docs", "arguments": '{"query": "y"}'},
        }
        openrouter.reply(
            [
                _chunk({"role": "assistant", "content": "Let me check.[src:1]"}),
                _chunk({"tool_calls": [call]}),
                _chunk({}, "tool_calls"),
            ]
        )

        reply = _llm().bind_tools([search_docs]).invoke(_conversation())

        assert reply.tool_calls[0]["name"] == "search_docs"
        assert _text_of(reply.content) == "Let me check."
        assert _sources_of(reply.content) == [SCHEMA_URL]


class TestUsageMetadata:
    USAGE = {
        "prompt_tokens": 100,
        "completion_tokens": 30,
        "total_tokens": 130,
        "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 20},
        "completion_tokens_details": {"reasoning_tokens": 12},
    }

    def _check(self, message: AIMessage) -> None:
        usage = message.usage_metadata
        assert (usage["input_tokens"], usage["output_tokens"]) == (100, 30)
        assert usage["input_token_details"] == {"cache_read": 40, "cache_creation": 20}
        assert usage["output_token_details"] == {"reasoning": 12}

    def test_a_streamed_reply(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_stream_of("Hello", usage=self.USAGE))

        self._check(_llm().invoke([HumanMessage(content="hi")]))

    async def test_an_async_streamed_reply(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_stream_of("Hello", usage=self.USAGE))

        self._check(await _llm().ainvoke([HumanMessage(content="hi")]))

    def test_a_complete_reply(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(_whole_reply("Hello", usage=self.USAGE))
        llm = _llm()
        llm.streaming = False

        self._check(llm.invoke([HumanMessage(content="hi")]))

    def test_the_metrics_price_the_cache_tokens(self, openrouter: FakeOpenRouter) -> None:
        """The reason these details matter: cache reads are billed at a tenth."""
        from src.metrics.db import extract_token_usage

        openrouter.reply(_stream_of("Hello", usage=self.USAGE))
        reply = _llm().invoke([HumanMessage(content="hi")])

        usage = extract_token_usage({"messages": [reply]})

        assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_creation_tokens) == (
            100,
            40,
            20,
        )

    def test_a_reply_with_no_details_has_plain_totals(self, openrouter: FakeOpenRouter) -> None:
        openrouter.reply(
            _stream_of(
                "Hello", usage={"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}
            )
        )

        usage = _llm().invoke([HumanMessage(content="hi")]).usage_metadata

        assert (usage["input_tokens"], usage["output_tokens"]) == (9, 3)
        assert not usage.get("input_token_details")
        assert not usage.get("output_token_details")

    def test_the_carrier_field_does_not_leak_into_the_message(
        self, openrouter: FakeOpenRouter
    ) -> None:
        openrouter.reply(_stream_of("Hello", usage=self.USAGE))

        reply = _llm().invoke([HumanMessage(content="hi")])

        assert "osa_usage_details" not in json.dumps(reply.additional_kwargs, default=str)


def test_the_module_is_not_imported_until_an_openrouter_model_is_built() -> None:
    """langchain_litellm takes about a second to import; the API process should not pay
    for it unless a request routes to OpenRouter."""
    import subprocess
    import sys

    code = "import sys; import src.api.routers.community; print('langchain_litellm' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip().splitlines()[-1] == "False"
