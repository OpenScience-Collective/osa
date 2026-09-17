"""Tests for the LangFuse observability helper.

The model-construction surface this module used to carry (direct OpenAI,
direct Anthropic, and OpenRouter factories) was removed in Phase 2 of the
Claude Platform on AWS migration, because nothing in production called it.
Model construction is covered by tests/test_core/test_anthropic_llm.py and
tests/test_core/test_litellm_llm.py.
"""

import pytest

from src.api.config import Settings
from src.core.services.llm import LLMService, get_llm_service

# Check if langfuse is available
try:
    import langfuse  # noqa: F401

    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False


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


@pytest.mark.skipif(not LANGFUSE_AVAILABLE, reason="Langfuse not installed")
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
