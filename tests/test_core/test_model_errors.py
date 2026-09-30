"""Which model-call failures can succeed on a retry (release review, finding 3).

Every exception here is a real one, built the way its library builds it or raised by the
library's own code: botocore's ``ClientError`` and its stream counterpart, the network
errors botocore and httpx raise, ``langchain-aws``'s own ``ValueError`` for a service
exception event, the Anthropic SDK's status errors and LiteLLM's OpenAI-style ones.
"""

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
    (TimeoutError(), "timeout"),
    (httpx.ReadTimeout("slow"), "timeout"),
    (httpx.ConnectError("refused"), "connection"),
    # langchain-aws: a stream that ended with no messageStop event
    (
        ConnectionError("Incomplete Bedrock response stream: missing messageStop event."),
        "connection",
    ),
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


def test_an_http_status_error_is_read_by_its_status() -> None:
    request = httpx.Request("POST", "https://x.example")

    bad = httpx.HTTPStatusError("bad", request=request, response=httpx.Response(400))
    busy = httpx.HTTPStatusError("busy", request=request, response=httpx.Response(503))

    assert classify_model_error(bad).retryable is False
    assert classify_model_error(busy).retryable is True
