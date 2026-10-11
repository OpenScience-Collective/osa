"""Tests for src.api.config.Settings validation.

Real Settings construction throughout -- no mocks, since the whole point is
to verify pydantic's own validation behavior.
"""

import logging

import pytest
from pydantic import ValidationError

from src.api.config import RETIRED_ENV_VARS, Settings, get_settings


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


class TestRetiredEnvironmentVariables:
    """A retired variable is ignored, and said to be, so an operator who had set it on
    purpose is not silently moved to a different behavior (issue #548)."""

    @pytest.fixture(autouse=True)
    def _fresh_settings(self):
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.mark.parametrize("name", sorted(RETIRED_ENV_VARS))
    def test_a_retired_variable_that_is_set_is_warned_about_by_name_with_its_replacement(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, name: str
    ) -> None:
        monkeypatch.setenv(name, "1024")
        with caplog.at_level(logging.WARNING, logger="src.api.config"):
            get_settings()
        messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(name in m and "reasoning_effort" in m for m in messages), messages

    @pytest.mark.parametrize("name", sorted(RETIRED_ENV_VARS))
    def test_the_name_is_matched_in_any_case(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, name: str
    ) -> None:
        """Settings reads names case-insensitively, so a lowercase export counts."""
        monkeypatch.delenv(name, raising=False)  # a developer's own shell may export it
        monkeypatch.setenv(name.lower(), "1024")
        with caplog.at_level(logging.WARNING, logger="src.api.config"):
            get_settings()
        assert any(name in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("name", sorted(RETIRED_ENV_VARS))
    def test_nothing_is_said_when_it_is_not_set(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, name: str
    ) -> None:
        for variant in (name, name.lower()):
            monkeypatch.delenv(variant, raising=False)
        with caplog.at_level(logging.WARNING, logger="src.api.config"):
            get_settings()
        assert not any(name in r.getMessage() for r in caplog.records)

    def test_a_retired_variable_does_not_stop_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in RETIRED_ENV_VARS:
            monkeypatch.setenv(name, "not even a number")
        assert get_settings() is not None

    def test_a_retired_variable_in_the_env_file_is_warned_about_too(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path
    ) -> None:
        """Settings reads .env as well as the environment, so a retired name there is unused
        all the same, and is reported the same way."""
        name = sorted(RETIRED_ENV_VARS)[0]
        for variant in (name, name.lower()):
            monkeypatch.delenv(variant, raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(f"{name}=1024\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="src.api.config"):
            get_settings()
        assert any(name in r.getMessage() for r in caplog.records)


class TestDefaultModel:
    """DEFAULT_MODEL is the model a Bedrock default falls back to, so a typo in it must stop
    startup rather than change the model family for every such request."""

    def test_a_default_model_that_is_not_offered_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            Settings(default_model="claude-typo-9-9")

    def test_a_class_name_and_an_offered_id_are_accepted(self) -> None:
        assert Settings(default_model="haiku").default_model == "haiku"
        assert Settings(default_model="claude-haiku-5-5").default_model == "claude-haiku-5-5"
