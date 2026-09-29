"""Tests for the OpenRouter LLM factory and the offered-model slug mapping.

The chat model itself (cache breakpoints, tagged citations, usage details) is tested
in ``test_litellm_chat.py``, at the HTTP boundary. This file covers what
``create_openrouter_llm`` configures, the live-API smoke tests that need
``OPENROUTER_API_KEY_FOR_TESTING`` (marked ``llm``, skipped without it), and the
first-party id to OpenRouter slug mapping.
"""

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_litellm import ChatLiteLLM

from src.core.services.anthropic_llm import MODEL_ALIASES, OFFERED_MODELS, normalize_model
from src.core.services.litellm_chat import TaggedCitationChatLiteLLM
from src.core.services.litellm_llm import (
    OPENROUTER_MODEL_IDS,
    create_openrouter_llm,
    to_openrouter_model,
)
from src.metrics.cost import MODEL_PRICING

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
        assert llm.model_kwargs["provider"] == {"order": ["Anthropic"]}

    def test_anthropic_model_overrides_default_provider(self) -> None:
        """Anthropic models should override any specified provider."""
        llm = create_openrouter_llm(
            model="anthropic/claude-sonnet-4.5",
            api_key="test-key",
            provider="DeepInfra/FP8",  # Should be ignored for Anthropic models
        )
        # Should use Anthropic provider, not the specified one
        assert llm.model_kwargs["provider"] == {"order": ["Anthropic"]}

    def test_anthropic_model_with_different_version(self) -> None:
        """All Anthropic model versions should auto-select Anthropic provider."""
        llm = create_openrouter_llm(
            model="anthropic/claude-opus-4",
            api_key="test-key",
            provider="SomeOtherProvider",
        )
        assert llm.model_kwargs["provider"] == {"order": ["Anthropic"]}

    def test_non_anthropic_model_uses_specified_provider(self) -> None:
        """Non-Anthropic models should use the specified provider."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            provider="Cerebras",
        )
        assert llm.model_kwargs["provider"] == {"order": ["Cerebras"]}

    def test_non_anthropic_model_with_deepinfra_provider(self) -> None:
        """Non-Anthropic models should use DeepInfra provider when specified."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            provider="DeepInfra/FP8",
        )
        assert llm.model_kwargs["provider"] == {"order": ["DeepInfra/FP8"]}

    def test_non_anthropic_model_without_provider(self) -> None:
        """Non-Anthropic models with no provider should have no provider key."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            provider=None,
        )
        assert "provider" not in llm.model_kwargs

    def test_default_model_with_default_provider(self) -> None:
        """Default model with default provider should use the specified provider."""
        llm = create_openrouter_llm(
            api_key="test-key",
            # Uses default model="openai/gpt-oss-120b" and provider="Cerebras"
        )
        assert llm.model_kwargs["provider"] == {"order": ["Cerebras"]}


class TestCreateOpenRouterLLMConfiguration:
    """Tests for general LLM configuration options."""

    def test_model_prefix(self) -> None:
        """LLM should use openrouter/ prefix for LiteLLM."""
        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
        )
        # LiteLLM should receive the model with openrouter/ prefix
        assert llm.model.startswith("openrouter/")

    def test_temperature_configuration(self) -> None:
        """LLM should respect temperature parameter."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            temperature=0.5,
        )
        assert llm.temperature == 0.5

    def test_max_tokens_configuration(self) -> None:
        """LLM should respect max_tokens parameter."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
            max_tokens=1000,
        )
        assert llm.max_tokens == 1000

    def test_user_id_for_sticky_routing(self) -> None:
        """LLM should include user ID for cache optimization."""
        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5",
            api_key="test-key",
            user_id="test-user-123",
        )
        assert llm.model_kwargs["user"] == "test-user-123"

    def test_extra_headers_for_openrouter(self) -> None:
        """LLM should include required OpenRouter headers."""
        llm = create_openrouter_llm(
            model="openai/gpt-oss-120b",
            api_key="test-key",
        )
        headers = llm.model_kwargs["extra_headers"]
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
        assert llm.streaming is True


class TestCreateOpenRouterLLMKeyResolution:
    """Tests that a missing API key fails loud instead of constructing an
    LLM with no key that would only surface an opaque auth error later."""

    def test_no_api_key_and_no_env_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="No OpenRouter API key available"):
            create_openrouter_llm(model="openai/gpt-oss-120b", api_key=None)

    def test_env_var_used_when_api_key_not_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
        llm = create_openrouter_llm(model="openai/gpt-oss-120b", api_key=None)
        assert llm.api_key == "env-key"

    def test_explicit_api_key_takes_precedence_over_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
        llm = create_openrouter_llm(model="openai/gpt-oss-120b", api_key="explicit-key")
        assert llm.api_key == "explicit-key"


class TestCreateOpenRouterLLMPromptCaching:
    """The factory returns the tagged-citation chat model, with caching on or off."""

    def test_returns_the_tagged_citation_chat_model(self) -> None:
        llm = create_openrouter_llm(model="anthropic/claude-haiku-4.5", api_key="test-key")
        assert isinstance(llm, TaggedCitationChatLiteLLM)
        assert isinstance(llm, ChatLiteLLM)

    def test_caching_enabled_by_default(self) -> None:
        llm = create_openrouter_llm(model="anthropic/claude-haiku-4.5", api_key="test-key")
        assert llm.prompt_caching is True

    def test_caching_can_be_disabled(self) -> None:
        llm = create_openrouter_llm(
            model="anthropic/claude-haiku-4.5", api_key="test-key", enable_caching=False
        )
        assert llm.prompt_caching is False

    def test_bind_tools_keeps_the_model(self) -> None:
        """The bound model runs this class's methods; nothing has to be re-wrapped."""
        llm = create_openrouter_llm(model="openai/gpt-oss-120b", api_key="test-key")
        bound = llm.bind_tools([calculator])
        assert bound.bound is llm


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


