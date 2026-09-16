"""Unit tests for the Anthropic Claude Platform LLM integration.

No real API calls here: these tests assert real constructed ``ChatAnthropic``/
``CachingChatAnthropic`` objects and the real payload dicts produced by
``_get_request_payload``, never a mocked model standing in for API behavior.
Live-endpoint behavior (thinking, tool calls, streaming against the real
Claude Platform) is covered by tests/test_integration/test_anthropic_platform.py.
"""

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.api.config import Settings
from src.core.services.anthropic_llm import (
    CACHE_TTLS,
    DEFAULT_MODEL,
    MIN_THINKING_BUDGET_TOKENS,
    MODEL_ALIASES,
    OFFERED_MODELS,
    CachingChatAnthropic,
    _validate_thinking,
    create_anthropic_llm,
    default_thinking,
    normalize_model,
)


def _settings(**overrides: object) -> Settings:
    """Build a Settings instance with explicit Anthropic fields for tests.

    Every anthropic_* field is passed explicitly (even when overrides do not
    touch it) so tests never depend on, or accidentally exercise, the real
    values Settings would otherwise load from the worktree's .env file.
    """
    defaults: dict[str, object] = {
        "anthropic_api_key": "test-server-key",
        "anthropic_base_url": "https://aws-anthropic.example.test",
        "anthropic_workspace_id": "wrkspc_test123",
        "anthropic_thinking_budget_tokens": 2048,
        "anthropic_max_output_tokens": 8000,
        "anthropic_cache_ttl": "5m",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _count_cache_control(payload: dict) -> int:
    """Count cache_control markers anywhere in a request payload."""
    count = 0
    system = payload.get("system")
    if isinstance(system, list):
        count += sum(1 for block in system if isinstance(block, dict) and "cache_control" in block)
    for message in payload.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            count += sum(
                1 for block in content if isinstance(block, dict) and "cache_control" in block
            )
    return count


@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"Sunny in {city}"


class TestNormalizeModel:
    """Tests for normalize_model()."""

    def test_offered_models_resolve_to_themselves(self) -> None:
        for model in OFFERED_MODELS:
            assert normalize_model(model) == model

    def test_none_returns_default(self) -> None:
        assert normalize_model(None) == DEFAULT_MODEL

    def test_all_aliases_resolve_to_an_offered_model(self) -> None:
        # Iterate the real alias table rather than hardcoding entries, so a
        # future alias addition is covered automatically.
        for alias, resolved in MODEL_ALIASES.items():
            assert normalize_model(alias) == resolved
            assert resolved in OFFERED_MODELS

    def test_unoffered_openai_model_raises(self) -> None:
        with pytest.raises(ValueError, match="not available"):
            normalize_model("openai/gpt-5")

    def test_unoffered_claude_model_raises(self) -> None:
        with pytest.raises(ValueError, match="not available"):
            normalize_model("claude-opus-4-6")


class TestDefaultThinking:
    """Tests for default_thinking()."""

    def test_sonnet_5_is_adaptive(self) -> None:
        assert default_thinking("claude-sonnet-5") == {"type": "adaptive"}

    def test_haiku_is_budget_shaped(self) -> None:
        assert default_thinking("claude-haiku-4-5", budget=2048) == {
            "type": "enabled",
            "budget_tokens": 2048,
        }

    def test_zero_budget_disables(self) -> None:
        assert default_thinking("claude-haiku-4-5", budget=0) is None

    def test_negative_budget_disables(self) -> None:
        assert default_thinking("claude-haiku-4-5", budget=-1) is None


class TestValidateThinking:
    """Tests for _validate_thinking()."""

    def test_rejects_enabled_on_sonnet_5(self) -> None:
        with pytest.raises(ValueError, match="adaptive"):
            _validate_thinking({"type": "enabled", "budget_tokens": 2048}, "claude-sonnet-5", 8000)

    def test_rejects_adaptive_on_haiku(self) -> None:
        with pytest.raises(ValueError, match="no adaptive thinking mode"):
            _validate_thinking({"type": "adaptive"}, "claude-haiku-4-5", 8000)

    def test_rejects_budget_below_minimum(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            _validate_thinking(
                {"type": "enabled", "budget_tokens": MIN_THINKING_BUDGET_TOKENS - 1},
                "claude-haiku-4-5",
                8000,
            )

    def test_rejects_budget_at_or_above_max_tokens(self) -> None:
        with pytest.raises(ValueError, match="below max_tokens"):
            _validate_thinking({"type": "enabled", "budget_tokens": 8000}, "claude-haiku-4-5", 8000)

    def test_accepts_disabled_on_sonnet_5(self) -> None:
        _validate_thinking({"type": "disabled"}, "claude-sonnet-5", 8000)

    def test_accepts_disabled_on_haiku(self) -> None:
        _validate_thinking({"type": "disabled"}, "claude-haiku-4-5", 8000)

    def test_accepts_valid_budget_on_haiku(self) -> None:
        _validate_thinking(
            {"type": "enabled", "budget_tokens": MIN_THINKING_BUDGET_TOKENS},
            "claude-haiku-4-5",
            8000,
        )


class TestCreateAnthropicLLMCredentials:
    """Tests for server-mode vs BYOK credential handling in create_anthropic_llm()."""

    def test_server_mode_sets_base_url_and_workspace_header(self) -> None:
        settings = _settings()
        llm = create_anthropic_llm(settings=settings)
        assert llm.anthropic_api_url == settings.anthropic_base_url
        assert llm.default_headers == {"anthropic-workspace-id": settings.anthropic_workspace_id}

    def test_server_mode_missing_key_raises(self) -> None:
        settings = _settings(anthropic_api_key=None)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            create_anthropic_llm(settings=settings)

    def test_server_mode_without_base_url_or_workspace_omits_them(self) -> None:
        settings = _settings(anthropic_base_url=None, anthropic_workspace_id=None)
        llm = create_anthropic_llm(settings=settings)
        # ChatAnthropic falls back to its own client default when base_url is
        # not supplied; the point under test is that we did not force one.
        assert (
            llm.anthropic_api_url != settings.anthropic_base_url
            or settings.anthropic_base_url is None
        )
        assert not llm.default_headers

    def test_byok_mode_pins_first_party_url_and_sends_no_workspace_header(self) -> None:
        settings = _settings()
        llm = create_anthropic_llm(api_key="byok-key", settings=settings)
        assert llm.anthropic_api_url == "https://api.anthropic.com"
        assert not llm.default_headers


class TestCreateAnthropicLLMBehavior:
    """Tests for streaming, temperature, thinking, and caching selection."""

    def test_streaming_always_true(self) -> None:
        settings = _settings()
        assert create_anthropic_llm(settings=settings).streaming is True
        assert create_anthropic_llm(api_key="byok-key", settings=settings).streaming is True

    def test_temperature_absent_when_thinking_on_default(self) -> None:
        """Haiku's default thinking is on, so temperature must be dropped."""
        settings = _settings()
        llm = create_anthropic_llm(model="claude-haiku-4-5", temperature=0.5, settings=settings)
        assert llm.temperature is None

    def test_temperature_present_for_haiku_with_thinking_explicitly_off(self) -> None:
        settings = _settings()
        llm = create_anthropic_llm(
            model="claude-haiku-4-5", temperature=0.5, thinking=None, settings=settings
        )
        assert llm.temperature == 0.5

    def test_temperature_always_absent_for_sonnet_5(self) -> None:
        settings = _settings()
        with_default_thinking = create_anthropic_llm(
            model="claude-sonnet-5", temperature=0.7, settings=settings
        )
        with_thinking_off = create_anthropic_llm(
            model="claude-sonnet-5", temperature=0.7, thinking=None, settings=settings
        )
        assert with_default_thinking.temperature is None
        assert with_thinking_off.temperature is None

    def test_default_thinking_applied_per_model(self) -> None:
        settings = _settings()
        haiku = create_anthropic_llm(model="claude-haiku-4-5", settings=settings)
        sonnet = create_anthropic_llm(model="claude-sonnet-5", settings=settings)
        assert haiku.thinking == {"type": "enabled", "budget_tokens": 2048}
        assert sonnet.thinking == {"type": "adaptive"}

    def test_explicit_none_thinking_disables(self) -> None:
        settings = _settings()
        llm = create_anthropic_llm(model="claude-haiku-4-5", thinking=None, settings=settings)
        assert llm.thinking is None

    def test_invalid_thinking_for_model_raises(self) -> None:
        settings = _settings()
        with pytest.raises(ValueError, match="adaptive"):
            create_anthropic_llm(
                model="claude-sonnet-5",
                thinking={"type": "enabled", "budget_tokens": 2048},
                settings=settings,
            )

    def test_unsupported_cache_ttl_raises(self) -> None:
        settings = _settings()
        with pytest.raises(ValueError, match="Unsupported prompt cache TTL"):
            create_anthropic_llm(settings=settings, cache_ttl="10m")

    @pytest.mark.parametrize("ttl", CACHE_TTLS)
    def test_supported_cache_ttls_accepted(self, ttl: str) -> None:
        settings = _settings()
        llm = create_anthropic_llm(settings=settings, cache_ttl=ttl)
        assert llm.cache_ttl == ttl

    def test_enable_caching_true_returns_caching_subclass(self) -> None:
        settings = _settings()
        llm = create_anthropic_llm(settings=settings, enable_caching=True)
        assert isinstance(llm, CachingChatAnthropic)

    def test_enable_caching_false_returns_plain_chat_anthropic(self) -> None:
        settings = _settings()
        llm = create_anthropic_llm(settings=settings, enable_caching=False)
        assert type(llm) is ChatAnthropic

    def test_max_tokens_defaults_from_settings(self) -> None:
        settings = _settings(anthropic_max_output_tokens=4096)
        llm = create_anthropic_llm(model="claude-haiku-4-5", thinking=None, settings=settings)
        assert llm.max_tokens == 4096

    def test_max_tokens_override_wins_over_settings(self) -> None:
        settings = _settings(anthropic_max_output_tokens=4096)
        llm = create_anthropic_llm(
            model="claude-haiku-4-5", thinking=None, max_tokens=1234, settings=settings
        )
        assert llm.max_tokens == 1234


class TestCachingChatAnthropicPayload:
    """Tests for CachingChatAnthropic._get_request_payload()."""

    def _llm(self, model: str = "claude-haiku-4-5", **overrides: object) -> CachingChatAnthropic:
        settings = _settings(**overrides)
        llm = create_anthropic_llm(model=model, thinking=None, settings=settings)
        assert isinstance(llm, CachingChatAnthropic)
        return llm

    def test_system_becomes_block_form_with_cache_control(self) -> None:
        llm = self._llm()
        payload = llm._get_request_payload(
            [SystemMessage(content="You are a helpful assistant."), HumanMessage(content="Hi")]
        )
        system = payload["system"]
        assert isinstance(system, list)
        assert system[-1]["type"] == "text"
        assert system[-1]["cache_control"] == {"type": "ephemeral"}

    def test_trailing_message_gets_cache_breakpoint(self) -> None:
        llm = self._llm()
        payload = llm._get_request_payload(
            [SystemMessage(content="You are a helpful assistant."), HumanMessage(content="Hi")]
        )
        last_message_content = payload["messages"][-1]["content"]
        assert isinstance(last_message_content, list)
        assert last_message_content[-1]["cache_control"] == {"type": "ephemeral"}

    def test_total_cache_control_markers_within_anthropic_limit(self) -> None:
        llm = self._llm()
        payload = llm._get_request_payload(
            [SystemMessage(content="You are a helpful assistant."), HumanMessage(content="Hi")]
        )
        markers = _count_cache_control(payload)
        assert 1 <= markers <= 4

    def test_non_default_ttl_adds_ttl_field(self) -> None:
        llm = self._llm(anthropic_cache_ttl="1h")
        payload = llm._get_request_payload(
            [SystemMessage(content="sys"), HumanMessage(content="hi")]
        )
        assert payload["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    def test_tool_bound_model_still_caches(self) -> None:
        """bind_tools() must not bypass caching (the bug the subclass avoids)."""
        llm = self._llm()
        bound = llm.bind_tools([get_weather])

        # bind_tools() returns a RunnableBinding around the same
        # CachingChatAnthropic instance, proving tool binding does not
        # silently drop into a plain, non-caching model.
        underlying = bound.bound
        assert isinstance(underlying, CachingChatAnthropic)

        payload = underlying._get_request_payload(
            [SystemMessage(content="You are a helpful assistant."), HumanMessage(content="Hi")]
        )
        assert _count_cache_control(payload) >= 1
        assert payload["system"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_plain_chat_anthropic_has_no_cache_control(self) -> None:
        """Regression guard: catches the subclass silently becoming a no-op."""
        plain = ChatAnthropic(model="claude-haiku-4-5", api_key="test-key", max_tokens=100)
        payload = plain._get_request_payload(
            [SystemMessage(content="You are a helpful assistant."), HumanMessage(content="Hi")]
        )
        assert _count_cache_control(payload) == 0
