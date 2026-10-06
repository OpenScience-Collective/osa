"""Answer a Bedrock client's requests at botocore's transport hook.

Requests are intercepted at ``before-send``, after the client has serialized,
authenticated and addressed them, and answered with fixture bytes: the same idea as an
``httpx`` response fixture, one layer down. So what a test asserts about a request is
what the real client would have sent, and the reply goes through the real parser,
including the binary event stream ConverseStream answers in. A refusal or a network
failure is staged the same way, so the real client's error handling runs too.
"""

import json
import struct
import zlib
from typing import Any

from botocore.awsrequest import AWSResponse

EVENT_STREAM = "application/vnd.amazon.eventstream"


class Raw:
    """The minimum urllib3-shaped body botocore reads a response from.

    ``then_raises`` is raised from ``stream()`` once the body has been delivered, which is
    how urllib3 ends a response whose connection stalled or dropped part way.
    """

    def __init__(self, body: bytes, then_raises: Exception | None = None) -> None:
        self._body = body
        self._then_raises = then_raises

    def stream(self, *_args: Any, **_kwargs: Any):
        yield self._body
        if self._then_raises is not None:
            raise self._then_raises

    def read(self, *_args: Any, **_kwargs: Any) -> bytes:
        return self._body

    def close(self) -> None:
        """Nothing to release: the body is already in memory."""


class Wire:
    """Captures what a client sends and answers with a fixed response.

    Args:
        llm: A model built by ``create_bedrock_llm``; its runtime client is hooked.
        body: The response body.
        content_type: The response's content type (``EVENT_STREAM`` for a stream).
        status: The response's HTTP status.
        headers: More response headers, such as ``x-amzn-errortype`` on a refusal.
        raises: An exception to raise from the transport instead of answering, which is
            how botocore sees a timeout or a dropped connection.
        then_raises: An exception to raise from the response body after ``body`` has been
            read, which is how a stream that stalls or drops part way surfaces: botocore
            iterates urllib3's response itself, so urllib3's own error comes out.
    """

    def __init__(
        self,
        llm: Any,
        body: bytes = b"",
        content_type: str = "application/json",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        raises: Exception | None = None,
        then_raises: Exception | None = None,
    ) -> None:
        self.requests: list[Any] = []
        self._body = body
        self._status = status
        self._headers = {"content-type": content_type, **(headers or {})}
        self._raises = raises
        self._then_raises = then_raises
        llm.client.meta.events.register("before-send.bedrock-runtime.*", self._answer)

    def _answer(self, request: Any, **_kwargs: Any) -> AWSResponse:
        self.requests.append(request)
        if self._raises is not None:
            raise self._raises
        return AWSResponse(
            request.url, self._status, self._headers, Raw(self._body, self._then_raises)
        )

    @property
    def sent(self) -> Any:
        return self.requests[-1]

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.sent.body)


def refusal(code: str, message: str) -> dict[str, Any]:
    """The keyword arguments of ``Wire`` that stage the service refusing a call.

    ``code`` is the error type Bedrock names in ``x-amzn-errortype``
    (``ThrottlingException``, ``ValidationException``, ...); the status follows it the
    way the service pairs them.
    """
    statuses = {
        "ThrottlingException": 429,
        "ValidationException": 400,
        "AccessDeniedException": 403,
        "ServiceUnavailableException": 503,
        "InternalServerException": 500,
        "ModelTimeoutException": 408,
    }
    return {
        "body": json.dumps({"message": message}).encode(),
        "status": statuses[code],
        "headers": {"x-amzn-errortype": code},
    }


def frame(event_type: str, payload: dict[str, Any], message_type: str = "event") -> bytes:
    """One AWS event-stream message: the framing ConverseStream replies in.

    ``message_type`` is ``event`` for the stream's own events and ``exception`` for an
    error the service raises mid-stream, which carries its kind in ``:exception-type``.
    """

    def header(name: str, value: str) -> bytes:
        raw_name, raw_value = name.encode(), value.encode()
        return (
            struct.pack("B", len(raw_name))
            + raw_name
            + b"\x07"
            + struct.pack(">H", len(raw_value))
            + raw_value
        )

    kind = ":event-type" if message_type == "event" else ":exception-type"
    headers = (
        header(kind, event_type)
        + header(":content-type", "application/json")
        + header(":message-type", message_type)
    )
    body = json.dumps(payload).encode()
    total = 12 + len(headers) + len(body) + 4
    prelude = struct.pack(">II", total, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude))
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


def converse_stream(
    text_deltas: list[str],
    *,
    stop_reason: str = "end_turn",
    reasoning: list[str] | None = None,
    usage: dict[str, int] | None = None,
) -> bytes:
    """A ConverseStream reply: optional reasoning, then text, then how it stopped.

    ``usage`` of ``None`` sends the usual small counts; pass ``{}`` to end the stream
    with no ``metadata`` event at all, which is what a response the service did not meter
    looks like (``langchain-aws`` reads the usage from that event and nowhere else).
    """
    frames = [frame("messageStart", {"role": "assistant"})]
    index = 0
    if reasoning:
        for piece in reasoning:
            frames.append(
                frame(
                    "contentBlockDelta",
                    {
                        "contentBlockIndex": index,
                        "delta": {"reasoningContent": {"text": piece}},
                    },
                )
            )
        frames.append(frame("contentBlockStop", {"contentBlockIndex": index}))
        index += 1
    if text_deltas:
        for delta in text_deltas:
            frames.append(
                frame(
                    "contentBlockDelta",
                    {"contentBlockIndex": index, "delta": {"text": delta}},
                )
            )
        frames.append(frame("contentBlockStop", {"contentBlockIndex": index}))
    frames.append(frame("messageStop", {"stopReason": stop_reason}))
    counts = {"inputTokens": 20, "outputTokens": 7, "totalTokens": 27} if usage is None else usage
    if counts:
        frames.append(frame("metadata", {"usage": counts, "metrics": {"latencyMs": 1}}))
    return b"".join(frames)
