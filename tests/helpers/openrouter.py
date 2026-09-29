"""A local stand-in for OpenRouter's chat-completions endpoint.

Speaks the wire format LiteLLM and the OpenAI client expect (Server-Sent Events for
streams, one JSON body otherwise), so the real client stack runs and only the network
hop is replaced. Point LiteLLM at it with ``OPENROUTER_API_BASE`` (the ``openrouter``
fixture in the tests that use it does).
"""

import gc
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


def stream_with_usage_shape(shape: str, *texts: str, usage: dict) -> list[dict]:
    """A streamed reply whose usage arrives in the given shape.

    ``documented``: the shape OpenRouter documents, a second chunk that repeats the
    ``finish_reason`` with a content-free delta and the usage. ``on_finish``: usage on
    the only finish chunk. ``trailing``: a further chunk after the finish, with no
    ``finish_reason``. LiteLLM's stream wrapper keeps the provider's usage only for
    ``on_finish``.
    """
    events = [sse_chunk({"role": "assistant", "content": ""})]
    events += [sse_chunk({"content": text}) for text in texts]
    if shape == "on_finish":
        events.append(sse_chunk({}, "stop", usage=usage))
    elif shape == "documented":
        events.append(sse_chunk({"role": "assistant", "content": ""}, "stop"))
        events.append(sse_chunk({"role": "assistant", "content": ""}, "stop", usage=usage))
    elif shape == "trailing":
        events.append(sse_chunk({}, "stop"))
        events.append(sse_chunk({"content": ""}, None, usage=usage))
    else:
        raise ValueError(shape)
    return events


USAGE_SHAPES = ["documented", "on_finish", "trailing"]


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
    """Serves queued replies to ``POST /api/v1/chat/completions`` and records each request.

    Safe under concurrent requests: each request is recorded, with its headers, as one
    entry, so ``requests[i]`` and ``headers[i]`` always describe the same request.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[tuple[dict[str, Any], dict[str, str]]] = []
        self._replies: list[Any] = []
        outer = self
        # Automatic garbage collection is held off while this server is up, and run when it
        # closes. httpcore's connection pool lock is not reentrant, and LiteLLM can leave a
        # streaming response unclosed, to be finalized by the collector wherever it next runs:
        # inside a later request, while the pool lock is held, that finalizer takes the same
        # lock on the same thread and never returns. It showed as a test that hangs forever,
        # at a different place on different runs, with the main thread in
        # ConnectionPool.close under ConnectionPool.handle_request.
        self._gc_was_enabled = gc.isenabled()
        gc.disable()

        class Handler(BaseHTTPRequestHandler):
            # Keep-alive, as the real service does; without it a burst of concurrent
            # synchronous clients sees connections reset.
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 (http.server's interface)
                length = int(self.headers["content-length"])
                request = json.loads(self.rfile.read(length))
                headers = {k.lower(): v for k, v in self.headers.items()}
                with outer._lock:
                    outer._records.append((request, headers))
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

        class Server(ThreadingHTTPServer):
            # The default backlog of 5 resets connections when a dozen threads connect at once.
            request_queue_size = 128
            daemon_threads = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/api/v1"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [request for request, _ in self._records]

    @property
    def headers(self) -> list[dict[str, str]]:
        with self._lock:
            return [headers for _, headers in self._records]

    @property
    def records(self) -> list[tuple[dict[str, Any], dict[str, str]]]:
        """Each request with its own headers."""
        with self._lock:
            return list(self._records)

    def reply(self, *replies: Any) -> None:
        with self._lock:
            self._replies.extend(replies)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        gc.collect()  # a safe point: no request is in flight, so no pool lock is held
        if self._gc_was_enabled:
            gc.enable()
