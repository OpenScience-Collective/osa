"""Tests for LiteLLM integration and caching functionality.

Testing Approach:
-----------------
This test suite uses a two-tier testing strategy to balance the NO MOCKS policy
with practical test requirements:

1. **Unit Tests (FakeListChatModel)**: Test CachingLLMWrapper's internal mechanics
   - Message transformation logic (_add_cache_control)
   - Input validation and error handling
   - Wrapper initialization and double-wrapping prevention

   These tests use FakeListChatModel (a LangChain test utility) because they're
   testing the WRAPPER's logic, not LLM behavior. The wrapper mechanics are
   deterministic and don't require real API calls.

2. **Integration Tests (Real API)**: Test actual LLM behavior with caching
   - Real Anthropic API calls with @pytest.mark.llm marker
   - Tool binding with actual models
   - Streaming responses with cache_control

   These tests verify the wrapper works correctly with real LLMs and that
   cache_control parameters are properly transmitted.

This separation allows fast unit tests for wrapper logic while ensuring real
LLM integration is thoroughly tested. The FakeListChatModel is NOT used to
test LLM responses or behavior - only wrapper mechanics.

Additionally, this file includes tests for OpenRouter LLM creation, particularly
the provider auto-selection behavior for Anthropic models.
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_litellm import ChatLiteLLM

from src.core.services.litellm_llm import CachingLLMWrapper, create_openrouter_llm

# ============================================================================
# Provider Selection Tests
# ============================================================================


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
        assert isinstance(llm, CachingLLMWrapper)

    def test_caching_enabled_by_default(self) -> None:
        """Caching should be enabled by default."""
        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
        )
        # Should be wrapped by default
        assert isinstance(llm, CachingLLMWrapper)

    def test_caching_can_be_disabled(self) -> None:
        """Caching should be disableable via parameter."""
        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
            enable_caching=False,
        )
        # Should NOT be wrapped when disabled
        assert not isinstance(llm, CachingLLMWrapper)
        assert isinstance(llm, ChatLiteLLM)


# Test tool for tool binding tests
@tool
def calculator(expression: str) -> str:
    """Calculate a mathematical expression.

    Args:
        expression: A mathematical expression to evaluate (basic arithmetic only)

    Returns:
        The result of the calculation
    """
    import ast
    import operator

    # Safe operators mapping
    safe_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    def safe_eval(node):
        """Safely evaluate an AST node with only basic arithmetic."""
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.BinOp):
            left = safe_eval(node.left)
            right = safe_eval(node.right)
            op = safe_operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = safe_eval(node.operand)
            op = safe_operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(operand)
        else:
            raise ValueError(f"Unsupported expression: {type(node).__name__}")

    try:
        # Parse the expression into an AST
        tree = ast.parse(expression, mode="eval")
        # Evaluate using only safe operations
        result = safe_eval(tree.body)
        return str(result)
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


class TestCachingToolMessages:
    """Test that tool call messages are correctly serialized by _add_cache_control."""

    def _make_wrapper(self):
        from langchain_community.chat_models import FakeListChatModel

        return CachingLLMWrapper(llm=FakeListChatModel(responses=["Test"]))

    def test_ai_message_with_tool_calls_preserved(self):
        """AIMessage with tool_calls should serialize with role=assistant and tool_calls array."""
        wrapper = self._make_wrapper()

        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_123", "name": "search_hed_docs", "args": {"query": "BCI"}},
            ],
        )
        result = wrapper._add_cache_control([ai_msg])

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert "tool_calls" in result[0]
        assert len(result[0]["tool_calls"]) == 1

        tc = result[0]["tool_calls"][0]
        assert tc["id"] == "call_123"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "search_hed_docs"
        assert json.loads(tc["function"]["arguments"]) == {"query": "BCI"}

    def test_tool_message_serialized_correctly(self):
        """ToolMessage should serialize with role=tool and tool_call_id."""
        wrapper = self._make_wrapper()

        tool_msg = ToolMessage(
            content="Found 3 documents about BCI.",
            tool_call_id="call_123",
        )
        result = wrapper._add_cache_control([tool_msg])

        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_123"
        assert result[0]["content"] == "Found 3 documents about BCI."

    def test_tool_message_empty_content(self):
        """ToolMessage with empty content should serialize without error."""
        wrapper = self._make_wrapper()

        tool_msg = ToolMessage(content="", tool_call_id="call_123")
        result = wrapper._add_cache_control([tool_msg])

        assert result[0]["role"] == "tool"
        assert result[0]["content"] == ""
        assert result[0]["tool_call_id"] == "call_123"

    def test_ai_message_without_tool_calls_unchanged(self):
        """AIMessage without tool_calls should serialize normally (no tool_calls key)."""
        wrapper = self._make_wrapper()

        ai_msg = AIMessage(content="Here is the answer.")
        result = wrapper._add_cache_control([ai_msg])

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Here is the answer."
        assert "tool_calls" not in result[0]

    def test_full_tool_call_sequence(self):
        """Complete [System, Human, AI(tool_calls), Tool, AI] sequence serializes correctly."""
        wrapper = self._make_wrapper()

        messages = [
            SystemMessage(content="You are a HED expert."),
            HumanMessage(content="What is BCI in HED?"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_abc", "name": "search_hed_docs", "args": {"query": "BCI"}},
                ],
            ),
            ToolMessage(
                content="BCI stands for Brain-Computer Interface.", tool_call_id="call_abc"
            ),
            AIMessage(content="BCI stands for Brain-Computer Interface in HED."),
        ]

        result = wrapper._add_cache_control(messages)

        assert len(result) == 5
        assert result[0]["role"] == "system"
        assert result[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert "tool_calls" in result[2]
        assert result[3]["role"] == "tool"
        assert result[3]["tool_call_id"] == "call_abc"
        assert result[4]["role"] == "assistant"
        assert "tool_calls" not in result[4]

    def test_ai_message_with_multiple_tool_calls(self):
        """Multiple tool calls on a single AIMessage should all be serialized."""
        wrapper = self._make_wrapper()

        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_1", "name": "search_docs", "args": {"q": "BCI"}},
                {"id": "call_2", "name": "fetch_page", "args": {"url": "https://example.com"}},
            ],
        )
        result = wrapper._add_cache_control([ai_msg])

        assert len(result[0]["tool_calls"]) == 2
        assert result[0]["tool_calls"][0]["function"]["name"] == "search_docs"
        assert result[0]["tool_calls"][1]["function"]["name"] == "fetch_page"
