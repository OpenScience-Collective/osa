"""Integration tests for the /hed/ask endpoint against the real Claude Platform on AWS.

These make real, paid API calls (the HED assistant binds knowledge-search
tools and thinking defaults on, so nearly every response here is exactly the
block-list content shape src/agents/content.py exists to handle). What this
actually checks, end to end: that a real answer/stream never contains a
stringified block-list artifact (the pre-Phase-2 "str(content)" bug), on
both the non-streaming and streaming paths, and that streamed `thinking`
events carry no payload.

This is narrower than "reasoning never leaks": it greps for dict-repr
fingerprints, so it cannot detect a classification swap that streams
reasoning text as an ordinary "text" block -- that content would be
plain prose with no artifact to grep for. Classification correctness
(thinking/redacted_thinking blocks never contribute to extract_text's or
classify_content_blocks's "text" pairs) is covered directly by the unit
tests in tests/test_agents/test_content.py.

Skip condition: see tests/test_integration/test_anthropic_platform.py's
module docstring for why this checks Settings rather than os.getenv.
"""

import json

import pytest
from fastapi.testclient import TestClient

from src.api.config import get_settings
from src.api.main import app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm,
    pytest.mark.skipif(
        not get_settings().anthropic_api_key,
        reason="ANTHROPIC_API_KEY is not configured (server mode requires it)",
    ),
]

# An authorized HED origin (see src/assistants/hed/config.yaml's cors_origins)
# so the request resolves a platform/community key instead of requiring BYOK.
_HED_ORIGIN = "https://hedtags.org"

# Fragments that would only appear if a Python list/dict of content blocks
# were stringified into the answer (the bug this content-shape work fixes).
_BLOCK_LIST_ARTIFACTS = (
    '"type": "thinking"',
    "'type': 'thinking'",
    '"type": "text"',
    "'type': 'text'",
    "content_block",
)


def _auth_headers() -> dict[str, str]:
    """Build the X-API-Key header this environment's admin auth requires.

    This worktree's .env has REQUIRE_API_AUTH/API_KEYS configured, so an
    unauthenticated /hed/ask (no BYOK header) needs a valid admin key -- a
    BYOK header would sidestep that, but the real ANTHROPIC_API_KEY here is
    AWS-workspace-scoped (see src/core/services/anthropic_llm.py) and would
    fail if sent as BYOK, since BYOK routes to api.anthropic.com without the
    workspace header. Reading the key through Settings (never the .env file
    directly) keeps this test from ever printing it.
    """
    settings = get_settings()
    if not settings.require_api_auth or not settings.api_keys:
        return {}
    admin_keys = settings.parse_admin_keys()
    return {"X-API-Key": next(iter(admin_keys))} if admin_keys else {}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestHedAskNonStreaming:
    """Non-streaming /hed/ask against the real Claude Platform on AWS."""

    def test_answer_is_plain_text_with_no_block_artifacts(self, client: TestClient) -> None:
        response = client.post(
            "/hed/ask",
            json={"question": "What is HED? Answer in one sentence.", "stream": False},
            headers={"Origin": _HED_ORIGIN, **_auth_headers()},
        )

        assert response.status_code == 200
        data = response.json()
        answer = data["answer"]

        assert isinstance(answer, str)
        assert answer.strip() != ""
        for artifact in _BLOCK_LIST_ARTIFACTS:
            assert artifact not in answer, f"Answer leaked a block-list artifact: {answer!r}"


class TestHedAskStreaming:
    """Streaming /hed/ask against the real Claude Platform on AWS."""

    def test_client_receives_text_and_thinking_events_carry_no_reasoning(
        self, client: TestClient
    ) -> None:
        collected_text = ""
        thinking_events: list[dict] = []

        with client.stream(
            "POST",
            "/hed/ask",
            json={"question": "What is HED? Answer in one sentence.", "stream": True},
            headers={"Origin": _HED_ORIGIN, **_auth_headers()},
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: ") :])
                if event.get("event") == "content":
                    collected_text += event.get("content", "")
                elif event.get("event") == "thinking":
                    thinking_events.append(event)
                elif event.get("event") == "error":
                    pytest.fail(f"Streaming error event: {event}")

        assert collected_text.strip() != ""
        for artifact in _BLOCK_LIST_ARTIFACTS:
            assert artifact not in collected_text

        # Thinking events are a liveness signal only: never any reasoning text.
        for event in thinking_events:
            assert set(event.keys()) == {"event"}
