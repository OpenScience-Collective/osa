"""Tests for src.api.config.Settings validation.

Real Settings construction throughout -- no mocks, since the whole point is
to verify pydantic's own validation behavior.
"""

import pytest
from pydantic import ValidationError

from src.api.config import Settings


class TestWorkspaceIdWithBaseUrl:
    """ANTHROPIC_BASE_URL requires ANTHROPIC_WORKSPACE_ID (see
    validate_workspace_id_with_base_url): the Claude Platform on AWS
    endpoint rejects requests without the anthropic-workspace-id header, so
    this is caught at Settings construction instead of on the first request
    that falls through to the platform key.
    """

    def test_base_url_without_workspace_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ANTHROPIC_WORKSPACE_ID"):
            Settings(
                anthropic_base_url="https://aws-anthropic.example.test",
                anthropic_workspace_id=None,
            )

    def test_base_url_with_workspace_id_accepted(self) -> None:
        settings = Settings(
            anthropic_base_url="https://aws-anthropic.example.test",
            anthropic_workspace_id="wrkspc_test123",
        )
        assert settings.anthropic_base_url == "https://aws-anthropic.example.test"
        assert settings.anthropic_workspace_id == "wrkspc_test123"

    def test_no_base_url_no_workspace_id_accepted(self) -> None:
        """Both unset is the first-party/BYOK-only case, not an error."""
        settings = Settings(anthropic_base_url=None, anthropic_workspace_id=None)
        assert settings.anthropic_base_url is None
        assert settings.anthropic_workspace_id is None

    def test_workspace_id_without_base_url_accepted(self) -> None:
        """A workspace id with no base_url still constructs; base_url has
        its own default elsewhere (see create_anthropic_llm)."""
        settings = Settings(anthropic_base_url=None, anthropic_workspace_id="wrkspc_test123")
        assert settings.anthropic_workspace_id == "wrkspc_test123"


class TestBedrockApiKey:
    """AWS_BEARER_TOKEN_BEDROCK is a header value: it must arrive clean or not at all."""

    def test_a_clean_key_is_kept(self) -> None:
        assert Settings(bedrock_api_key="ABSKabc123").bedrock_api_key == "ABSKabc123"

    @pytest.mark.parametrize("raw", ["ABSKabc123\n", "ABSKabc123\r\n", "  ABSKabc123  "])
    def test_surrounding_whitespace_is_trimmed(self, raw: str) -> None:
        """A CRLF env file or a pasted line break made the HTTP client refuse the header."""
        assert Settings(bedrock_api_key=raw).bedrock_api_key == "ABSKabc123"

    @pytest.mark.parametrize("raw", ["", "   ", "\n", "\r\n"])
    def test_a_blank_key_is_unset(self, raw: str) -> None:
        assert Settings(bedrock_api_key=raw).bedrock_api_key is None

    def test_the_other_spelling_is_cleaned_too(self) -> None:
        assert Settings(aws_bearer_token_bedrock="ABSKabc123\r\n").bedrock_api_key == "ABSKabc123"

    @pytest.mark.parametrize("raw", ["ABSK abc", "ABSK\nabc", "ABSK\tabc", "ABSK\x00abc"])
    def test_whitespace_inside_a_key_is_refused_without_echoing_it(self, raw: str) -> None:
        with pytest.raises(ValidationError) as caught:
            Settings(bedrock_api_key=raw)

        message = str(caught.value)
        assert "AWS_BEARER_TOKEN_BEDROCK" in message
        assert "abc" not in message, "the rejected value must not appear in the error"

    def test_no_key_is_the_default(self) -> None:
        assert Settings(bedrock_api_key=None).bedrock_api_key is None
