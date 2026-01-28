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
from collections.abc import AsyncIterator, Iterator
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

    Provider Selection:
        - Anthropic models (anthropic/*) automatically use provider="Anthropic"
          for best performance, regardless of the provider parameter
        - Other models use the specified provider or default routing

    Args:
        model: Model identifier (e.g., "openai/gpt-oss-120b", "anthropic/claude-haiku-4.5")
        api_key: OpenRouter API key (defaults to OPENROUTER_API_KEY env var)
        temperature: Sampling temperature (0.0-1.0)
        max_tokens: Maximum tokens to generate
        provider: Specific provider to use (e.g., "Cerebras", "DeepInfra/FP8").
                 Ignored for Anthropic models, which always use "Anthropic" provider.
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

    # Auto-select Anthropic provider for Anthropic models (better performance)
    # Override any default provider if this is an Anthropic model
    if model.startswith("anthropic/"):
        effective_provider = "Anthropic"
        logger.debug("Auto-selected Anthropic provider for model %s (better performance)", model)
    else:
        effective_provider = provider

    # Provider routing (e.g., {"order": ["DeepInfra/FP8"]})
    # Use "order" not "only" - OpenRouter requires exact routing field name
    if effective_provider:
        model_kwargs["provider"] = {"order": [effective_provider]}

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
    (RunnableBinding) to preserve caching through tool binding. When bind_tools()
    is called, it returns a new CachingLLMWrapper around the RunnableBinding,
    creating a chain: CachingLLMWrapper -> RunnableBinding -> BaseChatModel.

    This nested structure ensures cache_control markers are applied to all
    invocations, including tool calls, preventing the 10x cost increase that
    would occur if caching were bypassed.

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

        This method performs a two-step process:
        1. Delegates tool binding to the underlying LLM (returns RunnableBinding)
        2. Wraps the result in a new CachingLLMWrapper to preserve caching

        This ensures cache_control markers are applied to all invocations of the
        tool-bound model, preventing the 10x cost increase that would occur if
        caching were bypassed during tool calls.

        Args:
            tools: List of tools to bind
            **kwargs: Additional arguments for tool binding

        Returns:
            New CachingLLMWrapper instance wrapping the tool-bound RunnableBinding

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

        Applies cache_control markers only to SystemMessage instances. Other message
        types (HumanMessage, AIMessage) are passed through unchanged.

        Validation is strict with fail-fast behavior:
        - Messages must have a 'content' attribute (ValueError if missing)
        - Message content must not be None (ValueError if None)
        - Messages list must be a list, not None (ValueError/TypeError)

        Args:
            messages: List of LangChain messages

        Returns:
            List of message dicts with cache_control on system messages

        Raises:
            ValueError: If messages is None, contains messages without content,
                       or contains messages with None content
            TypeError: If messages is not a list
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
                    logger.error("Message at index %d missing content attribute", i)
                    raise ValueError(
                        f"Invalid message at index {i}: missing 'content' attribute. "
                        f"Message type: {type(msg).__name__}. All messages must have a 'content' attribute."
                    )

                if isinstance(msg, SystemMessage):
                    # Validate content is not None
                    if msg.content is None:
                        logger.error("SystemMessage at index %d has None content", i)
                        raise ValueError(
                            f"SystemMessage at index {i} has None content. "
                            "All system messages must have non-None content."
                        )
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
                    if msg.content is None:
                        logger.error("HumanMessage at index %d has None content", i)
                        raise ValueError(
                            f"HumanMessage at index {i} has None content. "
                            "All messages must have non-None content."
                        )
                    result.append({"role": "user", "content": str(msg.content)})

                elif isinstance(msg, AIMessage):
                    if msg.content is None:
                        logger.error("AIMessage at index %d has None content", i)
                        raise ValueError(
                            f"AIMessage at index {i} has None content. "
                            "All messages must have non-None content."
                        )
                    result.append({"role": "assistant", "content": str(msg.content)})

                else:
                    # Fallback for other message types
                    logger.debug(
                        "Unknown message type %s at index %d, treating as user message",
                        type(msg).__name__,
                        i,
                    )
                    if msg.content is None:
                        logger.error("Message at index %d has None content", i)
                        raise ValueError(
                            f"Message at index {i} has None content. "
                            "All messages must have non-None content."
                        )
                    result.append({"role": "user", "content": str(msg.content)})

            except (ValueError, AttributeError, UnicodeError) as e:
                logger.error(
                    "Error processing message at index %d: %s (%s)",
                    i,
                    str(e),
                    type(e).__name__,
                )
                raise
            except Exception as e:
                logger.error(
                    "Unexpected error processing message at index %d: %s (%s)",
                    i,
                    str(e),
                    type(e).__name__,
                    exc_info=True,
                )
                raise

        logger.debug(
            "Transformed %d messages, added cache_control to %d system messages",
            len(messages),
            sum(1 for msg in messages if isinstance(msg, SystemMessage)),
        )
        return result

    def _generate(self, messages: list[BaseMessage], **kwargs) -> Any:
        """Generate response with cache_control on system messages."""
        logger.debug("Generating response for %d messages", len(messages))
        try:
            cached_messages = self._add_cache_control(messages)
            return self.llm._generate(cached_messages, **kwargs)
        except Exception as e:
            logger.error(
                "Error in _generate for %s: %s",
                type(self.llm).__name__,
                str(e),
                exc_info=True,
            )
            raise

    async def _agenerate(self, messages: list[BaseMessage], **kwargs) -> Any:
        """Async generate response with cache_control on system messages."""
        logger.debug("Async generating response for %d messages", len(messages))
        try:
            cached_messages = self._add_cache_control(messages)
            return await self.llm._agenerate(cached_messages, **kwargs)
        except Exception as e:
            logger.error(
                "Error in _agenerate for %s: %s",
                type(self.llm).__name__,
                str(e),
                exc_info=True,
            )
            raise

    def invoke(self, messages: list[BaseMessage], **kwargs) -> Any:
        """Invoke LLM with cache_control on system messages."""
        logger.debug("Invoking %s with %d messages", type(self.llm).__name__, len(messages))
        try:
            cached_messages = self._add_cache_control(messages)
            return self.llm.invoke(cached_messages, **kwargs)
        except Exception as e:
            logger.error(
                "Error invoking %s: %s",
                type(self.llm).__name__,
                str(e),
                exc_info=True,
            )
            raise

    async def ainvoke(self, messages: list[BaseMessage], **kwargs) -> Any:
        """Async invoke LLM with cache_control on system messages."""
        logger.debug("Async invoking %s with %d messages", type(self.llm).__name__, len(messages))
        try:
            cached_messages = self._add_cache_control(messages)
            return await self.llm.ainvoke(cached_messages, **kwargs)
        except Exception as e:
            logger.error(
                "Error async invoking %s: %s",
                type(self.llm).__name__,
                str(e),
                exc_info=True,
            )
            raise

    def stream(self, input: list[BaseMessage] | Any, config: Any = None, **kwargs) -> Iterator[Any]:
        """Stream with cache_control applied to system messages.

        Applies cache_control transformation only if input is a list of messages.
        Non-list inputs are passed through unchanged to the underlying LLM's stream method.

        Args:
            input: Messages to stream (can be list of BaseMessage or other formats)
            config: Optional runtime configuration
            **kwargs: Additional arguments for streaming

        Yields:
            Stream chunks from the underlying LLM

        Raises:
            ValueError: If input is None or invalid
            NotImplementedError: If underlying LLM doesn't support streaming
            Exception: Any exception raised by the underlying LLM's stream() method
        """
        # Validate input
        if input is None:
            logger.error("Cannot stream with None input")
            raise ValueError("Input cannot be None for streaming")

        # Check if underlying LLM supports streaming
        if not (hasattr(self.llm, "stream") and callable(self.llm.stream)):
            logger.error(
                "Underlying LLM %s does not support streaming",
                type(self.llm).__name__,
            )
            raise NotImplementedError(
                f"Underlying LLM {type(self.llm).__name__} does not support streaming. "
                "To use streaming, either: (1) use a different LLM model that supports streaming, "
                "or (2) use invoke() instead of stream() for non-streaming responses."
            )

        # Apply caching if input is a message list
        if isinstance(input, list):
            logger.debug("Applying cache_control to %d messages for streaming", len(input))
            input = self._add_cache_control(input)
        else:
            logger.warning(
                "Input is not a message list (got %s), caching disabled for this stream. "
                "This may result in higher API costs.",
                type(input).__name__,
            )

        logger.debug("Starting stream from %s", type(self.llm).__name__)
        return self.llm.stream(input, config=config, **kwargs)

    async def astream(
        self, input: list[BaseMessage] | Any, config: Any = None, **kwargs
    ) -> AsyncIterator[Any]:
        """Async stream with cache_control applied to system messages.

        Applies cache_control transformation only if input is a list of messages.
        Non-list inputs are passed through unchanged to the underlying LLM's astream method.

        Args:
            input: Messages to stream (can be list of BaseMessage or other formats)
            config: Optional runtime configuration
            **kwargs: Additional arguments for streaming

        Yields:
            Stream chunks from the underlying LLM

        Raises:
            ValueError: If input is None or invalid
            NotImplementedError: If underlying LLM doesn't support async streaming
            Exception: Any exception raised by the underlying LLM's astream() method
        """
        # Validate input
        if input is None:
            logger.error("Cannot async stream with None input")
            raise ValueError("Input cannot be None for streaming")

        # Check if underlying LLM supports async streaming
        if not (hasattr(self.llm, "astream") and callable(self.llm.astream)):
            logger.error(
                "Underlying LLM %s does not support async streaming",
                type(self.llm).__name__,
            )
            raise NotImplementedError(
                f"Underlying LLM {type(self.llm).__name__} does not support async streaming. "
                "To use streaming, either: (1) use a different LLM model that supports streaming, "
                "or (2) use ainvoke() instead of astream() for non-streaming responses."
            )

        # Apply caching if input is a message list
        if isinstance(input, list):
            logger.debug(
                "Applying cache_control to %d messages for async stream",
                len(input),
            )
            input = self._add_cache_control(input)
        else:
            logger.warning(
                "Input is not a message list (got %s), caching disabled for this stream. "
                "This may result in higher API costs.",
                type(input).__name__,
            )

        logger.debug("Starting async stream from %s", type(self.llm).__name__)
        async for chunk in self.llm.astream(input, config=config, **kwargs):
            yield chunk


# Reference list of known Anthropic Claude models supporting prompt caching
# This is informational only - the is_cacheable_model() function uses a permissive
# heuristic (any "anthropic/claude-*" model) rather than this restrictive list.
# Caching is enabled by default for all models; OpenRouter/LiteLLM handle
# unsupported models gracefully by ignoring cache_control parameters.
CACHEABLE_MODELS = {
    "claude-opus-4.5": "anthropic/claude-opus-4.5",
    "claude-sonnet-4.5": "anthropic/claude-sonnet-4.5",
    "claude-haiku-4.5": "anthropic/claude-haiku-4.5",
}


def is_cacheable_model(model: str) -> bool:
    """Check if a model identifier suggests Anthropic prompt caching support.

    Uses a heuristic check: returns True for model identifiers in the known
    cacheable models list, or any identifier starting with "anthropic/claude-".

    Note: This is optimistic and may return True for models that don't actually
    support caching. The LiteLLM/OpenRouter layer handles unsupported models
    gracefully by ignoring cache_control parameters.

    Args:
        model: Model identifier (e.g., "anthropic/claude-haiku-4.5")

    Returns:
        True if the model likely supports cache_control based on its identifier
    """
    # Check exact match in aliases
    if model in CACHEABLE_MODELS:
        return True
    # Check if it's an Anthropic Claude model (permissive heuristic)
    return model.startswith("anthropic/claude-")
