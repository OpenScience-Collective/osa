"""Unit tests for the Anthropic Claude Platform LLM integration.

No real API calls here: these tests assert real constructed ``ChatAnthropic``/
``CachingChatAnthropic`` objects and the real payload dicts produced by
``_get_request_payload``, never a mocked model standing in for API behavior.
Live-endpoint behavior (thinking, tool calls, streaming against the real
Claude Platform) is covered by tests/test_integration/test_anthropic_platform.py.
"""

import inspect

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import ValidationError

from src.api.config import Settings
from src.core.services.anthropic_llm import (
    CACHE_TTLS,
    DEFAULT_MODEL,
    DEFAULT_THINKING_BUDGET_TOKENS,
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
    """Count cache_control markers anywhere in a request payload.

    A top-level cache_control parameter counts as one, since it asks the
    API for exactly one breakpoint on the last cacheable block.
    """
    count = 1 if payload.get("cache_control") else 0
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


@pytest.mark.parametrize("method", ["_generate", "_stream", "_agenerate", "_astream"])
def test_chat_anthropic_still_calls_get_request_payload(method: str) -> None:
    """Guard the upgrade risk CachingChatAnthropic's docstring calls out.

    Every caching test below calls ``_get_request_payload`` directly, so an
    upstream langchain-anthropic release that keeps the method but stops
    calling it from these real code paths would leave all of them green
    while caching silently stopped working in production. This introspects
    the installed library's source instead of calling it, so it catches that
    upgrade even though nothing here exercises the real HTTP path.
    """
    assert "_get_request_payload" in inspect.getsource(getattr(ChatAnthropic, method))


def test_default_thinking_budget_matches_settings_default() -> None:
    """Lock the module constant and the Settings field default together.

    ``DEFAULT_THINKING_BUDGET_TOKENS`` exists so this module does not have to
    import ``Settings`` for a literal; if the two ever drift, a caller who
    never touches Settings (e.g. constructs a plain Settings() with no env
    vars) would silently get a different thinking budget than one that goes
    through ``create_anthropic_llm``'s settings-based default.
    """
    settings_default = Settings.model_fields["anthropic_thinking_budget_tokens"].default
    assert settings_default == DEFAULT_THINKING_BUDGET_TOKENS


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


class TestOfferedModelsLabels:
    """Tests for OFFERED_MODELS label content.

    The community config endpoint serves these labels verbatim to the
    widget's model menu, so they must be plain display names: no policy
    words like "(default)" baked in. Which model is the default is a
    separate, per-community fact (``default_model``) that the same
    response already carries; encoding it into the label string would
    create two sources of truth that can drift apart.
    """

    def test_labels_do_not_encode_default_status(self) -> None:
        for label in OFFERED_MODELS.values():
            assert "default" not in label.lower()

    def test_labels_are_non_empty_plain_strings(self) -> None:
        for label in OFFERED_MODELS.values():
            assert label.strip() == label
            assert len(label) > 0


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
        # not supplied; compare against a real no-argument instance instead
        # of settings.anthropic_base_url, which is None here and would make
        # the old "!= settings.anthropic_base_url or ... is None" assertion
        # vacuously true regardless of what create_anthropic_llm actually did.
        default_url = ChatAnthropic(model=DEFAULT_MODEL, api_key="x").anthropic_api_url
        assert llm.anthropic_api_url == default_url
        assert not llm.default_headers

    def test_base_url_without_workspace_id_raises(self) -> None:
        """Settings itself now rejects this combination at construction
        time (see tests/test_api/test_config.py), so this exercises it via
        _settings() rather than via create_anthropic_llm."""
        with pytest.raises(ValidationError, match="ANTHROPIC_WORKSPACE_ID"):
            _settings(anthropic_workspace_id=None)

    def test_create_anthropic_llm_rechecks_settings_mutated_after_construction(self) -> None:
        """Defense-in-depth: Settings.validate_workspace_id_with_base_url
        only runs at construction time, and Settings is mutable, so a value
        changed afterward (e.g. by a bug elsewhere) is not re-validated by
        pydantic. create_anthropic_llm's own check still catches it."""
        settings = _settings()
        settings.anthropic_workspace_id = None
        with pytest.raises(RuntimeError, match="ANTHROPIC_WORKSPACE_ID"):
            create_anthropic_llm(settings=settings)

    def test_workspace_id_without_base_url_still_constructs(self) -> None:
        settings = _settings(anthropic_base_url=None)
        llm = create_anthropic_llm(settings=settings)
        assert llm.default_headers == {"anthropic-workspace-id": settings.anthropic_workspace_id}

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
        with_thinking_disabled = create_anthropic_llm(
            model="claude-sonnet-5", temperature=0.7, thinking=None, settings=settings
        )
        assert with_default_thinking.temperature is None
        assert with_thinking_disabled.temperature is None

    def test_dropped_temperature_says_which_reason_applied(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Dropping a caller's parameter should leave a trace, cheaply.

        Debug rather than warning because this runs per request; the loud
        version of the same fact is the config-load warning in
        FAQGenerationConfig.validate_agent_roles.
        """
        settings = _settings()

        with caplog.at_level("DEBUG", logger="src.core.services.anthropic_llm"):
            create_anthropic_llm(
                model="claude-sonnet-5", temperature=0.7, thinking=None, settings=settings
            )

        assert "Dropping temperature=0.7 for claude-sonnet-5" in caplog.text
        assert "only accepts its default temperature" in caplog.text

    def test_temperature_dropped_for_thinking_says_so(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Haiku does accept a temperature, but not while it is thinking."""
        settings = _settings()

        with caplog.at_level("DEBUG", logger="src.core.services.anthropic_llm"):
            create_anthropic_llm(model="claude-haiku-4-5", temperature=0.5, settings=settings)

        assert "extended thinking is on" in caplog.text

    def test_honored_temperature_is_not_reported_as_dropped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        settings = _settings()

        with caplog.at_level("DEBUG", logger="src.core.services.anthropic_llm"):
            llm = create_anthropic_llm(
                model="claude-haiku-4-5", temperature=0.5, thinking=None, settings=settings
            )

        assert llm.temperature == 0.5
        assert "Dropping temperature" not in caplog.text

    def test_default_thinking_applied_per_model(self) -> None:
        settings = _settings()
        haiku = create_anthropic_llm(model="claude-haiku-4-5", settings=settings)
        sonnet = create_anthropic_llm(model="claude-sonnet-5", settings=settings)
        assert haiku.thinking == {"type": "enabled", "budget_tokens": 2048}
        assert sonnet.thinking == {"type": "adaptive"}

    def test_thinking_budget_from_settings_is_used(self) -> None:
        """A distinct (non-default-literal) value proves settings are plumbed through.

        The default budget in Settings and in this module's own
        DEFAULT_THINKING_BUDGET_TOKENS are both 2048, so a test using that
        literal would still pass if the settings value were silently
        ignored and the module fell back to its own constant instead.
        """
        settings = _settings(anthropic_thinking_budget_tokens=4096)
        llm = create_anthropic_llm(model="claude-haiku-4-5", settings=settings)
        assert llm.thinking["budget_tokens"] == 4096

    def test_default_thinking_budget_conflicts_with_max_tokens(self) -> None:
        """Exercise the budget-vs-max_tokens conflict through the public entry point."""
        settings = _settings()
        with pytest.raises(ValueError, match="below max_tokens"):
            create_anthropic_llm(model="claude-haiku-4-5", max_tokens=1000, settings=settings)

    def test_explicit_none_thinking_disables(self) -> None:
        settings = _settings()
        llm = create_anthropic_llm(model="claude-haiku-4-5", thinking=None, settings=settings)
        assert llm.thinking is None

    def test_explicit_none_thinking_sends_disabled_type_on_sonnet_5(self) -> None:
        """claude-sonnet-5 has no bare "off"; an omitted key means adaptive-on.

        thinking=None must therefore produce an explicit {"type": "disabled"}
        rather than omitting the key (the haiku case, covered above by
        test_explicit_none_thinking_disables).
        """
        settings = _settings()
        llm = create_anthropic_llm(model="claude-sonnet-5", thinking=None, settings=settings)
        assert llm.thinking == {"type": "disabled"}

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

    def test_conversation_gets_a_cache_breakpoint(self) -> None:
        """The conversation prefix is marked for caching, in either shape.

        Against the direct Anthropic API, langchain-anthropic forwards
        cache_control as a top-level request parameter and the API attaches
        the breakpoint to the last cacheable block. On a transport that does
        not accept that parameter it expands the kwarg into a block-level
        marker instead. Either satisfies the intent, so assert the intent
        rather than one library's placement, and fail when neither happened.
        """
        llm = self._llm()
        payload = llm._get_request_payload(
            [SystemMessage(content="You are a helpful assistant."), HumanMessage(content="Hi")]
        )
        top_level = payload.get("cache_control")
        last_message_content = payload["messages"][-1]["content"]
        block_level = isinstance(last_message_content, list) and any(
            isinstance(block, dict) and "cache_control" in block for block in last_message_content
        )
        assert top_level == {"type": "ephemeral"} or block_level, (
            f"No conversation cache breakpoint in either shape: payload={payload!r}"
        )

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
        expected = {"type": "ephemeral", "ttl": "1h"}
        assert payload["system"][-1]["cache_control"] == expected
        # The conversation breakpoint carries the same TTL, wherever the
        # library places it (top-level parameter or block-level marker).
        last_message_content = payload["messages"][-1]["content"]
        block_markers = (
            [
                block["cache_control"]
                for block in last_message_content
                if isinstance(block, dict) and "cache_control" in block
            ]
            if isinstance(last_message_content, list)
            else []
        )
        assert payload.get("cache_control") == expected or expected in block_markers

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


class TestCachingChatAnthropicCacheTtlValidation:
    """Tests that cache_ttl validity is enforced on the class itself (fix 5)."""

    def test_direct_construction_with_invalid_ttl_raises(self) -> None:
        """Constructing the class directly must not bypass the CACHE_TTLS check."""
        with pytest.raises(ValidationError, match="Unsupported prompt cache TTL"):
            CachingChatAnthropic(
                model="claude-haiku-4-5", api_key="test-key", max_tokens=100, cache_ttl="10m"
            )

    @pytest.mark.parametrize("ttl", CACHE_TTLS)
    def test_direct_construction_with_valid_ttl_succeeds(self, ttl: str) -> None:
        llm = CachingChatAnthropic(
            model="claude-haiku-4-5", api_key="test-key", max_tokens=100, cache_ttl=ttl
        )
        assert llm.cache_ttl == ttl


class TestCachingChatAnthropicSystemListForm:
    """Tests for the list-form system content branch, previously uncovered."""

    def _llm(self, **overrides: object) -> CachingChatAnthropic:
        settings = _settings(**overrides)
        llm = create_anthropic_llm(model="claude-haiku-4-5", thinking=None, settings=settings)
        assert isinstance(llm, CachingChatAnthropic)
        return llm

    def test_breakpoint_lands_on_last_block_only(self) -> None:
        llm = self._llm()
        system_message = SystemMessage(
            content=[
                {"type": "text", "text": "Block one."},
                {"type": "text", "text": "Block two."},
            ]
        )
        payload = llm._get_request_payload([system_message, HumanMessage(content="Hi")])
        system = payload["system"]
        assert "cache_control" not in system[0]
        assert system[-1]["cache_control"] == {"type": "ephemeral"}

    def test_already_marked_system_list_is_not_double_marked(self) -> None:
        llm = self._llm()
        system_message = SystemMessage(
            content=[
                {"type": "text", "text": "Block one.", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "Block two."},
            ]
        )
        payload = llm._get_request_payload([system_message, HumanMessage(content="Hi")])
        system = payload["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in system[-1]

    def test_callers_system_message_is_not_mutated(self) -> None:
        """Regression guard for fix 1.

        langchain_anthropic's _format_messages reuses the caller's own dict
        objects for list-content system messages, so applying the cache
        breakpoint must copy rather than mutate them in place; otherwise a
        later request built from the same SystemMessage instance would see
        a stale cache_control marker and silently ship the wrong TTL.
        """
        llm = self._llm()
        system_message = SystemMessage(
            content=[
                {"type": "text", "text": "Block one."},
                {"type": "text", "text": "Block two."},
            ]
        )
        original_content = [dict(block) for block in system_message.content]

        llm._get_request_payload([system_message, HumanMessage(content="Hi")])

        assert system_message.content == original_content
        assert "cache_control" not in system_message.content[-1]
