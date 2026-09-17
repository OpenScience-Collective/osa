"""LangFuse observability helper.

This module used to be a third LLM construction path, with its own direct
OpenAI, direct Anthropic, and OpenRouter model factories. Phase 2 of the
Claude Platform on AWS migration (issue #362) retired all three: platform
traffic is built by ``src.core.services.anthropic_llm`` and bring-your-own-key
OpenRouter traffic by ``src.core.services.litellm_llm``. Nothing called this
module's ``get_model``, and its model tables had already drifted out of step
with the offered models, so a request that reached them would have failed on
an unknown-model error rather than fallen back.

What remains is the only part production ever used: LangFuse tracing config,
wired into requests by ``src.api.routers.community.create_community_assistant``.
"""

import os
from typing import Any

from src.api.config import Settings, get_settings


class LLMService:
    """LangFuse tracing configuration for agent invocations."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the service.

        Args:
            settings: Optional settings instance. Defaults to ``get_settings()``.
        """
        self.settings = settings or get_settings()

    def get_langfuse_handler(
        self,
        trace_id: str | None = None,
    ) -> Any | None:
        """Create a LangFuse callback handler for tracing.

        LangFuse uses environment variables for authentication:
        - LANGFUSE_PUBLIC_KEY
        - LANGFUSE_SECRET_KEY
        - LANGFUSE_HOST

        This method sets these from settings before creating the handler.

        Note: Langfuse import is lazy to avoid Python 3.14 compatibility issues
        with Pydantic v1 (used by langfuse internally).

        Args:
            trace_id: Optional custom trace ID for the root LangChain run.

        Returns:
            CallbackHandler instance if langfuse is configured and importable,
            None otherwise.
        """
        if not self.settings.langfuse_public_key or not self.settings.langfuse_secret_key:
            return None

        # Lazy import to avoid Pydantic v1 compatibility issues on Python 3.14
        # See: https://github.com/OpenScience-Collective/osa/issues/108
        try:
            from langfuse.langchain import CallbackHandler as LangfuseHandler
        except (ImportError, Exception) as e:
            import warnings

            warnings.warn(
                f"Langfuse import failed: {e}. Observability disabled. "
                "Install with: uv pip install 'open-science-assistant[observability]'",
                stacklevel=2,
            )
            return None

        # Set environment variables for LangFuse client
        os.environ["LANGFUSE_PUBLIC_KEY"] = self.settings.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = self.settings.langfuse_secret_key
        os.environ["LANGFUSE_HOST"] = self.settings.langfuse_host

        # Create handler with optional trace context
        if trace_id:
            return LangfuseHandler(trace_context={"trace_id": trace_id})
        return LangfuseHandler()

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
