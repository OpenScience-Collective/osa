"""LiteLLM integration for OpenRouter with prompt caching support.

This module provides LLM access through LiteLLM, which natively supports
Anthropic's prompt caching via the cache_control parameter. This reduces
costs by up to 90% for repeated prompts with large static content.

Default model: GPT-OSS-120B via Cerebras (fast inference with reliable tool calling)

Usage:
    from src.core.services.litellm_llm import create_openrouter_llm

    # Create LLM with default model via Cerebras
    llm = create_openrouter_llm(
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )

    # Or specify a different model
    llm = create_openrouter_llm(
        model="anthropic/claude-haiku-4.5",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        enable_caching=True,  # For Anthropic prompt caching
    )

    # Use with LangChain messages
    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query),
    ])
"""

import logging
import os
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable

logger = logging.getLogger(__name__)


def create_openrouter_llm(
    model: str = "openai/gpt-oss-120b",
    api_key: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    provider: str | None = "Cerebras",
    user_id: str | None = None,
    enable_caching: bool | None = None,
) -> BaseChatModel:
    """Create an OpenRouter LLM instance with optional prompt caching.

    Uses LiteLLM for native support of Anthropic's prompt caching feature.
    When caching is enabled, system messages are automatically transformed
    to include cache_control markers for 90% cost reduction on cache hits.

    Args:
        model: Model identifier (e.g., "openai/gpt-oss-120b", "anthropic/claude-haiku-4.5")
        api_key: OpenRouter API key (defaults to OPENROUTER_API_KEY env var)
        temperature: Sampling temperature (0.0-1.0)
        max_tokens: Maximum tokens to generate
        provider: Specific provider to use (e.g., "Cerebras", "Anthropic")
        user_id: User identifier for cache optimization (sticky routing)
        enable_caching: Enable prompt caching. If None (default), enabled for all models.
            OpenRouter/LiteLLM gracefully handles models that don't support caching.

    Returns:
        LLM instance configured for OpenRouter
    """
    from langchain_litellm import ChatLiteLLM

    # LiteLLM uses openrouter/ prefix for OpenRouter models
    litellm_model = f"openrouter/{model}"

    # Build model_kwargs for OpenRouter-specific options
    model_kwargs: dict[str, Any] = {
        # OpenRouter app identification headers
        "extra_headers": {
            "HTTP-Referer": "https://osc.earth/osa",
            "X-Title": "Open Science Assistant",
        },
    }

    # Provider routing (e.g., {"order": ["DeepInfra/FP8"]})
    # Use "order" not "only" - OpenRouter requires exact routing field name
    if provider:
        model_kwargs["provider"] = {"order": [provider]}

    # User ID for sticky cache routing
    if user_id:
        model_kwargs["user"] = user_id

    # Create base LLM with streaming enabled for proper event handling
    llm = ChatLiteLLM(
        model=litellm_model,
        api_key=api_key or os.getenv("OPENROUTER_API_KEY"),
        temperature=temperature,
        max_tokens=max_tokens,
        model_kwargs=model_kwargs,
        streaming=True,  # Required for on_chat_model_stream events in LangGraph
    )

    # Determine if caching should be enabled
    if enable_caching is None:
        # Enable caching by default for all models
        # OpenRouter/LiteLLM handles gracefully if model doesn't support it
        enable_caching = True

    if enable_caching:
        return CachingLLMWrapper(llm=llm)

    return llm


