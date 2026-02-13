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
        assert _extract_token_usage({"output": ai_msg}) == (100, 50)

    def test_empty_event_data(self):
        """Should return (0, 0) for empty event data."""
        assert _extract_token_usage({}) == (0, 0)

    def test_no_output_key(self):
        """Should return (0, 0) when output key is missing."""
        assert _extract_token_usage({"other": "data"}) == (0, 0)

    def test_output_is_none(self):
        """Should return (0, 0) when output is None."""
        assert _extract_token_usage({"output": None}) == (0, 0)

    def test_no_usage_metadata_attribute(self):
        """Should return (0, 0) when output object has no usage_metadata."""
        ai_msg = SimpleNamespace(content="hello")
        assert _extract_token_usage({"output": ai_msg}) == (0, 0)

    def test_usage_metadata_is_none(self):
        """Should return (0, 0) when usage_metadata is None."""
        ai_msg = SimpleNamespace(usage_metadata=None)
        assert _extract_token_usage({"output": ai_msg}) == (0, 0)

    def test_usage_metadata_is_not_dict(self):
        """Should return (0, 0) when usage_metadata is not a dict."""
        ai_msg = SimpleNamespace(usage_metadata="not a dict")
        assert _extract_token_usage({"output": ai_msg}) == (0, 0)

    def test_usage_metadata_is_empty_dict(self):
        """Should return (0, 0) when usage_metadata is empty."""
        ai_msg = SimpleNamespace(usage_metadata={})
        assert _extract_token_usage({"output": ai_msg}) == (0, 0)

    def test_missing_token_keys(self):
        """Should default to 0 when token keys are missing."""
        ai_msg = SimpleNamespace(usage_metadata={"total_tokens": 150})
        assert _extract_token_usage({"output": ai_msg}) == (0, 0)

    def test_none_token_values(self):
        """Should treat None token values as 0."""
        ai_msg = SimpleNamespace(usage_metadata={"input_tokens": None, "output_tokens": None})
        assert _extract_token_usage({"output": ai_msg}) == (0, 0)

    def test_zero_tokens(self):
        """Should handle zero token counts."""
        ai_msg = SimpleNamespace(usage_metadata={"input_tokens": 0, "output_tokens": 0})
        assert _extract_token_usage({"output": ai_msg}) == (0, 0)

    def test_partial_token_keys(self):
        """Should handle when only one token key is present."""
        ai_msg = SimpleNamespace(usage_metadata={"input_tokens": 100})
        assert _extract_token_usage({"output": ai_msg}) == (100, 0)

        ai_msg = SimpleNamespace(usage_metadata={"output_tokens": 50})
        assert _extract_token_usage({"output": ai_msg}) == (0, 50)

    def test_never_raises(self):
        """Should never raise, even with bizarre input types."""
        # String as event_data (has .get but returns wrong types)
        assert _extract_token_usage({"output": 42}) == (0, 0)
        assert _extract_token_usage({"output": [1, 2, 3]}) == (0, 0)

    def test_with_langchain_aimessage(self):
        """Should work with real LangChain AIMessage objects."""
        from langchain_core.messages import AIMessage

        msg = AIMessage(
            content="hello",
            usage_metadata={"input_tokens": 200, "output_tokens": 75, "total_tokens": 275},
        )
        assert _extract_token_usage({"output": msg}) == (200, 75)

    def test_with_langchain_aimessage_no_usage(self):
        """Should handle AIMessage without usage_metadata."""
        from langchain_core.messages import AIMessage

        msg = AIMessage(content="hello")
        assert _extract_token_usage({"output": msg}) == (0, 0)
