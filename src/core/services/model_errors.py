"""Telling a model call's failures apart: which can succeed on a retry, which cannot.

A throttle, a read timeout and a request the provider rejects as invalid all end a
stream with an exception, and all used to reach the reader as the same "try again". Only
the first two can succeed on a retry; a request the provider refused outright (a 400 for a
field the model does not take, say) fails the same way every time, and the operator is the
one who has to act on it.

``classify_model_error`` reads an exception from any of the three provider paths, each of
which raises differently:

- Amazon Bedrock raises botocore's ``ClientError`` (the error code in
  ``response["Error"]["Code"]``; the service's own exception events that arrive inside a
  stream carry the code in lowerCamelCase), ``ReadTimeoutError`` and friends for the
  network, and, from ``langchain-aws``, a ``ValueError`` for an exception event it could
  not turn into a ``ClientError`` and for a permissions error about system tools.
- Anthropic raises the SDK's ``APIStatusError`` family (``status_code``) and
  ``APITimeoutError`` / ``APIConnectionError``.
- LiteLLM raises OpenAI-style exceptions (``status_code``, ``Timeout``).

Anything else is ``unknown``: no claim is made about it.
"""

import re
from dataclasses import dataclass
from typing import Literal

import httpx
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    HTTPClientError,
    ReadTimeoutError,
)
from botocore.exceptions import ConnectionError as BotocoreConnectionError

FailureKind = Literal[
    "throttled", "timeout", "unavailable", "connection", "rejected", "unauthorized", "unknown"
]

#: Bedrock error codes whose request can succeed later, and what each one is. Looked up
#: with the first letter upper-cased, since an exception event inside a stream names its
#: code in lowerCamelCase (``throttlingException``).
_BEDROCK_TRANSIENT: dict[str, FailureKind] = {
    "ThrottlingException": "throttled",
    "ServiceQuotaExceededException": "throttled",
    "ModelTimeoutException": "timeout",
    "ModelNotReadyException": "unavailable",
    "ServiceUnavailableException": "unavailable",
    "InternalServerException": "unavailable",
    "ModelStreamErrorException": "unavailable",
    "ModelErrorException": "unavailable",
}

#: Bedrock error codes that fail the same way on every retry.
_BEDROCK_PERMANENT: dict[str, FailureKind] = {
    "ValidationException": "rejected",
    "ResourceNotFoundException": "rejected",
    "AccessDeniedException": "unauthorized",
    "UnrecognizedClientException": "unauthorized",
    "ExpiredTokenException": "unauthorized",
}

#: ``langchain-aws`` writes an exception event it could not raise as a ``ClientError`` as
#: ``ValueError("Received AWS exception <code>:\n\n<body>")``.
_RECEIVED_AWS_EXCEPTION = re.compile(r"^Received AWS exception (\w+):")

#: Exception class names (anywhere in the MRO) that mean the call timed out or the
#: connection failed, for libraries whose base classes cannot be imported here: the
#: Anthropic SDK carries its own copy of httpx, LiteLLM raises OpenAI-style classes.
_TIMEOUT_NAMES = frozenset({"APITimeoutError", "TimeoutException", "Timeout"})
_CONNECTION_NAMES = frozenset({"APIConnectionError", "TransportError"})


@dataclass(frozen=True)
class ModelFailure:
    """What kind of failure a model call was.

    Attributes:
        kind: ``throttled``, ``timeout``, ``unavailable`` and ``connection`` can succeed
            on a retry; ``rejected`` (the provider refused the request) and
            ``unauthorized`` cannot; ``unknown`` is an exception this module does not
            recognize.
        retryable: True when a retry can succeed, False when it cannot, None when that is
            not known.
        detail: The exception class and the provider's error code or HTTP status, for a
            log line. It carries none of the provider's message.
    """

    kind: FailureKind
    retryable: bool | None
    detail: str

    @property
    def from_provider(self) -> bool:
        """Whether the exception came from the model call, not from OSA's own code."""
        return self.kind != "unknown"

    @property
    def retryable_label(self) -> str:
        """``yes``, ``no`` or ``unknown``, for a log line."""
        return {True: "yes", False: "no", None: "unknown"}[self.retryable]


def _by_status(status: int) -> tuple[FailureKind, bool] | None:
    """The kind an HTTP status says, or None when it says nothing about retrying."""
    if status == 429:
        return "throttled", True
    if status == 408:
        return "timeout", True
    if status >= 500:
        return "unavailable", True
    if status in (401, 403):
        return "unauthorized", False
    if 400 <= status < 500:
        return "rejected", False
    return None


def _upper_first(code: str) -> str:
    return code[:1].upper() + code[1:]


def _bedrock_code(code: str, name: str) -> ModelFailure | None:
    """The failure a Bedrock error code names, or None for a code this module does not list.

    botocore builds a ``ClientError`` subclass named for the code, so the class name and
    the code are often the same word; the detail says it once.
    """
    label = name if name == code else f"{name} {code}"
    known = _upper_first(code)
    if known in _BEDROCK_TRANSIENT:
        return ModelFailure(_BEDROCK_TRANSIENT[known], True, label)
    if known in _BEDROCK_PERMANENT:
        return ModelFailure(_BEDROCK_PERMANENT[known], False, label)
    return None


def _classify_one(error: BaseException) -> ModelFailure | None:
    name = type(error).__name__
    if isinstance(error, ClientError):
        code = str(error.response.get("Error", {}).get("Code", ""))
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        known = _bedrock_code(code, name)
        if known is not None:
            return known
        by_status = _by_status(status) if isinstance(status, int) else None
        if by_status is not None:
            return ModelFailure(by_status[0], by_status[1], f"{name} {code} (HTTP {status})")
        return ModelFailure("unavailable", None, f"{name} {code}")
    if isinstance(
        error, ReadTimeoutError | ConnectTimeoutError | httpx.TimeoutException | TimeoutError
    ):
        return ModelFailure("timeout", True, name)
    if isinstance(error, httpx.HTTPStatusError):
        by_status = _by_status(error.response.status_code)
        if by_status is not None:
            return ModelFailure(
                by_status[0], by_status[1], f"{name} (HTTP {error.response.status_code})"
            )
        return None
    if isinstance(
        error, HTTPClientError | BotocoreConnectionError | ConnectionError | httpx.TransportError
    ):
        # A connection that dropped, including a Bedrock stream that ended with no
        # messageStop (``ConnectionError`` from langchain-aws).
        return ModelFailure("connection", True, name)
    if isinstance(error, ValueError):
        match = _RECEIVED_AWS_EXCEPTION.match(str(error))
        if match:
            return _bedrock_code(match.group(1), name) or ModelFailure(
                "unavailable", None, f"{name} {match.group(1)}"
            )
        return None
    mro_names = {cls.__name__ for cls in type(error).__mro__}
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and (by_status := _by_status(status)) is not None:
        return ModelFailure(by_status[0], by_status[1], f"{name} (HTTP {status})")
    if mro_names & _TIMEOUT_NAMES:
        return ModelFailure("timeout", True, name)
    if mro_names & _CONNECTION_NAMES:
        return ModelFailure("connection", True, name)
    return None


def classify_model_error(error: BaseException) -> ModelFailure:
    """Classify an exception a model call raised.

    Looks at the exception and then at what it was raised from (``__cause__``, a few
    levels), since a library that wraps a provider error keeps the original there.
    """
    current: BaseException | None = error
    for _ in range(4):
        if current is None:
            break
        failure = _classify_one(current)
        if failure is not None:
            return failure
        current = current.__cause__
    return ModelFailure("unknown", None, type(error).__name__)
