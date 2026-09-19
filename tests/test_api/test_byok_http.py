"""HTTP-level tests for BYOK authorization on /ask and /chat.

Every other BYOK assertion in this repo calls the resolution helpers
(``resolve_byok``, ``_resolve_provider``) directly. Nothing previously drove
the actual ``X-Anthropic-API-Key`` header through FastAPI's routing and
dependency-injection layer, so a typo'd header alias, or a header wired up
on only one of /ask or /chat, would still pass the whole suite.

These mock the real Claude Platform response at the HTTP boundary (the BYOK
path hits api.anthropic.com directly -- see create_anthropic_llm's docstring
in src/core/services/anthropic_llm.py), so the request never leaves the
process. This is not a business-logic mock: routing, authorization, model
selection, and the full LangGraph agent loop all run for real; only the
outbound Anthropic HTTP call is faked.

Not respx: the installed ``anthropic`` SDK (1.6.0) vendors its own private,
fully-isolated copy of the HTTP stack (``httpx2``/``httpcore2``, a straight
fork of ``httpx``/``httpcore`` under a different top-level package name),
and ``langchain-anthropic`` builds its client on that vendored copy
(``anthropic.DefaultHttpxClient`` subclasses ``httpx2.Client``, not
``httpx.Client``). respx only ever patches the real, public ``httpx``
package, so a respx route registered against these endpoints silently never
matches -- confirmed by direct testing: it does not raise or skip, it lets
the request fall through to a REAL network call to api.anthropic.com. The
two tests below used to do exactly that in every CI run (safely, since the
fake test key just gets a real 401 back, which the endpoint's own exception
handler turns into a 500 -- satisfying the old, weaker
``status_code != 403`` assertion by accident regardless of whether any
mocking happened at all).

Instead, this file patches ``langchain_anthropic.chat_models.
_get_default_httpx_client`` (the one place langchain-anthropic actually
constructs that vendored client) to return an ``httpx2.Client`` backed by
``httpx2.MockTransport`` -- httpx2's own equivalent of ``httpx.MockTransport``,
since it is a faithful fork. If ``anthropic``/``langchain-anthropic`` change
this vendoring again, this file's tests will fail loudly (real network
error) rather than silently pass on an unmocked live call, which is the
whole point.
"""

import json
from collections.abc import Callable
from unittest.mock import patch

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.config import get_settings
from src.api.routers.community import create_community_router
from src.assistants import discover_assistants, registry
from src.core.services.anthropic_llm import normalize_model

discover_assistants()

# An origin not in any real community's cors_origins (see each community's
# config.yaml), so requests using it are unauthorized without BYOK.
_UNAUTHORIZED_ORIGIN = "https://evil.example.com"

_ANTHROPIC_MESSAGES_PATH = "/v1/messages"


def _admin_auth_headers() -> dict[str, str]:
    """Server admin auth header, if this environment requires one.

    Read through Settings (never the .env file directly) so the admin key
    is never printed. Some local/dev environments configure
    REQUIRE_API_AUTH/API_KEYS, in which case an unauthenticated, non-BYOK
    request gets 401 before it ever reaches the origin check this test
    isolates; supplying a valid admin key here makes that isolation work
    regardless of environment.
    """
    settings = get_settings()
    if not settings.require_api_auth or not settings.api_keys:
        return {}
    admin_keys = settings.parse_admin_keys()
    return {"X-API-Key": next(iter(admin_keys))} if admin_keys else {}


def _community_for_http_byok_test() -> str:
    """Find a registered community suited to this HTTP-boundary test.

    Dynamic lookup (per the project's testing guidelines) rather than a
    hardcoded id: needs a community that is actually available, whose
    default model normalizes on the Anthropic path (so the request does not
    400 before reaching authorization), and with nothing marked for preload
    (so building the assistant does not attempt a real documentation fetch
    alongside the mocked Anthropic call).
    """
    for info in registry.list_all():
        if info.status != "available":
            continue
        config = info.community_config
        if config is None:
            continue
        try:
            normalize_model(config.default_model)
        except ValueError:
            continue
        if config.get_doc_registry().get_preloaded():
            continue
        return info.id
    pytest.fail("No registered community suitable for the HTTP BYOK test")


@pytest.fixture(scope="module")
def community_id() -> str:
    return _community_for_http_byok_test()