class TestOpenRouterLive:
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


# ============================================================================
# OpenRouter slugs for the offered models
# ============================================================================


class TestOpenRouterModelIds:
    """Tests for the first-party to OpenRouter slug mapping.

    A request funded by an OpenRouter key should run the same model the
    community chose, not a different model family, so every offered model
    needs a slug here.
    """

    def test_every_offered_model_has_an_openrouter_slug(self) -> None:
        """Adding an offered model must not silently skip the mapping."""
        assert set(OPENROUTER_MODEL_IDS) == set(OFFERED_MODELS)

    def test_every_offered_model_is_priced(self) -> None:
        """Every offered first-party id must be in MODEL_PRICING.

        Drift test for item 3: a community whose default resolves to an
        offered model that is missing from MODEL_PRICING gets blocked
        outright by _check_model_cost with a 403, on both provider paths.
        """
        missing = set(OFFERED_MODELS) - set(MODEL_PRICING)
        assert not missing, f"Offered models missing from MODEL_PRICING: {missing}"

    def test_every_openrouter_slug_is_priced(self) -> None:
        """Every OpenRouter slug an offered model maps to must be priced.

        This is what stops item 3 (anthropic/claude-sonnet-5 missing from
        MODEL_PRICING) from recurring when a third model is added: the slug
        a bare id maps to over OpenRouter needs its own pricing entry, since
        MODEL_PRICING is keyed by the id actually sent to _check_model_cost,
        not by the first-party id.
        """
        missing = set(OPENROUTER_MODEL_IDS.values()) - set(MODEL_PRICING)
        assert not missing, f"OpenRouter slugs missing from MODEL_PRICING: {missing}"

    def test_slugs_are_openrouter_shaped(self) -> None:
        """Shape check only: a wrong-but-well-formed slug is not caught here.

        No test in this suite makes a real OpenRouter call, so a slug that
        looks like "creator/model" but names a model OpenRouter does not
        actually serve would pass this test and only surface later as a
        live 400 from OpenRouter.
        """
        for first_party, slug in OPENROUTER_MODEL_IDS.items():
            assert "/" in slug, f"{first_party} maps to {slug!r}, not a creator/model slug"

    def test_maps_offered_first_party_ids(self) -> None:
        for first_party, slug in OPENROUTER_MODEL_IDS.items():
            assert to_openrouter_model(first_party) == slug

    def test_passes_through_existing_openrouter_slugs(self) -> None:
        assert to_openrouter_model("qwen/qwen3-235b-a22b-2507") == "qwen/qwen3-235b-a22b-2507"

    def test_returns_none_for_unmappable_bare_id(self) -> None:
        assert to_openrouter_model("some-unknown-model") is None

    def test_returns_none_for_no_model(self) -> None:
        assert to_openrouter_model(None) is None

    def test_every_legacy_alias_resolves_through_canonicalize_then_map(self) -> None:
        """Every bare alias in MODEL_ALIASES must map to a valid OpenRouter slug.

        Regression for item 2: to_openrouter_model() alone only knows the
        two canonical ids, so a bare legacy alias (e.g. "claude-sonnet-4.5")
        needs normalize_model() first. This is the canonicalize-then-map
        path _select_model now uses (see
        _to_openrouter_model_via_canonical in src/api/routers/community.py).
        """
        for alias in MODEL_ALIASES:
            canonical = normalize_model(alias)
            slug = to_openrouter_model(canonical)
            assert slug is not None, f"alias {alias!r} (-> {canonical!r}) did not map"
            assert "/" in slug, f"alias {alias!r} mapped to non-slug {slug!r}"
