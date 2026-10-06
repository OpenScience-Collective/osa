"""Telling a model call's failures apart: which can succeed on a retry, which cannot.

A throttle, a read timeout and a request the provider rejects as invalid all end a
stream with an exception. Only the first two can succeed on a retry; a request the
provider refused outright (a 400 for a field the model does not take, say) fails the same
way every time, and the operator is the one who has to act on it.

``classify_model_error`` reads an exception from any of the three provider paths, each of
which raises differently:

- Amazon Bedrock raises botocore's ``ClientError`` (the error code in
  ``response["Error"]["Code"]``; the service's own exception events that arrive inside a
  stream carry the code in lowerCamelCase), ``ReadTimeoutError`` and friends for the
  network, and, from ``langchain-aws``, a ``ValueError`` for an exception event it could
  not turn into a ``ClientError`` and for a permissions error about system tools. Once a
  stream is open, botocore iterates urllib3's response itself, so a stall or a dropped
  connection there is urllib3's ``ReadTimeoutError`` or ``ProtocolError``, not botocore's.
- Anthropic raises the SDK's ``APIStatusError`` family (``status_code``) and
  ``APITimeoutError`` / ``APIConnectionError``.
- LiteLLM raises OpenAI-style exceptions (``status_code``, ``Timeout``).

Anything else is ``unknown``: no claim is made about it. That includes the errors any
code of ours raises, and this module is careful to leave them there. A tool that fetches
a page fails with ``httpx`` errors and the built-in ``TimeoutError`` and
``ConnectionError``, and langgraph's ``ToolNode`` re-raises what it does not handle, so such
an error reaches the same handlers as a model call's. Reading it as the model's would put
the wrong name on it, call it retryable (or, for a 404, not retryable), and leave its
traceback out of the log. So an exception is the model call's only when its class comes
from a model provider's library, or, for a class that is not a provider's, when its
traceback shows a provider library raised it (the one built-in exception ``langchain-aws``
raises on its own) or carried it up (urllib3's timeout and protocol errors, which botocore
lets out of an open stream raw).
"""

import re
from dataclasses import dataclass
from types import TracebackType
from typing import Literal

from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    HTTPClientError,
    ReadTimeoutError,
)
from botocore.exceptions import ConnectionError as BotocoreConnectionError
from urllib3.exceptions import ProtocolError as Urllib3ProtocolError
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

FailureKind = Literal[
    "throttled", "timeout", "unavailable", "connection", "rejected", "unauthorized", "unknown"
]

#: The kinds ``ModelFailure.worth_retrying_now`` allows.
_WORTH_RETRYING_NOW: frozenset[FailureKind] = frozenset({"unavailable", "connection"})

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

#: ``langchain-aws`` raises ``ValueError("Received unsupported stream event:\n\n<event>")``
#: for a stream event it has no parser for, which is the service's doing, not a request OSA
#: built wrong.
_UNSUPPORTED_STREAM_EVENT = re.compile(r"^Received unsupported stream event")

#: The packages a model call's own exception classes come from. ``httpx2`` is the copy of
#: httpx the Anthropic SDK vendors, whose errors escape it raw when a stream dies part way;
#: OSA's own code uses ``httpx`` (a different package), so the two never mix. LiteLLM's
#: exceptions subclass the OpenAI SDK's.
_PROVIDER_PACKAGES = frozenset(
    {
        "anthropic",
        "openai",
        "litellm",
        "httpx2",
        "langchain_aws",
        "langchain_anthropic",
        "langchain_litellm",
    }
)

#: Exception class names (anywhere in the MRO) that mean the call timed out or the
#: connection failed, for libraries whose base classes cannot be imported here: the
#: Anthropic SDK carries its own copy of httpx, LiteLLM raises OpenAI-style classes. Read
#: only on a class from ``_PROVIDER_PACKAGES``, since ``Timeout`` and ``TransportError``
#: are names other libraries use too.
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
    def worth_retrying_now(self) -> bool:
        """Whether a second try a moment later is likely to work.

        True for a failure that can clear by itself and is not already covered: a stream
        the service cut short, a dropped connection, a service error. A throttle is left
        out, since the clients retry one with backoff before the response begins, and a
        second try a second later only adds load to the account being throttled. A timeout
        is left out even though a retry can succeed: it has already waited out its limit,
        so a second try would double the reader's wait.
        """
        return self.retryable is True and self.kind in _WORTH_RETRYING_NOW

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