@pytest.fixture
def client(community_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(create_community_router(community_id))
    return TestClient(app)


def _sse_message(text: str) -> bytes:
    """A minimal, structurally valid Claude Messages API streaming response.

    create_anthropic_llm always sets streaming=True (see its module
    docstring), so a non-streaming JSON body -- what this file used to
    return -- is never actually a shape the real code path would accept;
    that went unnoticed only because respx never intercepted the request in
    the first place (see the module docstring above).
    """
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_test_byok_http",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


class _CapturedRequest:
    """The single request the mock transport received, if any."""

    request: httpx2.Request | None = None


def _mock_anthropic_client(captured: _CapturedRequest) -> Callable[..., httpx2.Client]:
    """Build a replacement for langchain_anthropic's _get_default_httpx_client.

    Records the request it receives into `captured` and returns a
    structurally valid streaming response, so callers can assert on the
    actual outbound request (headers, in particular the caller's API key)
    instead of only on the response the app produces from it.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.request = request
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse_message("Mocked answer."),
        )

    def fake_get_default_httpx_client(
        *, base_url: str | None = None, **_kwargs: object
    ) -> httpx2.Client:
        # Matches _get_default_httpx_client's keyword-only signature
        # (base_url, timeout, anthropic_proxy) since langchain_anthropic
        # calls it with **http_client_params; only base_url matters for a
        # mocked transport.
        return httpx2.Client(
            base_url=base_url or "https://api.anthropic.com",
            transport=httpx2.MockTransport(handler),
        )

    return fake_get_default_httpx_client


class TestByokHttpBoundary:
    """X-Anthropic-API-Key driven through the real FastAPI routes."""

    def test_ask_from_unauthorized_origin_with_byok_is_not_403(
        self, client: TestClient, community_id: str
    ) -> None:
        captured = _CapturedRequest()
        with patch(
            "langchain_anthropic.chat_models._get_default_httpx_client",
            side_effect=_mock_anthropic_client(captured),
        ):
            response = client.post(
                f"/{community_id}/ask",
                json={"question": "What is this project?", "stream": False},
                headers={
                    "Origin": _UNAUTHORIZED_ORIGIN,
                    "X-Anthropic-API-Key": "sk-ant-fake-test-key",
                },
            )

        assert response.status_code != 403, response.text
        # Avoiding the 403 is necessary but not sufficient: a regression
        # where BYOK silently falls back to the platform's own key would
        # also avoid the 403 (the platform key routes the same call) while
        # billing the server instead of the caller -- the exact bug class
        # #393/#394 already shipped once. Assert the outbound request
        # actually carries the caller's key, matching the pattern in
        # tests/test_cli/test_validate.py.
        assert captured.request is not None, "no request reached the mocked transport"
        assert captured.request.url.path == _ANTHROPIC_MESSAGES_PATH
        assert captured.request.headers["x-api-key"] == "sk-ant-fake-test-key"

    def test_chat_from_unauthorized_origin_with_byok_is_not_403(
        self, client: TestClient, community_id: str
    ) -> None:
        captured = _CapturedRequest()
        with patch(
            "langchain_anthropic.chat_models._get_default_httpx_client",
            side_effect=_mock_anthropic_client(captured),
        ):
            response = client.post(
                f"/{community_id}/chat",
                json={"message": "What is this project?", "stream": False},
                headers={
                    "Origin": _UNAUTHORIZED_ORIGIN,
                    "X-Anthropic-API-Key": "sk-ant-fake-test-key",
                },
            )

        assert response.status_code != 403, response.text
        assert captured.request is not None, "no request reached the mocked transport"
        assert captured.request.url.path == _ANTHROPIC_MESSAGES_PATH
        assert captured.request.headers["x-api-key"] == "sk-ant-fake-test-key"

    def test_unauthorized_origin_without_byok_is_403(
        self, client: TestClient, community_id: str
    ) -> None:
        """Control: the same origin without BYOK really is rejected.

        Without this, the two tests above would pass trivially if the
        router stopped enforcing origin checks altogether.
        """
        response = client.post(
            f"/{community_id}/ask",
            json={"question": "What is this project?", "stream": False},
            headers={"Origin": _UNAUTHORIZED_ORIGIN, **_admin_auth_headers()},
        )

        assert response.status_code == 403
