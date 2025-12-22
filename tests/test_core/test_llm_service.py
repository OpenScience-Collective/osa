"""Tests for LLM service.

These tests verify the LLM service configuration logic without
making actual API calls to LLM providers.
"""

import pytest

from src.api.config import Settings
from src.core.services.llm import LLMService, get_llm_service


class TestLLMServiceModelMappings:
    """Tests for model name mappings."""

    def test_openai_models_mapping(self) -> None:
        """LLMService should have OpenAI model mappings."""
        assert "gpt-4o" in LLMService.OPENAI_MODELS
        assert "gpt-4o-mini" in LLMService.OPENAI_MODELS
        assert "gpt-4-turbo" in LLMService.OPENAI_MODELS

    def test_anthropic_models_mapping(self) -> None:
        """LLMService should have Anthropic model mappings."""
        assert "claude-3-5-sonnet" in LLMService.ANTHROPIC_MODELS
        assert "claude-3-5-haiku" in LLMService.ANTHROPIC_MODELS
        assert "claude-3-opus" in LLMService.ANTHROPIC_MODELS

    def test_default_model_from_settings(self) -> None:
        """LLMService should get default model from settings."""
        settings = Settings(default_model="openai/gpt-oss-120b")
        service = LLMService(settings=settings)
        assert service.default_model == "openai/gpt-oss-120b"

    def test_test_model_from_settings(self) -> None:
        """LLMService should get test model from settings."""
        settings = Settings(test_model="openai/gpt-oss-120b")
        service = LLMService(settings=settings)
        assert service.test_model == "openai/gpt-oss-120b"


class TestLLMServiceInitialization:
    """Tests for LLMService initialization."""

    def test_init_with_settings(self) -> None:
        """LLMService should accept custom settings."""
        settings = Settings(openai_api_key="test-key")
        service = LLMService(settings=settings)
        assert service.settings.openai_api_key == "test-key"

    def test_init_with_default_settings(self) -> None:
        """LLMService should use default settings when none provided."""
        service = LLMService()
        assert service.settings is not None


class TestLLMServiceGetModel:
    """Tests for get_model method."""

    def test_get_model_unknown_model_raises(self) -> None:
        """get_model should raise for unknown model names."""
        service = LLMService()
        with pytest.raises(ValueError, match="Unknown model"):
            service.get_model("unknown-model-xyz")

    def test_get_model_openai_without_key_raises(self) -> None:
        """get_model should raise when OpenAI key is missing."""
        settings = Settings(openai_api_key=None)
        service = LLMService(settings=settings)
        with pytest.raises(ValueError, match="OpenAI API key required"):
            service.get_model("gpt-4o")

    def test_get_model_anthropic_without_key_raises(self) -> None:
        """get_model should raise when Anthropic key is missing."""
        settings = Settings(anthropic_api_key=None)
        service = LLMService(settings=settings)
        with pytest.raises(ValueError, match="Anthropic API key required"):
            service.get_model("claude-3-5-sonnet")

    def test_get_model_with_api_key_override(self) -> None:
        """get_model should use provided API key over settings."""
        settings = Settings(openai_api_key=None)
        service = LLMService(settings=settings)
        # Should not raise because we provide the key
        model = service.get_model("gpt-4o", api_key="override-key")
        assert model is not None

    def test_get_model_openai(self) -> None:
        """get_model should return OpenAI model when configured."""
        settings = Settings(openai_api_key="test-openai-key")
        service = LLMService(settings=settings)
        model = service.get_model("gpt-4o-mini")
        assert model is not None
        assert model.model_name == "gpt-4o-mini"

    def test_get_model_anthropic(self) -> None:
        """get_model should return Anthropic model when configured."""
        settings = Settings(anthropic_api_key="test-anthropic-key")
        service = LLMService(settings=settings)
        model = service.get_model("claude-3-5-haiku")
        assert model is not None

    def test_get_model_default_openrouter(self) -> None:
        """get_model should use default OpenRouter model when none specified."""
        settings = Settings(openrouter_api_key="test-key")
        service = LLMService(settings=settings)
        model = service.get_model()  # Uses default_model from settings
        assert model is not None

    def test_get_model_openrouter_with_provider(self) -> None:
        """get_model should pass provider preference to OpenRouter."""
        settings = Settings(
            openrouter_api_key="test-key",
            default_model="openai/gpt-oss-120b",
            default_model_provider="Cerebras",
        )
        service = LLMService(settings=settings)
        model = service.get_model()
        assert model is not None
        # The provider should be in extra_body
        assert model.extra_body == {"provider": {"order": ["Cerebras"]}}

    def test_get_test_model(self) -> None:
        """get_test_model should use test model settings."""
        settings = Settings(
            openrouter_api_key="test-key",
            test_model="openai/gpt-oss-120b",
            test_model_provider="Cerebras",
        )
        service = LLMService(settings=settings)
        model = service.get_test_model()
        assert model is not None
        assert model.extra_body == {"provider": {"order": ["Cerebras"]}}

    def test_get_model_temperature(self) -> None:
        """get_model should apply temperature setting."""
        settings = Settings(openai_api_key="test-key")
        service = LLMService(settings=settings)
        model = service.get_model("gpt-4o-mini", temperature=0.2)
        assert model.temperature == 0.2


class TestLangfuseIntegration:
    """Tests for LangFuse integration."""

    def test_langfuse_handler_not_configured(self) -> None:
        """get_langfuse_handler should return None when not configured."""
        settings = Settings(langfuse_public_key=None, langfuse_secret_key=None)
        service = LLMService(settings=settings)
        handler = service.get_langfuse_handler()
        assert handler is None

    def test_langfuse_handler_configured(self) -> None:
        """get_langfuse_handler should return handler when configured."""
        settings = Settings(
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
        )
        service = LLMService(settings=settings)
        handler = service.get_langfuse_handler()
        assert handler is not None

    def test_langfuse_handler_with_trace_id(self) -> None:
        """get_langfuse_handler should accept custom trace ID."""
        settings = Settings(
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
        )
        service = LLMService(settings=settings)
        handler = service.get_langfuse_handler(trace_id="custom-trace-123")
        assert handler is not None

    def test_config_with_tracing_no_langfuse(self) -> None:
        """get_config_with_tracing should return empty config when not configured."""
        settings = Settings(langfuse_public_key=None)
        service = LLMService(settings=settings)
        config = service.get_config_with_tracing()
        assert config == {}

    def test_config_with_tracing_langfuse(self) -> None:
        """get_config_with_tracing should include callbacks when configured."""
        settings = Settings(
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
        )
        service = LLMService(settings=settings)
        config = service.get_config_with_tracing(trace_id="test")
        assert "callbacks" in config
        assert len(config["callbacks"]) == 1


class TestLLMServiceSingleton:
    """Tests for LLM service singleton."""

    def test_get_llm_service_returns_instance(self) -> None:
        """get_llm_service should return a service instance."""
        service = get_llm_service()
        assert isinstance(service, LLMService)

    def test_get_llm_service_singleton(self) -> None:
        """get_llm_service should return the same instance."""
        service1 = get_llm_service()
        service2 = get_llm_service()
        assert service1 is service2
