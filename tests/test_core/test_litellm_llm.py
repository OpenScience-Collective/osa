"""Tests for LiteLLM integration and caching functionality."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.core.services.litellm_llm import CachingLLMWrapper, create_openrouter_llm


# Test tool for tool binding tests
@tool
def calculator(expression: str) -> str:
    """Calculate a mathematical expression.

    Args:
        expression: A mathematical expression to evaluate

    Returns:
        The result of the calculation
    """
    try:
        return str(eval(expression))
    except Exception as e:
        return f"Error: {str(e)}"


class TestCachingLLMWrapperInitialization:
    """Test CachingLLMWrapper initialization and validation."""

    def test_prevents_double_wrapping(self):
        """Verify double-wrapping is prevented with clear error."""
        from langchain_community.chat_models import FakeListChatModel

        fake_llm = FakeListChatModel(responses=["Test"])
        wrapper = CachingLLMWrapper(llm=fake_llm)

        # Attempt to wrap a wrapper should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            CachingLLMWrapper(llm=wrapper)

        assert "Cannot wrap a CachingLLMWrapper" in str(exc_info.value)
        assert "infinite recursion" in str(exc_info.value)

    def test_validates_llm_has_invoke_method(self):
        """Verify initialization requires invoke method."""

        # Create a mock object without invoke method
        class InvalidLLM:
            pass

        with pytest.raises(TypeError) as exc_info:
            CachingLLMWrapper(llm=InvalidLLM())

        assert "missing required 'invoke' method" in str(exc_info.value)


class TestCachingLLMWrapperMessageTransformation:
    """Test message transformation with cache_control markers."""

    def test_add_cache_control_transforms_system_message(self):
        """Verify system message gets cache_control marker."""
        from langchain_community.chat_models import FakeListChatModel

        fake_llm = FakeListChatModel(responses=["Test response"])
        wrapper = CachingLLMWrapper(llm=fake_llm)

        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="Hello"),
        ]

        result = wrapper._add_cache_control(messages)

        # Check system message structure
        assert result[0]["role"] == "system"
        assert isinstance(result[0]["content"], list)
        assert result[0]["content"][0]["type"] == "text"
        assert result[0]["content"][0]["text"] == "You are a helpful assistant."
        assert result[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

        # Check human message unchanged
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "Hello"

    def test_add_cache_control_preserves_other_messages(self):
        """Verify non-system messages unchanged."""
        from langchain_community.chat_models import FakeListChatModel

        fake_llm = FakeListChatModel(responses=["Test"])
        wrapper = CachingLLMWrapper(llm=fake_llm)

        messages = [
            HumanMessage(content="User query"),
            AIMessage(content="Assistant response"),
        ]

        result = wrapper._add_cache_control(messages)

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "User query"
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Assistant response"
        # No cache_control on non-system messages
        assert "cache_control" not in str(result[0])
        assert "cache_control" not in str(result[1])

    def test_add_cache_control_handles_empty_list(self):
        """Verify empty message list handled gracefully."""
        from langchain_community.chat_models import FakeListChatModel

        fake_llm = FakeListChatModel(responses=["Test"])
        wrapper = CachingLLMWrapper(llm=fake_llm)

        result = wrapper._add_cache_control([])

        assert result == []

    def test_add_cache_control_handles_multiple_system_messages(self):
        """Verify multiple system messages all get cache_control."""
        from langchain_community.chat_models import FakeListChatModel

        fake_llm = FakeListChatModel(responses=["Test"])
        wrapper = CachingLLMWrapper(llm=fake_llm)

        messages = [
            SystemMessage(content="System prompt 1"),
            SystemMessage(content="System prompt 2"),
            HumanMessage(content="Query"),
        ]

        result = wrapper._add_cache_control(messages)

        # Both system messages should have cache_control
        assert result[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert result[1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_add_cache_control_rejects_none_input(self):
        """Verify None input raises ValueError."""
        from langchain_community.chat_models import FakeListChatModel

        fake_llm = FakeListChatModel(responses=["Test"])
        wrapper = CachingLLMWrapper(llm=fake_llm)

        with pytest.raises(ValueError) as exc_info:
            wrapper._add_cache_control(None)

        assert "cannot be None" in str(exc_info.value)

    def test_add_cache_control_rejects_non_list_input(self):
        """Verify non-list input raises TypeError."""
        from langchain_community.chat_models import FakeListChatModel

        fake_llm = FakeListChatModel(responses=["Test"])
        wrapper = CachingLLMWrapper(llm=fake_llm)

        with pytest.raises(TypeError) as exc_info:
            wrapper._add_cache_control("not a list")

        assert "Expected list of messages" in str(exc_info.value)


class TestCachingLLMWrapperToolBinding:
    """Test tool binding preserves caching.

    Note: Tool binding tests use real models in integration tests below,
    since bind_tools() behavior is model-specific and hard to test in isolation.
    """

    def test_bind_tools_rejects_empty_list(self):
        """Verify empty tools list raises ValueError."""
        from langchain_community.chat_models import FakeListChatModel

        fake_llm = FakeListChatModel(responses=["Test"])
        wrapper = CachingLLMWrapper(llm=fake_llm)

        with pytest.raises(ValueError) as exc_info:
            wrapper.bind_tools([])

        assert "empty tools list" in str(exc_info.value)

    def test_bind_tools_checks_for_method_support(self):
        """Verify error when LLM doesn't support bind_tools."""
        from langchain_community.chat_models import FakeListChatModel

        # Check if FakeListChatModel has bind_tools
        fake_llm = FakeListChatModel(responses=["Test"])
        if not hasattr(fake_llm, "bind_tools"):
            # If it doesn't have bind_tools, test the error path
            wrapper = CachingLLMWrapper(llm=fake_llm)

            with pytest.raises(NotImplementedError) as exc_info:
                wrapper.bind_tools([calculator])

            assert "does not support tool binding" in str(exc_info.value)
        else:
            # If it does have bind_tools, skip this test
            pytest.skip("FakeListChatModel has bind_tools, cannot test missing method error")

    @pytest.mark.llm
    def test_bind_tools_returns_caching_wrapper(self):
        """Verify bind_tools() returns CachingLLMWrapper instance with real model."""
        import os

        api_key = os.getenv("OPENROUTER_API_KEY_FOR_TESTING")
        if not api_key:
            pytest.skip("OPENROUTER_API_KEY_FOR_TESTING not set")

        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key=api_key,
            provider="Anthropic",
            enable_caching=True,
        )

        # Verify initial wrapper
        assert isinstance(llm, CachingLLMWrapper)

        # Bind tools
        bound_model = llm.bind_tools([calculator])

        # Verify result is still wrapped
        assert isinstance(bound_model, CachingLLMWrapper)

    @pytest.mark.llm
    def test_nested_bind_tools(self):
        """Verify multiple bind_tools() calls work correctly with real model."""
        import os

        api_key = os.getenv("OPENROUTER_API_KEY_FOR_TESTING")
        if not api_key:
            pytest.skip("OPENROUTER_API_KEY_FOR_TESTING not set")

        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key=api_key,
            provider="Anthropic",
            enable_caching=True,
        )

        # First binding
        bound_once = llm.bind_tools([calculator])
        assert isinstance(bound_once, CachingLLMWrapper)

        # Second binding should also work
        bound_twice = bound_once.bind_tools([calculator])
        assert isinstance(bound_twice, CachingLLMWrapper)


