"""Tests for streaming metrics helpers.

Tests the _extract_token_usage function which extracts token counts
from LangGraph on_chat_model_end events during streaming.
"""

from types import SimpleNamespace

from src.api.routers.community import _extract_token_usage


class TestExtractTokenUsage:
    """Tests for _extract_token_usage."""

    def test_valid_usage_metadata(self):
        """Should extract token counts from a valid AIMessage-like object."""
        ai_msg = SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 50})
        assert _extract_token_usage({"output": ai_msg}) == (100, 50, 0, 0)

    def test_empty_event_data(self):
        """Should return all zeros for empty event data."""
        assert _extract_token_usage({}) == (0, 0, 0, 0)

    def test_no_output_key(self):
        """Should return all zeros when output key is missing."""
        assert _extract_token_usage({"other": "data"}) == (0, 0, 0, 0)

    def test_output_is_none(self):
        """Should return all zeros when output is None."""
        assert _extract_token_usage({"output": None}) == (0, 0, 0, 0)

    def test_no_usage_metadata_attribute(self):
        """Should return all zeros when output object has no usage_metadata."""
        ai_msg = SimpleNamespace(content="hello")
        assert _extract_token_usage({"output": ai_msg}) == (0, 0, 0, 0)

    def test_usage_metadata_is_none(self):
        """Should return all zeros when usage_metadata is None."""
        ai_msg = SimpleNamespace(usage_metadata=None)
        assert _extract_token_usage({"output": ai_msg}) == (0, 0, 0, 0)

    def test_usage_metadata_is_not_dict(self):
        """Should return all zeros when usage_metadata is not a dict."""
        ai_msg = SimpleNamespace(usage_metadata="not a dict")
        assert _extract_token_usage({"output": ai_msg}) == (0, 0, 0, 0)

    def test_usage_metadata_is_empty_dict(self):
        """Should return all zeros when usage_metadata is empty."""
        ai_msg = SimpleNamespace(usage_metadata={})
        assert _extract_token_usage({"output": ai_msg}) == (0, 0, 0, 0)

    def test_missing_token_keys(self):
        """Should default to 0 when token keys are missing."""
        ai_msg = SimpleNamespace(usage_metadata={"total_tokens": 150})
        assert _extract_token_usage({"output": ai_msg}) == (0, 0, 0, 0)

    def test_none_token_values(self):
        """Should treat None token values as 0."""
        ai_msg = SimpleNamespace(usage_metadata={"input_tokens": None, "output_tokens": None})
        assert _extract_token_usage({"output": ai_msg}) == (0, 0, 0, 0)

    def test_zero_tokens(self):
        """Should handle zero token counts."""
        ai_msg = SimpleNamespace(usage_metadata={"input_tokens": 0, "output_tokens": 0})
        assert _extract_token_usage({"output": ai_msg}) == (0, 0, 0, 0)

    def test_partial_token_keys(self):
        """Should handle when only one token key is present."""
        ai_msg = SimpleNamespace(usage_metadata={"input_tokens": 100})
        assert _extract_token_usage({"output": ai_msg}) == (100, 0, 0, 0)

        ai_msg = SimpleNamespace(usage_metadata={"output_tokens": 50})
        assert _extract_token_usage({"output": ai_msg}) == (0, 50, 0, 0)

    def test_never_raises(self):
        """Should never raise, even with bizarre input types."""
        # String as event_data (has .get but returns wrong types)
        assert _extract_token_usage({"output": 42}) == (0, 0, 0, 0)
        assert _extract_token_usage({"output": [1, 2, 3]}) == (0, 0, 0, 0)

    def test_with_langchain_aimessage(self):
        """Should work with real LangChain AIMessage objects."""
        from langchain_core.messages import AIMessage

        msg = AIMessage(
            content="hello",
            usage_metadata={"input_tokens": 200, "output_tokens": 75, "total_tokens": 275},
        )
        assert _extract_token_usage({"output": msg}) == (200, 75, 0, 0)

    def test_with_langchain_aimessage_no_usage(self):
        """Should handle AIMessage without usage_metadata."""
        from langchain_core.messages import AIMessage

        msg = AIMessage(content="hello")
        assert _extract_token_usage({"output": msg}) == (0, 0, 0, 0)

    def test_extracts_cache_read_and_creation_tokens(self):
        """Should extract the cache breakdown from input_token_details."""
        ai_msg = SimpleNamespace(
            usage_metadata={
                "input_tokens": 1000,
                "output_tokens": 50,
                "input_token_details": {"cache_read": 800, "cache_creation": 100},
            }
        )
        assert _extract_token_usage({"output": ai_msg}) == (1000, 50, 800, 100)

    def test_input_token_details_not_dict_defaults_to_zero(self):
        """A malformed input_token_details should not raise; cache counts default to 0."""
        ai_msg = SimpleNamespace(
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 10,
                "input_token_details": "not a dict",
            }
        )
        assert _extract_token_usage({"output": ai_msg}) == (100, 10, 0, 0)

    def test_real_langchain_anthropic_cache_creation_shape(self):
        """Regression (item 1): a real per-TTL breakdown must not price as 0.

        See the matching test in tests/test_metrics/test_db.py for why the
        hand-built ``{"cache_read": N, "cache_creation": M}`` shape used by
        the other tests in this class is not what the real API produces once
        caching is active. This builds usage_metadata through the real
        ``langchain_anthropic._create_usage_metadata`` against a real
        ``anthropic.types.usage.Usage`` and checks the streaming extractor
        reports the real cache-creation count, not 0.
        """
        from anthropic.types.cache_creation import CacheCreation
        from anthropic.types.usage import Usage
        from langchain_anthropic.chat_models import _create_usage_metadata

        anthropic_usage = Usage(
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=500,
            cache_read_input_tokens=70,
            cache_creation=CacheCreation(
                ephemeral_5m_input_tokens=500, ephemeral_1h_input_tokens=0
            ),
        )
        usage_metadata = _create_usage_metadata(anthropic_usage)
        assert usage_metadata["input_token_details"]["cache_creation"] == 0
        assert usage_metadata["input_token_details"]["ephemeral_5m_input_tokens"] == 500

        ai_msg = SimpleNamespace(usage_metadata=usage_metadata)
        assert _extract_token_usage({"output": ai_msg}) == (670, 20, 70, 500)