class CachingLLMWrapper(BaseChatModel):
    """Wrapper that adds cache_control to system messages for Anthropic caching.

    This wrapper intercepts messages before they're sent to the LLM and
    transforms system messages to use the multipart format with cache_control.

    The cache_control parameter tells Anthropic to cache the content, reducing
    costs by 90% on cache hits (after initial 25% cache write premium).

    Supports wrapping both direct LLMs (BaseChatModel) and tool-bound models
    (RunnableBinding) to preserve caching through tool binding.

    Minimum cacheable prompt: 1024 tokens for Claude Sonnet/Opus, 4096 for Haiku 4.5
    Cache TTL: 5 minutes (refreshed on each hit)
    """

    llm: BaseChatModel | Runnable
    """The underlying LLM or Runnable to wrap."""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, llm: BaseChatModel | Runnable, **kwargs):
        """Initialize the caching wrapper.

        Args:
            llm: The underlying LLM or Runnable to wrap
            **kwargs: Additional arguments for BaseChatModel

        Raises:
            ValueError: If llm is already a CachingLLMWrapper (prevents double-wrapping)
            TypeError: If llm lacks required methods
        """
        # Prevent wrapping a CachingLLMWrapper (infinite recursion risk)
        if isinstance(llm, CachingLLMWrapper):
            raise ValueError(
                "Cannot wrap a CachingLLMWrapper with another CachingLLMWrapper. "
                "This would create infinite recursion. If you need to bind tools, "
                "call bind_tools() on the existing wrapper instead."
            )

        # Validate llm has required methods
        if not hasattr(llm, "invoke"):
            raise TypeError(
                f"Cannot wrap {type(llm).__name__}: missing required 'invoke' method. "
                "The LLM must implement at least the 'invoke' method."
            )

        logger.debug("Initialized CachingLLMWrapper wrapping %s", type(llm).__name__)
        super().__init__(llm=llm, **kwargs)

    @property
    def _llm_type(self) -> str:
        return "caching_llm_wrapper"

    def bind_tools(self, tools: list, **kwargs) -> "CachingLLMWrapper":
        """Bind tools while preserving caching functionality.

        Returns a CachingLLMWrapper that wraps the tool-bound model,
        ensuring cache_control markers are added to all invocations.

        Args:
            tools: List of tools to bind
            **kwargs: Additional arguments for tool binding

        Returns:
            CachingLLMWrapper wrapping the tool-bound model

        Raises:
            ValueError: If tools list is empty
            NotImplementedError: If underlying LLM doesn't support tool binding
            TypeError: If tool binding fails due to type issues
        """
        # Validate tools list
        if not tools:
            logger.error("Cannot bind empty tools list")
            raise ValueError("Cannot bind empty tools list. Provide at least one tool to bind.")

        # Check if underlying LLM supports bind_tools
        if not hasattr(self.llm, "bind_tools"):
            logger.error("Underlying LLM %s does not support bind_tools", type(self.llm).__name__)
            raise NotImplementedError(
                f"Underlying LLM {type(self.llm).__name__} does not support tool binding. "
                "Use a different LLM that implements bind_tools()."
            )

        try:
            # Bind tools to underlying LLM
            logger.debug("Binding %d tools to %s", len(tools), type(self.llm).__name__)
            bound_llm = self.llm.bind_tools(tools, **kwargs)

            # Wrap in CachingLLMWrapper to preserve caching
            wrapped_llm = CachingLLMWrapper(llm=bound_llm)
            logger.debug("Successfully bound tools and wrapped in CachingLLMWrapper")
            return wrapped_llm

        except NotImplementedError as e:
            logger.error(
                "Tool binding not implemented for %s: %s",
                type(self.llm).__name__,
                str(e),
            )
            raise NotImplementedError(
                f"Tool binding failed: {type(self.llm).__name__} does not implement bind_tools(). "
                f"Original error: {str(e)}"
            ) from e
        except TypeError as e:
            logger.error(
                "Type error during tool binding for %s: %s",
                type(self.llm).__name__,
                str(e),
            )
            raise TypeError(
                f"Tool binding failed due to type mismatch: {str(e)}. "
                "Check that tools are properly formatted LangChain tool objects."
            ) from e
        except ValueError as e:
            logger.error(
                "Value error during tool binding for %s: %s",
                type(self.llm).__name__,
                str(e),
            )
            raise

    def _add_cache_control(self, messages: list[BaseMessage]) -> list[dict]:
        """Transform messages to add cache_control to system messages.

        Args:
            messages: List of LangChain messages

        Returns:
            List of message dicts with cache_control on system messages

        Raises:
            ValueError: If messages list is None or contains invalid messages
            TypeError: If message content is not string-compatible
        """
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        # Validate input
        if messages is None:
            logger.error("Cannot transform None messages list")
            raise ValueError("Messages list cannot be None")

        if not isinstance(messages, list):
            logger.error("Expected list of messages, got %s", type(messages).__name__)
            raise TypeError(f"Expected list of messages, got {type(messages).__name__}")

        result = []
        for i, msg in enumerate(messages):
            try:
                # Validate message has content attribute
                if not hasattr(msg, "content"):
                    logger.warning("Message at index %d missing content attribute, skipping", i)
                    continue

                if isinstance(msg, SystemMessage):
                    # Validate content is not None
                    if msg.content is None:
                        logger.warning(
                            "SystemMessage at index %d has None content, using empty string",
                            i,
                        )
                        content = ""
                    else:
                        content = str(msg.content)

                    # Transform system message to multipart format with cache_control
                    result.append(
                        {
                            "role": "system",
                            "content": [
                                {
                                    "type": "text",
                                    "text": content,
                                    "cache_control": {"type": "ephemeral"},
                                }
                            ],
                        }
                    )
                    logger.debug("Added cache_control to SystemMessage at index %d", i)

                elif isinstance(msg, HumanMessage):
                    content = str(msg.content) if msg.content is not None else ""
                    result.append({"role": "user", "content": content})

                elif isinstance(msg, AIMessage):
                    content = str(msg.content) if msg.content is not None else ""
                    result.append({"role": "assistant", "content": content})

                else:
                    # Fallback for other message types
                    logger.debug(
                        "Unknown message type %s at index %d, treating as user message",
                        type(msg).__name__,
                        i,
                    )
                    content = str(msg.content) if msg.content is not None else ""
                    result.append({"role": "user", "content": content})

            except Exception as e:
                logger.error("Error processing message at index %d: %s", i, str(e))
                raise TypeError(f"Failed to process message at index {i}: {str(e)}") from e

        logger.debug(
            "Transformed %d messages, added cache_control to %d system messages",
            len(messages),
            sum(1 for msg in messages if isinstance(msg, SystemMessage)),
        )
        return result

    def _generate(self, messages: list[BaseMessage], **kwargs) -> Any:
        """Generate response with cache_control on system messages."""
        cached_messages = self._add_cache_control(messages)
        return self.llm._generate(cached_messages, **kwargs)

    async def _agenerate(self, messages: list[BaseMessage], **kwargs) -> Any:
        """Async generate response with cache_control on system messages."""
        cached_messages = self._add_cache_control(messages)
        return await self.llm._agenerate(cached_messages, **kwargs)

    def invoke(self, messages: list[BaseMessage], **kwargs) -> Any:
        """Invoke LLM with cache_control on system messages."""
        cached_messages = self._add_cache_control(messages)
        return self.llm.invoke(cached_messages, **kwargs)

    async def ainvoke(self, messages: list[BaseMessage], **kwargs) -> Any:
        """Async invoke LLM with cache_control on system messages."""
        cached_messages = self._add_cache_control(messages)
        return await self.llm.ainvoke(cached_messages, **kwargs)

    def stream(self, input, config=None, **kwargs):
        """Stream with cache_control applied to system messages.

        Args:
            input: Messages to stream (can be list of BaseMessage or other formats)
            config: Optional runtime configuration
            **kwargs: Additional arguments for streaming

        Yields:
            Stream chunks from the underlying LLM

        Raises:
            ValueError: If input is None or invalid
            NotImplementedError: If underlying LLM doesn't support streaming
        """
        # Validate input
        if input is None:
            logger.error("Cannot stream with None input")
            raise ValueError("Input cannot be None for streaming")

        # Check if underlying LLM supports streaming
        if not hasattr(self.llm, "stream"):
            logger.error(
                "Underlying LLM %s does not support streaming",
                type(self.llm).__name__,
            )
            raise NotImplementedError(
                f"Underlying LLM {type(self.llm).__name__} does not support streaming"
            )

        try:
            # Apply caching if input is a message list
            if isinstance(input, list):
                logger.debug("Applying cache_control to %d messages", len(input))
                input = self._add_cache_control(input)
            else:
                logger.debug(
                    "Input is not a list (%s), passing through without caching",
                    type(input).__name__,
                )

            logger.debug("Starting stream from %s", type(self.llm).__name__)
            return self.llm.stream(input, config=config, **kwargs)

        except Exception as e:
            logger.error("Error during streaming: %s", str(e))
            raise

    async def astream(self, input, config=None, **kwargs):
        """Async stream with cache_control applied to system messages.

        Args:
            input: Messages to stream (can be list of BaseMessage or other formats)
            config: Optional runtime configuration
            **kwargs: Additional arguments for streaming

        Yields:
            Stream chunks from the underlying LLM

        Raises:
            ValueError: If input is None or invalid
            NotImplementedError: If underlying LLM doesn't support async streaming
        """
        # Validate input
        if input is None:
            logger.error("Cannot async stream with None input")
            raise ValueError("Input cannot be None for async streaming")

        # Check if underlying LLM supports async streaming
        if not hasattr(self.llm, "astream"):
            logger.error(
                "Underlying LLM %s does not support async streaming",
                type(self.llm).__name__,
            )
            raise NotImplementedError(
                f"Underlying LLM {type(self.llm).__name__} does not support async streaming"
            )

        try:
            # Apply caching if input is a message list
            if isinstance(input, list):
                logger.debug(
                    "Applying cache_control to %d messages for async stream",
                    len(input),
                )
                input = self._add_cache_control(input)
            else:
                logger.debug(
                    "Input is not a list (%s), passing through without caching",
                    type(input).__name__,
                )

            logger.debug("Starting async stream from %s", type(self.llm).__name__)
            async for chunk in self.llm.astream(input, config=config, **kwargs):
                yield chunk

        except Exception as e:
            logger.error("Error during async streaming: %s", str(e))
            raise


# Current Anthropic models (for reference)
# Note: Caching is enabled for ALL models by default; OpenRouter handles gracefully
CACHEABLE_MODELS = {
    "claude-opus-4.5": "anthropic/claude-opus-4.5",
    "claude-sonnet-4.5": "anthropic/claude-sonnet-4.5",
    "claude-haiku-4.5": "anthropic/claude-haiku-4.5",
}


def is_cacheable_model(model: str) -> bool:
    """Check if a model supports Anthropic prompt caching.

    Args:
        model: Model identifier

    Returns:
        True if the model supports cache_control
    """
    # Check exact match in aliases
    if model in CACHEABLE_MODELS:
        return True
    # Check if it's an Anthropic Claude model
    return model.startswith("anthropic/claude-")