# Invocation and streaming tests are covered by integration tests below
# since they require real model behavior that's hard to test in isolation


class TestCachingLLMWrapperIntegration:
    """Integration tests with real API calls (requires OPENROUTER_API_KEY_FOR_TESTING)."""

    @pytest.mark.llm
    def test_caching_wrapper_with_anthropic_model(self):
        """End-to-end test with real Anthropic model."""
        import os

        api_key = os.getenv("OPENROUTER_API_KEY_FOR_TESTING")
        if not api_key:
            pytest.skip("OPENROUTER_API_KEY_FOR_TESTING not set")

        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key=api_key,
            provider="Anthropic",
            enable_caching=True,
        )

        messages = [
            SystemMessage(content="You are a helpful assistant. Always respond concisely."),
            HumanMessage(content="Say 'Hello' and nothing else."),
        ]

        response = llm.invoke(messages)

        # Verify response received
        assert response is not None
        assert hasattr(response, "content")
        assert "hello" in response.content.lower()

    @pytest.mark.llm
    def test_tool_binding_with_anthropic_model(self):
        """End-to-end test with tools and Anthropic model."""
        import os

        api_key = os.getenv("OPENROUTER_API_KEY_FOR_TESTING")
        if not api_key:
            pytest.skip("OPENROUTER_API_KEY_FOR_TESTING not set")

        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key=api_key,
            provider="Anthropic",
            enable_caching=True,
        )

        bound_model = llm.bind_tools([calculator])

        messages = [
            SystemMessage(content="You are a helpful calculator assistant."),
            HumanMessage(content="What is 25 * 4?"),
        ]

        response = bound_model.invoke(messages)

        # Verify response received (tool may or may not be called depending on model)
        assert response is not None
        assert hasattr(response, "content")

    @pytest.mark.llm
    def test_streaming_with_caching(self):
        """Verify streaming works with caching."""
        import os

        api_key = os.getenv("OPENROUTER_API_KEY_FOR_TESTING")
        if not api_key:
            pytest.skip("OPENROUTER_API_KEY_FOR_TESTING not set")

        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key=api_key,
            provider="Anthropic",
            enable_caching=True,
        )

        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="Count from 1 to 3."),
        ]

        chunks = []
        for chunk in llm.stream(messages):
            chunks.append(chunk)

        # Verify chunks received
        assert len(chunks) > 0

        # Assemble full response
        full_response = "".join(str(chunk.content) for chunk in chunks if hasattr(chunk, "content"))
        assert len(full_response) > 0

    @pytest.mark.llm
    async def test_async_invoke_with_caching(self):
        """Verify async invoke works with caching."""
        import os

        api_key = os.getenv("OPENROUTER_API_KEY_FOR_TESTING")
        if not api_key:
            pytest.skip("OPENROUTER_API_KEY_FOR_TESTING not set")

        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key=api_key,
            provider="Anthropic",
            enable_caching=True,
        )

        messages = [
            SystemMessage(content="You are a helpful assistant. Always respond concisely."),
            HumanMessage(content="Say 'Hello' and nothing else."),
        ]

        response = await llm.ainvoke(messages)

        # Verify response received
        assert response is not None
        assert hasattr(response, "content")
        assert "hello" in response.content.lower()

    @pytest.mark.llm
    async def test_async_streaming_with_caching(self):
        """Verify async streaming works with caching."""
        import os

        api_key = os.getenv("OPENROUTER_API_KEY_FOR_TESTING")
        if not api_key:
            pytest.skip("OPENROUTER_API_KEY_FOR_TESTING not set")

        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key=api_key,
            provider="Anthropic",
            enable_caching=True,
        )

        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="Count from 1 to 3."),
        ]

        chunks = []
        async for chunk in llm.astream(messages):
            chunks.append(chunk)

        # Verify chunks received
        assert len(chunks) > 0

        # Assemble full response
        full_response = "".join(str(chunk.content) for chunk in chunks if hasattr(chunk, "content"))
        assert len(full_response) > 0