#: What the Anthropic API's own error types say (``APIStatusError.type``, read from the
#: error body). Used when the status says nothing: a stream that fails part way has already
#: answered ``200``, so the SDK raises ``APIStatusError`` with ``status_code`` 200 and the
#: kind only in the body (``overloaded_error`` is the usual one).
_ANTHROPIC_ERROR_TYPES: dict[str, tuple[FailureKind, bool]] = {
    "overloaded_error": ("unavailable", True),
    "api_error": ("unavailable", True),
    "rate_limit_error": ("throttled", True),
    "timeout_error": ("timeout", True),
    "invalid_request_error": ("rejected", False),
    "not_found_error": ("rejected", False),
    "billing_error": ("rejected", False),
    "authentication_error": ("unauthorized", False),
    "permission_error": ("unauthorized", False),
}


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


def _is_provider_class(error: BaseException) -> bool:
    """Whether the exception's class comes from a model provider's library."""
    return any(cls.__module__.split(".")[0] in _PROVIDER_PACKAGES for cls in type(error).__mro__)


def _raised_in(error: BaseException, package: str) -> bool:
    """Whether the innermost frame of the error's traceback is in ``package``, that is,
    whether that library's own code raised it. False for an error that was never raised."""
    frame: TracebackType | None = error.__traceback__
    while frame is not None and frame.tb_next is not None:
        frame = frame.tb_next
    if frame is None:
        return False
    module = str(frame.tb_frame.f_globals.get("__name__", ""))
    return module == package or module.startswith(f"{package}.")


def _passed_through(error: BaseException, package: str) -> bool:
    """Whether the error's traceback has a frame in ``package``, that is, whether it
    traveled through that library's code on its way up. False for an error that was never
    raised. Unlike ``_raised_in``, any frame counts: a urllib3 error's innermost frame is
    urllib3's own."""
    frame: TracebackType | None = error.__traceback__
    while frame is not None:
        module = str(frame.tb_frame.f_globals.get("__name__", ""))
        if module == package or module.startswith(f"{package}."):
            return True
        frame = frame.tb_next
    return False


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
    if isinstance(error, ReadTimeoutError | ConnectTimeoutError):
        return ModelFailure("timeout", True, name)
    if isinstance(error, HTTPClientError | BotocoreConnectionError):
        return ModelFailure("connection", True, name)
    if isinstance(error, Urllib3TimeoutError | Urllib3ProtocolError) and _passed_through(
        error, "langchain_aws"
    ):
        # urllib3's own errors, which botocore lets out raw when it iterates a stream's
        # response (a Bedrock stream that stalled past the read timeout, or whose
        # connection dropped part way). urllib3 sits under other clients too, so only an
        # error that came up through langchain-aws, the Bedrock model call, is the model's.
        # The detail names urllib3, since botocore has classes of the same names.
        kind: FailureKind = "timeout" if isinstance(error, Urllib3TimeoutError) else "connection"
        return ModelFailure(kind, True, f"urllib3.{name}")
    if isinstance(error, ValueError):
        match = _RECEIVED_AWS_EXCEPTION.match(str(error))
        if match:
            return _bedrock_code(match.group(1), name) or ModelFailure(
                "unavailable", None, f"{name} {match.group(1)}"
            )
        if _UNSUPPORTED_STREAM_EVENT.match(str(error)):
            return ModelFailure("unavailable", None, f"{name} unsupported stream event")
        return None
    if type(error) is ConnectionError and _raised_in(error, "langchain_aws"):
        # A Bedrock stream that ended with no messageStop. Built-in, so it is the model
        # call's only when langchain-aws raised it: any ConnectionError a tool of ours
        # meets is the same class.
        return ModelFailure("connection", True, name)
    if not _is_provider_class(error):
        # Includes httpx's errors, the built-in TimeoutError and every other
        # ConnectionError: a tool of ours raises those too (see the module docstring).
        return None
    mro_names = {cls.__name__ for cls in type(error).__mro__}
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and (by_status := _by_status(status)) is not None:
        return ModelFailure(by_status[0], by_status[1], f"{name} (HTTP {status})")
    api_type = getattr(error, "type", None)
    if isinstance(api_type, str) and (by_type := _ANTHROPIC_ERROR_TYPES.get(api_type)):
        return ModelFailure(by_type[0], by_type[1], f"{name} {api_type}")
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
