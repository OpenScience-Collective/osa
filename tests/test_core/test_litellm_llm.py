"""Tests for LiteLLM OpenRouter integration.

These tests verify the OpenRouter LLM creation logic, particularly the
provider auto-selection behavior for Anthropic models.
"""

from src.core.services.litellm_llm import create_openrouter_llm


class TestCreateOpenRouterLLMProviderSelection:
    """Tests for provider auto-selection in create_openrouter_llm."""

    def test_anthropic_model_uses_anthropic_provider(self) -> None:
        """Anthropic models should auto-select Anthropic provider."""
        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
        )
        # Access the wrapped LLM's model_kwargs
        assert llm.llm.model_kwargs["provider"] == {"order": ["Anthropic"]}

    def test_anthropic_model_overrides_default_provider(self) -> None:
        """Anthropic models should override any specified provider."""
        llm = create_openrouter_llm(
            model="anthropic/claude-sonnet-4.5",
            api_key="test-key",
            provider="DeepInfra/FP8",  # Should be ignored for Anthropic models
        )
        # Should use Anthropic provider, not the specified one
        assert llm.llm.model_kwargs["provider"] == {"order": ["Anthropic"]}

    def test_anthropic_model_with_different_version(self) -> None:
        """All Anthropic model versions should auto-select Anthropic provider."""
        llm = create_openrouter_llm(
            model="anthropic/claude-opus-4",
            api_key="test-key",
            provider="SomeOtherProvider",
        )
        assert llm.llm.model_kwargs["provider"] == {"order": ["Anthropic"]}

    def test_non_anthropic_model_uses_specified_provider(self) -> None:
        """Non-Anthropic models should use the specified provider."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            provider="Cerebras",
        )
        assert llm.llm.model_kwargs["provider"] == {"order": ["Cerebras"]}

    def test_non_anthropic_model_with_deepinfra_provider(self) -> None:
        """Non-Anthropic models should use DeepInfra provider when specified."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            provider="DeepInfra/FP8",
        )
        assert llm.llm.model_kwargs["provider"] == {"order": ["DeepInfra/FP8"]}

    def test_non_anthropic_model_without_provider(self) -> None:
        """Non-Anthropic models with no provider should have no provider key."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            provider=None,
        )
        assert "provider" not in llm.llm.model_kwargs

    def test_default_model_with_default_provider(self) -> None:
        """Default model with default provider should use the specified provider."""
        llm = create_openrouter_llm(
            api_key="test-key",
            # Uses default model="openai/gpt-oss-120b" and provider="Cerebras"
        )
        assert llm.llm.model_kwargs["provider"] == {"order": ["Cerebras"]}


class TestCreateOpenRouterLLMConfiguration:
    """Tests for general LLM configuration options."""

    def test_model_prefix(self) -> None:
        """LLM should use openrouter/ prefix for LiteLLM."""
        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
        )
        # LiteLLM should receive the model with openrouter/ prefix
        assert llm.llm.model.startswith("openrouter/")

    def test_temperature_configuration(self) -> None:
        """LLM should respect temperature parameter."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            temperature=0.5,
        )
        assert llm.llm.temperature == 0.5

    def test_max_tokens_configuration(self) -> None:
        """LLM should respect max_tokens parameter."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            max_tokens=1000,
        )
        assert llm.llm.max_tokens == 1000

    def test_user_id_for_sticky_routing(self) -> None:
        """LLM should include user ID for cache optimization."""
        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
            user_id="test-user-123",
        )
        assert llm.llm.model_kwargs["user"] == "test-user-123"

    def test_extra_headers_for_openrouter(self) -> None:
        """LLM should include required OpenRouter headers."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
        )
        headers = llm.llm.model_kwargs["extra_headers"]
        assert "HTTP-Referer" in headers
        assert "X-Title" in headers
        assert headers["HTTP-Referer"] == "https://osc.earth/osa"
        assert headers["X-Title"] == "Open Science Assistant"

    def test_streaming_enabled_by_default(self) -> None:
        """LLM should have streaming enabled for LangGraph events."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
        )
        assert llm.llm.streaming is True


class TestCreateOpenRouterLLMCachingWrapper:
    """Tests for caching wrapper integration."""

    def test_returns_caching_wrapper(self) -> None:
        """create_openrouter_llm should return a CachingLLMWrapper."""
        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
        )
        # Should be wrapped for caching
        from src.core.services.litellm_llm import CachingLLMWrapper

        assert isinstance(llm, CachingLLMWrapper)

    def test_caching_enabled_by_default(self) -> None:
        """Caching should be enabled by default."""
        from src.core.services.litellm_llm import CachingLLMWrapper

        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
        )
        # Should be wrapped by default
        assert isinstance(llm, CachingLLMWrapper)

    def test_caching_can_be_disabled(self) -> None:
        """Caching should be disableable via parameter."""
        from langchain_litellm import ChatLiteLLM

        from src.core.services.litellm_llm import CachingLLMWrapper

        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
            enable_caching=False,
        )
        # Should NOT be wrapped when disabled
        assert not isinstance(llm, CachingLLMWrapper)
        assert isinstance(llm, ChatLiteLLM)
