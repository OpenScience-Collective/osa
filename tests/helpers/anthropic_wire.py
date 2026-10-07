"""Answer the Anthropic Messages API at the HTTP layer, for the real client stack.

The Anthropic SDK builds its client on a separate package, ``httpx2``, which ``respx`` does
not see (``tests/test_api/test_byok_http.py`` explains). So the transport is replaced where
``langchain-anthropic`` builds its client, the way
``tests/test_api/test_tool_call_anthropic_wire.py`` does, and everything above it
(``create_anthropic_llm``, ``langchain-anthropic``, the SDK's stream parser) runs as in
production. If the patch point moves, the request goes to the real API with a fake key,
fails with a 401, and the tests that use this fail with it.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import httpx2

_USAGE = {"input_tokens": 25, "output_tokens": 12}


def _event(kind: str, payload: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **payload})}\n\n"


def message_stream(
    text_deltas: list[str],
    *,
    stop_reason: str = "end_turn",
    thinking: list[str] | None = None,
    usage: dict[str, int] | None = None,
) -> bytes:
    """A streamed Messages reply: optional thinking, then text, then how it stopped."""
    counts = usage or _USAGE
    message = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": counts["input_tokens"], "output_tokens": 1},
    }
    events = [_event("message_start", {"message": message})]
    index = 0
    if thinking:
        events.append(
            _event(
                "content_block_start",
                {"index": index, "content_block": {"type": "thinking", "thinking": ""}},
            )
        )
        for piece in thinking:
            events.append(
                _event(
                    "content_block_delta",
                    {"index": index, "delta": {"type": "thinking_delta", "thinking": piece}},
                )
            )
        events.append(
            _event(
                "content_block_delta",
                {"index": index, "delta": {"type": "signature_delta", "signature": "sig"}},
            )
        )
        events.append(_event("content_block_stop", {"index": index}))
        index += 1
    if text_deltas:
        events.append(
            _event(
                "content_block_start",
                {"index": index, "content_block": {"type": "text", "text": ""}},
            )
        )
        for delta in text_deltas:
            events.append(
                _event(
                    "content_block_delta",
                    {"index": index, "delta": {"type": "text_delta", "text": delta}},
                )
            )
        events.append(_event("content_block_stop", {"index": index}))
    events.append(
        _event(
            "message_delta",
            {
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": counts["output_tokens"]},
            },
        )
    )
    events.append(_event("message_stop", {}))
    return "".join(events).encode()


def message_body(
    text: str, *, stop_reason: str = "end_turn", usage: dict[str, int] | None = None
) -> dict[str, Any]:
    """A complete (non-streamed) Messages reply."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or _USAGE,
    }


def reply_with(
    text_deltas: list[str],
    *,
    stop_reason: str = "end_turn",
    thinking: list[str] | None = None,
    usage: dict[str, int] | None = None,
) -> Callable[[httpx2.Request], httpx2.Response]:
    """A handler that answers a stream request with a stream and any other request with a
    complete reply, so a test does not care which the model asks for."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        if json.loads(request.content).get("stream"):
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=message_stream(
                    text_deltas, stop_reason=stop_reason, thinking=thinking, usage=usage
                ),
            )
        return httpx2.Response(
            200, json=message_body("".join(text_deltas), stop_reason=stop_reason, usage=usage)
        )

    return answer


def refusal_with(
    status: int, kind: str, message: str
) -> Callable[[httpx2.Request], httpx2.Response]:
    """A handler that refuses every request the way the Messages API does.

    ``x-should-retry: false`` (a header the API sends) keeps the SDK from retrying with its
    own backoff, so a test sees the error at once.
    """

    def refuse(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status,
            headers={"x-should-retry": "false"},
            json={"type": "error", "error": {"type": kind, "message": message}},
        )

    return refuse


@contextmanager
def served_by(handler: Callable[[httpx2.Request], httpx2.Response]) -> Iterator[None]:
    """Route every request an Anthropic model built inside this block makes to ``handler``."""

    def factory(cls: type) -> Callable[..., Any]:
        def build(*, base_url: str | None = None, **_kwargs: object) -> Any:
            return cls(
                base_url=base_url or "https://api.anthropic.com",
                transport=httpx2.MockTransport(handler),
            )

        return build

    with (
        patch(
            "langchain_anthropic.chat_models._get_default_httpx_client",
            side_effect=factory(httpx2.Client),
        ),
        patch(
            "langchain_anthropic.chat_models._get_default_async_httpx_client",
            side_effect=factory(httpx2.AsyncClient),
        ),
    ):
        yield
