"""A local stand-in for OpenRouter's chat-completions endpoint.

Speaks the wire format LiteLLM and the OpenAI client expect (Server-Sent Events for
streams, one JSON body otherwise), so the real client stack runs and only the network
hop is replaced. Point LiteLLM at it with ``OPENROUTER_API_BASE`` (the ``openrouter``
fixture in the tests that use it does).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def sse_chunk(delta: dict[str, Any], finish: str | None = None, usage: dict | None = None) -> dict:
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


def stream_of(*texts: str, usage: dict | None = None) -> list[dict]:
    """A streamed reply: the given text chunks, then the finish chunk (carrying usage)."""
    events = [sse_chunk({"role": "assistant", "content": ""})]
    events += [sse_chunk({"content": text}) for text in texts]
    events.append(sse_chunk({}, "stop", usage=usage))
    return events


def tool_call_stream(name: str, arguments: str) -> list[dict]:
    call = {"index": 0, "id": "call_1", "type": "function", "function": {"name": name}}
    return [
        sse_chunk(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{**call, "function": {"name": name, "arguments": ""}}],
            }
        ),
        sse_chunk({"tool_calls": [{"index": 0, "function": {"arguments": arguments}}]}),
        sse_chunk({}, "tool_calls"),
    ]


def whole_reply(text: str, usage: dict | None = None) -> dict:
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
