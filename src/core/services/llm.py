"""LLM service with provider abstraction and LangFuse observability."""

import os
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langfuse.langchain import CallbackHandler as LangfuseHandler

from src.api.config import Settings, get_settings

# OpenRouter API base URL
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMService:
    """Service for creating and managing LLM instances with observability."""

    # Model mappings for direct OpenAI API
    OPENAI_MODELS = {
        "gpt-4o": "gpt-4o",
        "gpt-4o-mini": "gpt-4o-mini",
        "gpt-4-turbo": "gpt-4-turbo",
        "gpt-4": "gpt-4",
        "gpt-3.5-turbo": "gpt-3.5-turbo",
    }

    # Model mappings for direct Anthropic API
    ANTHROPIC_MODELS = {
        "claude-3-5-sonnet": "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku": "claude-3-5-haiku-20241022",
        "claude-3-opus": "claude-3-opus-20240229",
        "claude-3-sonnet": "claude-3-sonnet-20240229",
        "claude-3-haiku": "claude-3-haiku-20240307",
    }

    @property
    def default_model(self) -> str:
        """Get the default model from settings."""
        return self.settings.default_model

    @property
    def test_model(self) -> str:
        """Get the test model from settings."""
        return self.settings.test_model

    def get_test_model(
        self,
        api_key: str | None = None,
        temperature: float | None = None,
        streaming: bool = True,
    ) -> BaseChatModel:
        """Get a model instance configured for testing.

        Convenience method that uses settings.test_model and settings.test_model_provider.
        """
        return self.get_model(
            model_name=self.settings.test_model,
            api_key=api_key,
            temperature=temperature,
            streaming=streaming,
            provider=self.settings.test_model_provider,
        )

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the LLM service.

        Args:
            settings: Optional settings instance. If not provided, uses defaults.
        """
        self.settings = settings or get_settings()

    def get_langfuse_handler(
        self,
        trace_id: str | None = None,
    ) -> LangfuseHandler | None:
        """Create a LangFuse callback handler for tracing.

        LangFuse uses environment variables for authentication:
        - LANGFUSE_PUBLIC_KEY
        - LANGFUSE_SECRET_KEY
        - LANGFUSE_HOST

        This method sets these from settings before creating the handler.

        Args:
            trace_id: Optional custom trace ID for the root LangChain run.

        Returns None if LangFuse is not configured.
        """
        if not self.settings.langfuse_public_key or not self.settings.langfuse_secret_key:
            return None

        # Set environment variables for LangFuse client
        os.environ["LANGFUSE_PUBLIC_KEY"] = self.settings.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = self.settings.langfuse_secret_key
        os.environ["LANGFUSE_HOST"] = self.settings.langfuse_host

        # Create handler with optional trace context
        if trace_id:
            return LangfuseHandler(trace_context={"trace_id": trace_id})
        return LangfuseHandler()

    def get_model(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        streaming: bool = True,
        provider: str | None = None,
    ) -> BaseChatModel:
        """Get a chat model instance.

        Args:
            model_name: Model name. Supports:
                - OpenRouter format: 'creator/model' (e.g., 'openai/gpt-oss-120b', 'qwen/qwen3-235b')
                - Direct OpenAI: 'gpt-4o', 'gpt-4o-mini', etc.
                - Direct Anthropic: 'claude-3-5-sonnet', etc.
                If not provided, uses settings.default_model.
            api_key: Optional API key override (for BYOK).
            temperature: Model temperature. If not provided, uses settings.llm_temperature.
            streaming: Whether to enable streaming.
            provider: Optional provider for routing (e.g., 'Cerebras' for fast inference).
                     If not provided, uses settings.default_model_provider.

        Returns:
            A configured chat model instance.

        Raises:
            ValueError: If the model is not recognized or API key is missing.
        """
        model_name = model_name or self.settings.default_model
        temperature = temperature if temperature is not None else self.settings.llm_temperature

        # OpenRouter models contain "/" (e.g., "openai/gpt-oss-120b")
        if "/" in model_name:
            # Use provided provider or fall back to settings
            effective_provider = (
                provider if provider is not None else self.settings.default_model_provider
            )
            return self._get_openrouter_model(
                model_name, api_key, temperature, streaming, effective_provider
            )
        # Direct OpenAI API
        elif model_name in self.OPENAI_MODELS:
            return self._get_openai_model(model_name, api_key, temperature, streaming)
        # Direct Anthropic API
        elif model_name in self.ANTHROPIC_MODELS:
            return self._get_anthropic_model(model_name, api_key, temperature, streaming)
        else:
            raise ValueError(
                f"Unknown model: {model_name}. "
                f"Use OpenRouter format 'provider/model' or one of: "
                f"{list(self.OPENAI_MODELS.keys()) + list(self.ANTHROPIC_MODELS.keys())}"
            )

    def _get_openai_model(
        self,
        model_name: str,
        api_key: str | None,
        temperature: float,
        streaming: bool,
    ) -> ChatOpenAI:
        """Create an OpenAI chat model."""
        key = api_key or self.settings.openai_api_key
        if not key:
            raise ValueError("OpenAI API key required but not configured")

        return ChatOpenAI(
            model=self.OPENAI_MODELS[model_name],
            api_key=key,
            temperature=temperature,
            streaming=streaming,
        )

    def _get_anthropic_model(
        self,
        model_name: str,
        api_key: str | None,
        temperature: float,
        streaming: bool,
    ) -> ChatAnthropic:
        """Create an Anthropic chat model."""
        key = api_key or self.settings.anthropic_api_key
        if not key:
            raise ValueError("Anthropic API key required but not configured")

        return ChatAnthropic(
            model=self.ANTHROPIC_MODELS[model_name],
            api_key=key,
            temperature=temperature,
            streaming=streaming,
        )

    def _get_openrouter_model(
        self,
        model_name: str,
        api_key: str | None,
        temperature: float,
        streaming: bool,
        provider: str | None = None,
    ) -> ChatOpenAI:
        """Create a model via OpenRouter API.

        OpenRouter provides access to many models through an OpenAI-compatible API.
        Model names are in format 'creator/model' (e.g., 'openai/gpt-oss-120b').
        Provider specifies where the model runs (e.g., 'Cerebras' for fast inference).

        Args:
            model_name: OpenRouter model ID (creator/model format)
            api_key: OpenRouter API key
            temperature: Model temperature
            streaming: Enable streaming responses
            provider: Provider for routing (e.g., 'Cerebras' for fast inference)
        """
        key = api_key or self.settings.openrouter_api_key
        if not key:
            raise ValueError("OpenRouter API key required but not configured")

        # Build extra body with provider preferences if specified
        extra_body: dict | None = None
        if provider:
            extra_body = {"provider": {"order": [provider]}}

        return ChatOpenAI(
            model=model_name,
            api_key=key,
            base_url=OPENROUTER_BASE_URL,
            temperature=temperature,
            streaming=streaming,
            default_headers={
                "HTTP-Referer": "https://osc.earth/osa",
                "X-Title": "Open Science Assistant",
            },
            extra_body=extra_body,
        )

    def get_config_with_tracing(
        self,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Get a config dict with LangFuse tracing callbacks.

        Use this with LangGraph invoke/ainvoke:
            config = llm_service.get_config_with_tracing(trace_id="abc")
            result = graph.invoke(state, config=config)

        Args:
            trace_id: Optional custom trace ID for the root LangChain run.
        """
        config: dict[str, Any] = {}

        handler = self.get_langfuse_handler(trace_id)
        if handler:
            config["callbacks"] = [handler]

        return config


# Singleton instance for convenience
_llm_service: LLMService | None = None


def get_llm_service(settings: Settings | None = None) -> LLMService:
    """Get the LLM service singleton."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService(settings)
    return _llm_service
