"""The `tool_call` SSE event from the Claude Messages API's own wire format (#538).

`test_tool_call_streaming.py` feeds the graph chunks built the way langchain-anthropic
yields them. This feeds langchain-anthropic itself: the server-sent events the Messages
API streams for a reply that writes tool calls, as bytes, read by the real
`ChatAnthropic` that `create_anthropic_llm` builds (the Anthropic SDK's stream decoder
and langchain-anthropic's chunk conversion), then LangGraph and the router's SSE
assembly for `/chat`. An upgrade that changes the chunks the adapter yields (the name
arriving later, the index going away) fails here, where hand-built chunks would not
notice.

What stands in: the HTTP transport under the Anthropic SDK, patched where
langchain-anthropic builds its client (`tests/test_api/test_byok_http.py` explains why
there and not with respx; the SDK builds its client on the separate ``httpx2`` package), and the router's
`create_community_assistant`, as in the other stream tests. If the patch point moves,
the request goes to the real API with a fake key, fails, and these tests fail with it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import httpx2
import pytest

from src.api.routers.community import AssistantWithMetrics, ChatSession, _stream_chat_response
from src.assistants.community import CommunityAssistant
from src.core.services.anthropic_llm import create_anthropic_llm
from src.core.services.anthropic_models import HAIKU
from tests.test_api.test_tool_call_streaming import (
    COMMUNITY,
    SEARCH_ARGS,
    SECRET_QUERY,
    _config,
    nemar_search_datasets,
    retrieve_toolcallstream_docs,
)

MODEL = HAIKU
LEAD_TEXT = "Let me search for that."
ANSWER = "Three datasets match: nm000103, nm000132 and nm000140."


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def _message_start(message_id: str) -> str:
    return _event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 120, "output_tokens": 1},
            },
        },
    )


def _text_block(index: int, text: str) -> str:
    return (
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            },
        )
        + "".join(
            _event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": text[i : i + 8]},
                },
            )
            for i in range(0, len(text), 8)
        )
        + _event("content_block_stop", {"type": "content_block_stop", "index": index})
    )


def _tool_use_block(index: int, call_id: str, name: str, args: dict[str, Any]) -> str:
    """A tool_use block as the API streams it: the name and id in the start event, the
    input as partial JSON, first an empty piece, then a few characters at a time."""
    payload = json.dumps(args)
    pieces = [""] + [payload[i : i + 9] for i in range(0, len(payload), 9)]
    return (
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
            },
        )
        + "".join(
            _event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": piece},
                },
            )
            for piece in pieces
        )
        + _event("content_block_stop", {"type": "content_block_stop", "index": index})
    )


def _message_end(stop_reason: str) -> str:
    return _event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 60},
        },
    ) + _event("message_stop", {"type": "message_stop"})


def _tool_turn(calls: list[tuple[str, str, dict[str, Any]]]) -> bytes:
    body = (
        _message_start("msg_tools") + _event("ping", {"type": "ping"}) + _text_block(0, LEAD_TEXT)
    )
    for offset, (call_id, name, args) in enumerate(calls, start=1):
        body += _tool_use_block(offset, call_id, name, args)
    return (body + _message_end("tool_use")).encode()


def _answer_turn() -> bytes:
    return (
        _message_start("msg_answer") + _text_block(0, ANSWER) + _message_end("end_turn")
    ).encode()


class _Wire:
    """Serves the queued response bodies in order, and keeps each request it got."""

    def __init__(self, bodies: list[bytes]) -> None:
        self.bodies = list(bodies)
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        if not self.bodies:
            return httpx2.Response(
                500, json={"type": "error", "error": {"message": "no more turns"}}
            )
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=self.bodies.pop(0)
        )

    def client_factory(self, cls: type) -> Callable[..., Any]:
        def build(*, base_url: str | None = None, **_kwargs: object) -> Any:
            return cls(
                base_url=base_url or "https://api.anthropic.com",
                transport=httpx2.MockTransport(self.handler),
            )

        return build


async def _chat_over_the_wire(wire: _Wire) -> list[dict]:
    with (
        patch(
            "langchain_anthropic.chat_models._get_default_httpx_client",
            side_effect=wire.client_factory(httpx2.Client),
        ),
        patch(
            "langchain_anthropic.chat_models._get_default_async_httpx_client",
            side_effect=wire.client_factory(httpx2.AsyncClient),
        ),
    ):
        llm = create_anthropic_llm(model=MODEL, api_key="sk-ant-fake-test-key", thinking=None)
        assistant = CommunityAssistant(
            model=llm,
            config=_config(),
            preload_docs=False,
            additional_tools=[nemar_search_datasets, retrieve_toolcallstream_docs],
            declared_client_tools={"execute_code"},
        )
        wrapped = AssistantWithMetrics(assistant=assistant, model=MODEL, key_source="byok")
        session = ChatSession("sess-anthropic-wire", COMMUNITY)
        session.add_user_message("Which datasets are about attention?")
        with patch("src.api.routers.community.create_community_assistant", return_value=wrapped):
            events = []
            async for line in _stream_chat_response(
                COMMUNITY, session, None, None, None, declared_client_tools={"execute_code"}
            ):
                assert line.startswith("data: ")
                events.append(json.loads(line[len("data: ") :]))
            return events


def _names(events: list[dict]) -> list[str]:
    return [event["event"] for event in events]


class TestTheClaudeWireFormat:
    @pytest.mark.asyncio
    async def test_parallel_calls_after_text_are_each_announced_once_before_they_run(
        self,
    ) -> None:
        wire = _Wire(
            [
                _tool_turn(
                    [
                        ("toolu_01wiresearch", "nemar_search_datasets", SEARCH_ARGS),
                        ("toolu_01wiredocs", "retrieve_toolcallstream_docs", {"url": "https://x"}),
                    ]
                ),
                _answer_turn(),
            ]
        )

        events = await _chat_over_the_wire(wire)

        names = _names(events)
        assert [e for e in events if e["event"] == "error"] == [], events
        calls = [e for e in events if e["event"] == "tool_call"]
        assert calls == [
            {"event": "tool_call", "name": "nemar_search_datasets"},
            {"event": "tool_call", "name": "retrieve_toolcallstream_docs"},
        ]
        # Each announced while the model writes it: after the text it wrote first, and
        # before either tool runs.
        first_content = names.index("content")
        call_positions = [i for i, name in enumerate(names) if name == "tool_call"]
        first_start = names.index("tool_start")
        assert first_content < call_positions[0] < call_positions[1] < first_start
        starts = [e["name"] for e in events if e["event"] == "tool_start"]
        assert sorted(starts) == ["nemar_search_datasets", "retrieve_toolcallstream_docs"]
        # The name and nothing else: the arguments reach the tool, never the event.
        assert SECRET_QUERY not in json.dumps(calls)
        search_start = next(
            e for e in events if e["event"] == "tool_start" and e["name"] == "nemar_search_datasets"
        )
        assert search_start["input"] == SEARCH_ARGS
        # Both turns went over the wire, and the second carried both results back.
        assert len(wire.requests) == 2
        results = [
            block
            for message in wire.requests[1]["messages"]
            if isinstance(message["content"], list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]
        assert {block["tool_use_id"] for block in results} == {
            "toolu_01wiresearch",
            "toolu_01wiredocs",
        }
        done = next(e for e in events if e["event"] == "done")
        assert done["content"].endswith(ANSWER)

    @pytest.mark.asyncio
    async def test_a_code_call_is_announced_before_the_run_parks_on_its_tool_request(
        self,
    ) -> None:
        code = {"code": "import numpy as np\n" * 12, "description": "Band power"}
        wire = _Wire([_tool_turn([("toolu_01wirecode", "execute_code", code)])])

        events = await _chat_over_the_wire(wire)

        names = _names(events)
        assert [e for e in events if e["event"] == "tool_call"] == [
            {"event": "tool_call", "name": "execute_code"}
        ]
        assert "tool_start" not in names
        assert names.index("tool_call") < names.index("tool_request") == len(names) - 1
        request = events[-1]
        assert request["tool"] == "execute_code"
        assert request["args"]["code"] == code["code"]
        assert len(wire.requests) == 1
