"""Which model-call failures can succeed on a retry (release review, finding 3).

Every exception here is a real one, built the way its library builds it or raised by the
library's own code: botocore's ``ClientError`` and its stream counterpart, the network
errors botocore and httpx raise, ``langchain-aws``'s own ``ValueError`` for a service
exception event, the Anthropic SDK's status errors and LiteLLM's OpenAI-style ones.
"""

import json

import anthropic
import httpx
import httpx2
import pytest
from botocore.eventstream import EventStreamError
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from langchain_aws.chat_models.bedrock_converse import _handle_bedrock_error, _parse_stream_event

from src.core.services.anthropic_models import BEDROCK_MODELS
from src.core.services.model_errors import classify_model_error


def _client_error(code: str, status: int, message: str = "no") -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "ConverseStream",
    )


def _anthropic_response(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com"))


RETRYABLE = [
    # Amazon Bedrock, from the client
    (_client_error("ThrottlingException", 429), "throttled"),
    (_client_error("ServiceQuotaExceededException", 400), "throttled"),
    (_client_error("ModelTimeoutException", 408), "timeout"),
    (_client_error("ServiceUnavailableException", 503), "unavailable"),
    (_client_error("InternalServerException", 500), "unavailable"),
    (_client_error("ModelStreamErrorException", 424), "unavailable"),
    (_client_error("ModelNotReadyException", 429), "unavailable"),
    # A code nobody listed: the status still says it is the service's side
    (_client_error("SomethingNewException", 502), "unavailable"),
    (_client_error("SomethingNewException", 429), "throttled"),
    # An exception event inside a stream: botocore names the code in lowerCamelCase
    (
        EventStreamError(
            {"Error": {"Code": "throttlingException", "Message": "slow down"}}, "ConverseStream"
        ),
        "throttled",
    ),
    (
        EventStreamError(
            {"Error": {"Code": "modelStreamErrorException", "Message": "x"}}, "ConverseStream"
        ),
        "unavailable",
    ),
    # The network
    (ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-2.amazonaws.com"), "timeout"),
    (
        ConnectTimeoutError(endpoint_url="https://bedrock-runtime.us-east-2.amazonaws.com"),
        "timeout",
    ),
    (
        EndpointConnectionError(endpoint_url="https://bedrock-runtime.us-east-2.amazonaws.com"),
        "connection",
    ),
    (
        ConnectionClosedError(endpoint_url="https://bedrock-runtime.us-east-2.amazonaws.com"),
        "connection",
    ),
    # Anthropic: its vendored httpx lets a stream's network errors out raw
    (httpx2.ReadTimeout("slow"), "timeout"),
    (httpx2.ConnectError("refused"), "connection"),
    (httpx2.RemoteProtocolError("peer closed connection without a complete body"), "connection"),
    # Anthropic
    (
        anthropic.RateLimitError("slow", response=_anthropic_response(429), body=None),
        "throttled",
    ),
    (
        anthropic.InternalServerError("down", response=_anthropic_response(529), body=None),
        "unavailable",
    ),
    (
        anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com")),
        "timeout",
    ),
    (
        anthropic.APIConnectionError(
            message="no route", request=httpx2.Request("POST", "https://api.anthropic.com")
        ),
        "connection",
    ),
]

PERMANENT = [
    (_client_error("ValidationException", 400, "reasoning_effort is not supported"), "rejected"),
    (_client_error("ResourceNotFoundException", 404), "rejected"),
    (_client_error("AccessDeniedException", 403), "unauthorized"),
    (_client_error("UnrecognizedClientException", 403), "unauthorized"),
    (_client_error("SomethingNewException", 400), "rejected"),
    (_client_error("SomethingNewException", 401), "unauthorized"),
    (
        EventStreamError(
            {"Error": {"Code": "validationException", "Message": "bad"}}, "ConverseStream"
        ),
        "rejected",
    ),
    (
        anthropic.BadRequestError("bad", response=_anthropic_response(400), body=None),
        "rejected",
    ),
    (
        anthropic.AuthenticationError("key", response=_anthropic_response(401), body=None),
        "unauthorized",
    ),
    (
        anthropic.PermissionDeniedError("no", response=_anthropic_response(403), body=None),
        "unauthorized",
    ),
    (
        anthropic.UnprocessableEntityError("no", response=_anthropic_response(422), body=None),
        "rejected",
    ),
]


@pytest.mark.parametrize(("error", "kind"), RETRYABLE, ids=lambda v: type(v).__name__)
def test_a_failure_that_can_clear_by_itself_is_retryable(error: Exception, kind: str) -> None:
    failure = classify_model_error(error)

    assert (failure.kind, failure.retryable, failure.retryable_label) == (kind, True, "yes")
    assert failure.from_provider


@pytest.mark.parametrize(("error", "kind"), PERMANENT, ids=lambda v: type(v).__name__)
def test_a_request_the_provider_refuses_is_not(error: Exception, kind: str) -> None:
    failure = classify_model_error(error)

    assert (failure.kind, failure.retryable, failure.retryable_label) == (kind, False, "no")
    assert failure.from_provider


class TestTheDetailCarriesNoProviderMessage:
    def test_a_refusal_names_its_class_and_code_only(self) -> None:
        secret = "the prompt said: my password is hunter2"

        failure = classify_model_error(_client_error("ValidationException", 400, secret))

        assert "ClientError" in failure.detail and "ValidationException" in failure.detail
        assert secret not in failure.detail


def _mid_stream_error(error_type: str) -> anthropic.APIStatusError:
    """The error the Anthropic SDK raises for an SSE ``error`` event, from its own decoder.

    The stream has already answered ``200``, so the status says nothing; only the body's
    error type does.
    """
    body = json.dumps({"type": "error", "error": {"type": error_type, "message": "x"}})
    response = httpx2.Response(
        200,
        content=f"event: error\ndata: {body}\n\n".encode(),
        headers={"content-type": "text/event-stream"},
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    stream = anthropic.Stream(
        cast_to=object, response=response, client=anthropic.Anthropic(api_key="test")
    )
    with pytest.raises(anthropic.APIStatusError) as caught:
        list(stream)
    assert caught.value.status_code == 200
    return caught.value


class TestAnAnthropicErrorThatArrivesInAStream:
    """Release review: ``overloaded_error`` mid-stream was logged as an unexpected error
    with a traceback and carried no ``retryable``, because the status is 200."""

    @pytest.mark.parametrize(
        ("error_type", "kind", "retryable"),
        [
            ("overloaded_error", "unavailable", True),
            ("api_error", "unavailable", True),
            ("rate_limit_error", "throttled", True),
            ("timeout_error", "timeout", True),
            ("invalid_request_error", "rejected", False),
            ("not_found_error", "rejected", False),
            ("authentication_error", "unauthorized", False),
            ("permission_error", "unauthorized", False),
        ],
    )
    def test_the_body_says_what_the_status_does_not(
        self, error_type: str, kind: str, retryable: bool
    ) -> None:
        failure = classify_model_error(_mid_stream_error(error_type))

        assert (failure.kind, failure.retryable) == (kind, retryable)
        assert error_type in failure.detail

    def test_an_error_type_nobody_listed_stays_unknown(self) -> None:
        failure = classify_model_error(_mid_stream_error("a_type_added_next_year"))

        assert (failure.kind, failure.retryable) == ("unknown", None)
        assert not failure.from_provider

    def test_the_detail_carries_the_type_and_none_of_the_providers_message(self) -> None:
        failure = classify_model_error(_mid_stream_error("overloaded_error"))

        assert failure.detail == "APIStatusError overloaded_error"


class TestLangchainAwsValueErrors:
    """``langchain-aws`` raises ``ValueError`` for two things that are the provider's,
    and the router used to call every ``ValueError`` the reader's own fault."""

    def test_a_service_exception_event_it_could_not_raise_as_a_client_error(self) -> None:
        with pytest.raises(ValueError, match="Received AWS exception") as caught:
            _parse_stream_event({"throttlingException": {"message": "Too many requests"}})

        failure = classify_model_error(caught.value)

        assert (failure.kind, failure.retryable) == ("throttled", True)

    def test_a_validation_event_is_not_retryable(self) -> None:
        with pytest.raises(ValueError, match="Received AWS exception") as caught:
            _parse_stream_event({"validationException": {"message": "bad field"}})

        assert classify_model_error(caught.value).retryable is False

    def test_an_exception_event_it_does_not_know_is_the_providers_but_not_known_retryable(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="Received AWS exception") as caught:
            _parse_stream_event({"novelException": {"message": "?"}})

        failure = classify_model_error(caught.value)

        assert failure.from_provider and failure.retryable is None

    def test_a_stream_event_it_has_no_parser_for_is_the_providers_not_the_readers(self) -> None:
        with pytest.raises(ValueError, match="unsupported stream event") as caught:
            _parse_stream_event({"somethingNewEvent": {"x": 1}})

        failure = classify_model_error(caught.value)

        assert failure.from_provider and failure.retryable is None
        assert "somethingNewEvent" not in failure.detail

    def test_a_permissions_error_about_system_tools_is_classified_by_what_it_wraps(self) -> None:
        original = _client_error(
            "AccessDeniedException", 403, "not authorized to perform bedrock:InvokeTool"
        )
        with pytest.raises(ValueError, match="InvokeTool") as caught:
            _handle_bedrock_error(original)

        failure = classify_model_error(caught.value)

        assert (failure.kind, failure.retryable) == ("unauthorized", False)
        assert caught.value.__cause__ is original


class TestWhatIsNotTheProviders:
    @pytest.mark.parametrize(
        "error",
        [
            ValueError("Message too long (20000 chars). Max: 10000"),
            ValueError("Invalid message at index 3: missing 'content' attribute."),
            KeyError("messages"),
            RuntimeError("the parked call's citations are malformed"),
            Exception("boom"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_our_own_errors_are_unknown(self, error: Exception) -> None:
        failure = classify_model_error(error)

        assert failure.kind == "unknown"
        assert failure.retryable is None and failure.retryable_label == "unknown"
        assert not failure.from_provider
        assert failure.detail == type(error).__name__

    def test_a_wrapper_is_classified_by_the_provider_error_it_was_raised_from(self) -> None:
        try:
            try:
                raise _client_error("ThrottlingException", 429)
            except ClientError as inner:
                raise RuntimeError("model call failed") from inner
        except RuntimeError as wrapped:
            failure = classify_model_error(wrapped)

        assert (failure.kind, failure.retryable) == ("throttled", True)

    def test_a_cause_chain_that_loops_does_not_hang(self) -> None:
        a, b = RuntimeError("a"), RuntimeError("b")
        a.__cause__, b.__cause__ = b, a

        assert classify_model_error(a).kind == "unknown"


class TestLiteLLMExceptions:
    """LiteLLM raises OpenAI-style exceptions carrying ``status_code``."""

    @pytest.fixture(scope="class")
    def litellm(self):
        import litellm

        return litellm

    def test_a_rate_limit(self, litellm) -> None:
        error = litellm.RateLimitError("slow down", llm_provider="openrouter", model="m")

        assert classify_model_error(error).kind == "throttled"

    def test_a_timeout(self, litellm) -> None:
        error = litellm.Timeout("slow", model="m", llm_provider="openrouter")

        failure = classify_model_error(error)

        assert (failure.kind, failure.retryable) == ("timeout", True)

    def test_a_bad_request_is_not_retryable(self, litellm) -> None:
        error = litellm.BadRequestError("bad field", model="m", llm_provider="openrouter")

        failure = classify_model_error(error)

        assert (failure.kind, failure.retryable) == ("rejected", False)

    def test_a_context_window_overflow_is_not_retryable(self, litellm) -> None:
        error = litellm.ContextWindowExceededError("too long", model="m", llm_provider="openrouter")

        assert classify_model_error(error).retryable is False

    def test_an_authentication_failure_is_not_retryable(self, litellm) -> None:
        error = litellm.AuthenticationError("bad key", llm_provider="openrouter", model="m")

        assert classify_model_error(error).kind == "unauthorized"

    def test_a_server_error_is_retryable(self, litellm) -> None:
        error = litellm.InternalServerError("oops", llm_provider="openrouter", model="m")

        assert classify_model_error(error).retryable is True


class TestOnlyAModelCallsErrorsAreTheModels:
    """A tool of ours that fetches a page raises ``httpx`` errors and the built-in
    ``TimeoutError`` and ``ConnectionError``, and langgraph's ``ToolNode`` re-raises
    them. None of those says anything about a model call: no kind, no retry claim, and so
    no "Model call failed" label and no swallowed traceback (release review, finding 3)."""

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("refused"),
            httpx.RemoteProtocolError("peer closed connection"),
            httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", "https://x.example"),
                response=httpx.Response(404),
            ),
            httpx.HTTPStatusError(
                "busy",
                request=httpx.Request("GET", "https://x.example"),
                response=httpx.Response(503),
            ),
            TimeoutError("fetch timed out"),
            ConnectionError("reset"),
            ConnectionResetError("reset by peer"),
            ConnectionError("Incomplete Bedrock response stream: missing messageStop event."),
        ],
        ids=lambda e: f"{type(e).__name__}-{str(e)[:12]}",
    )
    def test_the_errors_a_network_tool_raises_are_unknown(self, error: Exception) -> None:
        failure = classify_model_error(error)

        assert failure.kind == "unknown"
        assert failure.retryable is None
        assert not failure.from_provider

    def test_a_class_named_like_a_providers_is_not_one(self) -> None:
        class Timeout(Exception):
            status_code = 504

        assert classify_model_error(Timeout("a tool's own")).kind == "unknown"

    def test_a_connection_error_is_the_models_only_when_langchain_aws_raised_it(self) -> None:
        def ours() -> None:
            raise ConnectionError("Incomplete Bedrock response stream: missing messageStop event.")

        with pytest.raises(ConnectionError) as caught:
            ours()

        assert classify_model_error(caught.value).kind == "unknown"

    def test_the_connection_error_langchain_aws_raises_is_a_retryable_model_failure(self) -> None:
        """Through the real client: the stream ends after ``messageStart`` with no
        ``messageStop``, and langchain-aws raises the built-in ``ConnectionError``."""
        from src.api.config import Settings
        from src.core.services.bedrock_llm import _bedrock_client, create_bedrock_llm
        from tests.helpers.bedrock_wire import EVENT_STREAM, Wire, frame

        _bedrock_client.cache_clear()
        try:
            llm = create_bedrock_llm(
                sorted(BEDROCK_MODELS)[0],
                settings=Settings(
                    _env_file=None,
                    bedrock_api_key="test-bedrock-key",
                    bedrock_region="us-east-2",
                    bedrock_max_output_tokens=16000,
                ),
            )
            Wire(llm, frame("messageStart", {"role": "assistant"}), EVENT_STREAM)

            with pytest.raises(ConnectionError, match="missing messageStop") as caught:
                list(llm.stream("hello"))
        finally:
            _bedrock_client.cache_clear()

        failure = classify_model_error(caught.value)
        assert (failure.kind, failure.retryable, failure.from_provider) == (
            "connection",
            True,
            True,
        )
