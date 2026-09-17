"""HTTP-level tests for BYOK authorization on /ask and /chat.

Every other BYOK assertion in this repo calls the resolution helpers
(``resolve_byok``, ``_resolve_provider``) directly. Nothing previously drove
the actual ``X-Anthropic-API-Key`` header through FastAPI's routing and
dependency-injection layer, so a typo'd header alias, or a header wired up
on only one of /ask or /chat, would still pass the whole suite.

These use ``respx`` to mock the real Claude Platform response at the HTTP
boundary (the BYOK path hits api.anthropic.com directly -- see
create_anthropic_llm's docstring in src/core/services/anthropic_llm.py), so
the request never leaves the process. This is not a business-logic mock:
routing, authorization, model selection, and the full LangGraph agent loop
all run for real; only the outbound Anthropic HTTP call is faked.
"""

import httpx
import pytest
import respx
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

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


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


def _mock_anthropic_response() -> httpx.Response:
    """A minimal, structurally valid Claude Messages API response."""
    return httpx.Response(
        200,
        json={
            "id": "msg_test_byok_http",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": [{"type": "text", "text": "Mocked answer."}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


class TestByokHttpBoundary:
    """X-Anthropic-API-Key driven through the real FastAPI routes."""

    @respx.mock
    def test_ask_from_unauthorized_origin_with_byok_is_not_403(
        self, client: TestClient, community_id: str
    ) -> None:
        respx.post(_ANTHROPIC_MESSAGES_URL).mock(return_value=_mock_anthropic_response())

        response = client.post(
            f"/{community_id}/ask",
            json={"question": "What is this project?", "stream": False},
            headers={
                "Origin": _UNAUTHORIZED_ORIGIN,
                "X-Anthropic-API-Key": "sk-ant-fake-test-key",
            },
        )

        assert response.status_code != 403, response.text

    @respx.mock
    def test_chat_from_unauthorized_origin_with_byok_is_not_403(
        self, client: TestClient, community_id: str
    ) -> None:
        respx.post(_ANTHROPIC_MESSAGES_URL).mock(return_value=_mock_anthropic_response())

        response = client.post(
            f"/{community_id}/chat",
            json={"message": "What is this project?", "stream": False},
            headers={
                "Origin": _UNAUTHORIZED_ORIGIN,
                "X-Anthropic-API-Key": "sk-ant-fake-test-key",
            },
        )

        assert response.status_code != 403, response.text

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
