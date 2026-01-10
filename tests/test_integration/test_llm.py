"""Integration tests with real LLM API calls.

These tests require OPENROUTER_API_KEY in the environment.
Run with: pytest -m llm

Note: These tests make real API calls and cost money.
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


def get_test_api_key() -> str | None:
    """Get the testing API key from environment.

    Checks both OPENROUTER_API_KEY and OPENROUTER_API_KEY_FOR_TESTING
    for backwards compatibility.
    """
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY_FOR_TESTING")


# Skip all tests in this module if no API key is available
pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not get_test_api_key(),
        reason="OPENROUTER_API_KEY not set",
    ),
]


@pytest.fixture
def api_key() -> str:
    """Get the testing API key."""
    key = get_test_api_key()
    assert key, "OPENROUTER_API_KEY must be set"
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
            "/hed/chat",
            json={
                "message": "What is HED? Limit your answer to 50 words.",
                "assistant": "hed",
                "stream": False,
            },
            headers={"X-OpenRouter-API-Key": api_key},
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
            "/hed/chat",
            json={
                "message": "Give me a simple HED annotation for a button press. Just the HED string, nothing else.",
                "assistant": "hed",
                "stream": False,
            },
            headers={"X-OpenRouter-API-Key": api_key},
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
            "/hed/chat",
            json={
                "message": "My name is TestUser. Remember this. Reply with just 'OK'.",
                "assistant": "hed",
                "stream": False,
            },
            headers={"X-OpenRouter-API-Key": api_key},
        )

        assert response1.status_code == 200
        session_id = response1.json()["session_id"]

        # Second message using same session
        response2 = client.post(
            "/hed/chat",
            json={
                "message": "What is my name? Reply with just the name.",
                "assistant": "hed",
                "session_id": session_id,
                "stream": False,
            },
            headers={"X-OpenRouter-API-Key": api_key},
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

        url = cli_main.start_standalone_server(port=38530)
        assert url == "http://127.0.0.1:38530"

        # Give server time to start
        time.sleep(1)

        # Verify server is responding
        import httpx

        response = httpx.get(f"{url}/health", timeout=5.0)
        assert response.status_code == 200

        # Reset for next test
        cli_main._server_thread = None
        cli_main._server_started.clear()


class TestCLIStandaloneWithBYOK:
    """Integration tests for CLI in standalone mode with BYOK.

    These tests verify the complete CLI workflow using standalone mode
    (embedded server) with BYOK (Bring Your Own Key). This proves the
    package works independently without any external backend.
    """

    def test_cli_hed_ask_standalone(self, api_key, tmp_path) -> None:
        """Test 'osa hed ask' command in standalone mode with BYOK."""
        from typer.testing import CliRunner

        from src.cli.main import cli

        runner = CliRunner()

        # Run ask command with BYOK key via environment
        # The CLI reads from config, so we set it first
        result = runner.invoke(
            cli,
            ["config", "set", "--openrouter-key", api_key],
            env={"HOME": str(tmp_path)},
        )
        assert result.exit_code == 0

        # Now run the ask command in standalone mode (default)
        result = runner.invoke(
            cli,
            ["hed", "ask", "What is HED? One sentence only."],
            env={"HOME": str(tmp_path)},
            catch_exceptions=False,
        )

        # Should complete without error
        assert result.exit_code == 0
        # Should have HED-related content
        output_lower = result.output.lower()
        assert any(
            term in output_lower for term in ["hed", "hierarchical", "event", "descriptor"]
        ), f"Expected HED terms in output: {result.output}"

    def test_cli_shows_available_assistants(self) -> None:
        """Test that bare 'osa' command shows available assistants."""
        from typer.testing import CliRunner

        from src.cli.main import cli

        runner = CliRunner()

        result = runner.invoke(cli, [])

        assert result.exit_code == 0
        assert "Available Assistants" in result.output
        assert "hed" in result.output.lower()
        assert "bids" in result.output.lower()
        assert "available" in result.output.lower()
        assert "coming soon" in result.output.lower()

    def test_cli_hed_help_shows_commands(self) -> None:
        """Test that 'osa hed --help' shows ask and chat commands."""
        from typer.testing import CliRunner

        from src.cli.main import cli

        runner = CliRunner()

        result = runner.invoke(cli, ["hed", "--help"])

        assert result.exit_code == 0
        assert "ask" in result.output
        assert "chat" in result.output

    def test_cli_unavailable_assistant_rejects(self) -> None:
        """Test that unavailable assistant (bids) rejects commands."""
        from typer.testing import CliRunner

        from src.cli.main import cli

        runner = CliRunner()

        result = runner.invoke(cli, ["bids", "ask", "test question"])

        assert result.exit_code == 1
        assert "coming soon" in result.output.lower()


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
            "/hed/chat",
            json={
                "message": prompt,
                "assistant": "hed",
                "stream": False,
            },
            headers={"X-OpenRouter-API-Key": api_key},
        )

        assert response.status_code == 200
        content = response.json()["message"]["content"].lower()

        # At least one expected term should be present
        assert any(term in content for term in expected_terms), (
            f"Expected one of {expected_terms} in response: {content}"
        )
