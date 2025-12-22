"""Integration tests with real LLM API calls.

These tests require OPENROUTER_API_KEY_FOR_TESTING in the environment.
Run with: pytest -m llm

Note: These tests make real API calls and cost money.
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


def get_test_api_key() -> str | None:
    """Get the testing API key from environment."""
    return os.environ.get("OPENROUTER_API_KEY_FOR_TESTING")


# Skip all tests in this module if no API key is available
pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not get_test_api_key(),
        reason="OPENROUTER_API_KEY_FOR_TESTING not set",
    ),
]


@pytest.fixture
def api_key() -> str:
    """Get the testing API key."""
    key = get_test_api_key()
    assert key, "OPENROUTER_API_KEY_FOR_TESTING must be set"
    return key


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestHEDAssistantLLM:
    """Integration tests for HED assistant with real LLM calls."""

    def test_simple_hed_question(self, client, api_key) -> None:
        """Test a simple HED question with word limit."""
        response = client.post(
            "/chat",
            json={
                "message": "What is HED? Limit your answer to 50 words.",
                "assistant": "hed",
                "stream": False,
            },
            headers={"X-OpenRouter-Key": api_key},
        )

        assert response.status_code == 200
        data = response.json()

        assert "session_id" in data
        assert "message" in data
        assert data["message"]["role"] == "assistant"

        content = data["message"]["content"]
        assert len(content) > 0
        # Check it mentions HED-related terms
        content_lower = content.lower()
        assert any(
            term in content_lower
            for term in ["hed", "hierarchical", "event", "descriptor", "annotation"]
        )

    def test_hed_annotation_example(self, client, api_key) -> None:
        """Test HED annotation guidance."""
        response = client.post(
            "/chat",
            json={
                "message": "Give me a simple HED annotation for a button press. Just the HED string, nothing else.",
                "assistant": "hed",
                "stream": False,
            },
            headers={"X-OpenRouter-Key": api_key},
        )

        assert response.status_code == 200
        data = response.json()

        content = data["message"]["content"]
        # Should contain HED-like tags
        assert len(content) > 0
        # Common HED tags for button press
        content_lower = content.lower()
        assert any(
            term in content_lower
            for term in ["sensory", "action", "press", "button", "agent-action"]
        )

    def test_conversation_continuity(self, client, api_key) -> None:
        """Test that conversation history is maintained."""
        # First message
        response1 = client.post(
            "/chat",
            json={
                "message": "My name is TestUser. Remember this. Reply with just 'OK'.",
                "assistant": "hed",
                "stream": False,
            },
            headers={"X-OpenRouter-Key": api_key},
        )

        assert response1.status_code == 200
        session_id = response1.json()["session_id"]

        # Second message using same session
        response2 = client.post(
            "/chat",
            json={
                "message": "What is my name? Reply with just the name.",
                "assistant": "hed",
                "session_id": session_id,
                "stream": False,
            },
            headers={"X-OpenRouter-Key": api_key},
        )

        assert response2.status_code == 200
        content = response2.json()["message"]["content"]
        assert "TestUser" in content or "testuser" in content.lower()


class TestStandaloneMode:
    """Integration tests for standalone mode."""

    def test_standalone_server_starts(self) -> None:
        """Test that standalone server can be started."""
        import src.cli.main as cli_main

        # Reset global state
        cli_main._server_thread = None
        cli_main._server_started.clear()

        url = cli_main.start_standalone_server(port=38430)
        assert url == "http://127.0.0.1:38430"

        # Give server time to start
        time.sleep(1)

        # Verify server is responding
        import httpx

        response = httpx.get(f"{url}/health", timeout=5.0)
        assert response.status_code == 200

        # Reset for next test
        cli_main._server_thread = None
        cli_main._server_started.clear()

    def test_cli_ask_command(self, api_key) -> None:
        """Test the CLI ask command in standalone mode."""
        from typer.testing import CliRunner

        import src.cli.main as cli_main

        # Reset global state in case previous test left a server running
        cli_main._server_thread = None
        cli_main._server_started.clear()

        runner = CliRunner()

        # Set the API key in config
        result = runner.invoke(
            cli_main.cli,
            ["config", "set", "--openrouter-key", api_key],
        )
        assert result.exit_code == 0

        # Run ask command
        result = runner.invoke(
            cli_main.cli,
            ["ask", "What is HED? One sentence only.", "--standalone"],
            catch_exceptions=False,
        )

        # Should complete without error
        assert result.exit_code == 0
        assert "HED" in result.output or "hed" in result.output.lower()


class TestPromptExamples:
    """Test with example prompts that should produce specific outputs."""

    @pytest.mark.parametrize(
        "prompt,expected_terms",
        [
            (
                "What does HED stand for? Answer in 10 words or less.",
                ["hierarchical", "event", "descriptor"],
            ),
            (
                "Name one HED tag category. Just the category name.",
                ["sensory", "action", "agent", "item", "event", "property"],
            ),
            (
                "What file format does HED use for schemas? One word.",
                ["xml", "json", "mediawiki"],
            ),
        ],
    )
    def test_factual_hed_questions(
        self, client, api_key, prompt: str, expected_terms: list[str]
    ) -> None:
        """Test factual HED questions produce expected content."""
        response = client.post(
            "/chat",
            json={
                "message": prompt,
                "assistant": "hed",
                "stream": False,
            },
            headers={"X-OpenRouter-Key": api_key},
        )

        assert response.status_code == 200
        content = response.json()["message"]["content"].lower()

        # At least one expected term should be present
        assert any(
            term in content for term in expected_terms
        ), f"Expected one of {expected_terms} in response: {content}"
