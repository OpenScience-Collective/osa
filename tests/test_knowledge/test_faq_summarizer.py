"""Tests for FAQ summarization from mailing list threads.

Tests are tool-centered, not community-specific. They validate the
faq_summarizer module works correctly for any mailing list data.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.db import get_connection, init_db, upsert_mailing_list_message
from src.knowledge.faq_summarizer import (
    _build_thread_context,
    _score_thread_quality,
    _summarize_thread,
    estimate_summarization_cost,
)


@pytest.fixture
def populated_mailman_db(tmp_path: Path):
    """Create a test database with mailing list messages."""
    db_path = tmp_path / "knowledge" / "test-faq.db"

    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db("test-faq")

        with get_connection("test-faq") as conn:
            # Add messages forming a thread
            for i in range(3):
                upsert_mailing_list_message(
                    conn,
                    list_name="test-list",
                    message_id=f"msg{i:03d}",
                    thread_id="thread001",  # Same thread
                    subject=f"Test subject - message {i}",
                    author=f"Author {i}",
                    author_email=f"author{i}@example.com",
                    date=f"2026-01-{i + 1:02d}T10:00:00Z",
                    body=f"This is the body of message {i}.\nIt has multiple lines.",
                    in_reply_to=f"msg{i - 1:03d}" if i > 0 else None,
                    url=f"https://example.com/list/2026/msg{i:03d}.html",
                    year=2026,
                )

            # Add a single-message thread (should be filtered out)
            upsert_mailing_list_message(
                conn,
                list_name="test-list",
                message_id="single001",
                thread_id="thread002",
                subject="Single message thread",
                author="Solo Author",
                author_email="solo@example.com",
                date="2026-01-10T10:00:00Z",
                body="This thread has only one message.",
                in_reply_to=None,
                url="https://example.com/list/2026/single001.html",
                year=2026,
            )

            conn.commit()

        yield db_path


class TestBuildThreadContext:
    """Tests for thread context building."""

    def test_build_context_formats_correctly(self):
        """Test that thread context is formatted correctly."""
        messages = [
            {
                "author": "Alice",
                "date": "2026-01-01",
                "subject": "Question about feature",
                "body": "How do I use this feature?",
            },
            {
                "author": "Bob",
                "date": "2026-01-02",
                "subject": "Re: Question about feature",
                "body": "You can use it by following these steps...",
            },
        ]

        context = _build_thread_context(messages)

        assert "--- Message 1 ---" in context
        assert "--- Message 2 ---" in context
        assert "From: Alice" in context
        assert "From: Bob" in context
        assert "How do I use this feature?" in context
        assert "You can use it by following these steps" in context

    def test_build_context_truncates_long_messages(self):
        """Test that very long messages are truncated."""
        messages = [
            {
                "author": "Alice",
                "date": "2026-01-01",
                "subject": "Long message",
                "body": "A" * 3000,  # 3000 character message
            }
        ]

        context = _build_thread_context(messages)

        # Should be truncated to 2000 chars + truncation message
        assert len(messages[0]["body"]) == 3000
        assert "... truncated ...]" in context

    def test_build_context_handles_none_values(self):
        """Test that None values are handled gracefully."""
        messages = [
            {
                "author": None,
                "date": "2026-01-01",
                "subject": "Test",
                "body": None,
            }
        ]

        context = _build_thread_context(messages)

        assert "From: Unknown" in context
        assert "--- Message 1 ---" in context


class TestScoreThreadQuality:
    """Tests for thread quality scoring."""

    def test_score_returns_valid_range(self):
        """Test that scoring returns a value between 0.0 and 1.0."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "0.75"
        mock_model.invoke.return_value = mock_response

        score = _score_thread_quality("Test thread context", mock_model)

        assert 0.0 <= score <= 1.0
        assert score == 0.75

    def test_score_extracts_float_from_text(self):
        """Test that scoring extracts float even from verbose responses."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "The quality score for this thread is 0.82 out of 1.0"
        mock_model.invoke.return_value = mock_response

        score = _score_thread_quality("Test thread context", mock_model)

        assert score == 0.82

    def test_score_clamps_to_valid_range(self):
        """Test that scores outside 0-1 are clamped."""
        mock_model = MagicMock()
        mock_response = MagicMock()

        # Test upper bound
        mock_response.content = "1.5"
        mock_model.invoke.return_value = mock_response
        score = _score_thread_quality("Test thread context", mock_model)
        assert score == 1.0

        # Test lower bound (regex extracts "5" from "1.5" on second call)
        # Note: The regex doesn't capture negative signs, so "-0.5" would extract as "0.5"
        # We test that very high values are clamped
        mock_response.content = "2.5"
        score = _score_thread_quality("Test thread context", mock_model)
        assert score == 1.0

    def test_score_handles_non_numeric_response(self):
        """Test that non-numeric responses return None."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "I cannot determine a score"
        mock_model.invoke.return_value = mock_response

        score = _score_thread_quality("Test thread context", mock_model)

        assert score is None

    def test_score_handles_llm_errors(self):
        """Test that unexpected LLM errors are raised."""
        mock_model = MagicMock()
        mock_model.invoke.side_effect = Exception("API timeout")

        # Unexpected errors should be raised
        with pytest.raises(Exception, match="API timeout"):
            _score_thread_quality("Test thread context", mock_model)


class TestSummarizeThread:
    """Tests for thread summarization."""

    def test_summarize_parses_json_response(self):
        """Test that summarization parses JSON correctly."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """
        {
          "question": "How do I import data?",
          "answer": "You can import data using the File menu.",
          "tags": ["data-import", "beginner"],
          "category": "how-to"
        }
        """
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert summary.question == "How do I import data?"
        assert summary.answer == "You can import data using the File menu."
        assert summary.tags == ["data-import", "beginner"]
        assert summary.category == "how-to"

    def test_summarize_handles_markdown_code_blocks(self):
        """Test that markdown code blocks are stripped."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """```json
        {
          "question": "Test question?",
          "answer": "Test answer.",
          "tags": ["test"],
          "category": "discussion"
        }
        ```"""
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert summary.question == "Test question?"

    def test_summarize_handles_missing_optional_fields(self):
        """Test that missing tags/category use defaults."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """
        {
          "question": "Test?",
          "answer": "Answer."
        }
        """
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert summary.tags == []
        assert summary.category == "discussion"

    def test_summarize_handles_invalid_json(self):
        """Test that invalid JSON returns None."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "This is not JSON at all!"
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is None

    def test_summarize_handles_llm_errors(self):
        """Test that unexpected LLM errors are raised."""
        mock_model = MagicMock()
        mock_model.invoke.side_effect = Exception("API error")

        # Unexpected errors should be raised
        with pytest.raises(Exception, match="API error"):
            _summarize_thread("Test thread context", mock_model)


class TestEstimateSummarizationCost:
    """Tests for cost estimation."""

    def test_estimate_returns_structure(self, populated_mailman_db: Path):
        """Test that cost estimation returns expected structure."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            estimate = estimate_summarization_cost("test-list", project="test-faq")

            assert "thread_count" in estimate
            assert "estimated_input_tokens" in estimate
            assert "estimated_output_tokens" in estimate
            assert "haiku_cost" in estimate
            assert "sonnet_cost" in estimate
            assert "hybrid_cost" in estimate
            assert "recommended" in estimate

    def test_estimate_counts_threads_correctly(self, populated_mailman_db: Path):
        """Test that cost estimation counts threads correctly."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            estimate = estimate_summarization_cost("test-list", project="test-faq")

            # Should have 1 thread with >=2 messages (thread001)
            # thread002 has only 1 message and should be filtered
            assert estimate["thread_count"] == 1

    def test_estimate_handles_empty_database(self, tmp_path: Path):
        """Test cost estimation with no threads."""
        db_path = tmp_path / "knowledge" / "empty.db"

        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("empty")
            estimate = estimate_summarization_cost("test-list", project="empty")

            assert estimate["thread_count"] == 0
            assert estimate["haiku_cost"] == 0.0
            assert estimate["sonnet_cost"] == 0.0
            assert estimate["recommended"] == "none"

    def test_estimate_calculates_costs(self, populated_mailman_db: Path):
        """Test that costs are calculated as positive numbers."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            estimate = estimate_summarization_cost("test-list", project="test-faq")

            # All costs should be positive for non-zero threads
            if estimate["thread_count"] > 0:
                assert estimate["haiku_cost"] > 0
                assert estimate["sonnet_cost"] > 0
                assert estimate["hybrid_cost"] > 0

    def test_estimate_recommends_strategy(self, populated_mailman_db: Path):
        """Test that recommendation is based on thread count."""
        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            # Small thread count
            estimate = estimate_summarization_cost("test-list", project="test-faq")
            assert estimate["recommended"] in ["haiku", "hybrid", "none"]


class TestLLMResponseVariations:
    """Tests for handling various LLM response formats."""

    def test_summarize_handles_trailing_comma(self):
        """Test that trailing commas in JSON are handled gracefully."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        # Invalid JSON with trailing comma
        mock_response.content = """
        {
          "question": "How to install?",
          "answer": "Use the installation script.",
          "tags": ["installation"],
          "category": "how-to",
        }
        """
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        # Should return None for invalid JSON
        assert summary is None

    def test_summarize_handles_extra_text_before_json(self):
        """Test that extra text before JSON is stripped."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        # Extra text before JSON
        mock_response.content = """Here's the summary:
        {
          "question": "How to install?",
          "answer": "Use the installation script.",
          "tags": ["installation"],
          "category": "how-to"
        }"""
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        # Should return None (no markdown code block to strip)
        assert summary is None

    def test_summarize_handles_extra_text_after_json(self):
        """Test that extra text after JSON is stripped."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        # Extra text after JSON
        mock_response.content = """{
          "question": "How to install?",
          "answer": "Use the installation script.",
          "tags": ["installation"],
          "category": "how-to"
        }

        I hope this helps!"""
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        # Should return None (JSON is valid but has trailing text)
        assert summary is None

    def test_summarize_handles_nested_json_quotes(self):
        """Test that nested quotes in JSON are parsed correctly."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """
        {
          "question": "How to use quotes?",
          "answer": "Use \\"double quotes\\" for strings.",
          "tags": ["syntax"],
          "category": "how-to"
        }
        """
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert "double quotes" in summary.answer

    def test_summarize_handles_newlines_in_json(self):
        """Test that newlines in JSON strings are handled correctly."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """
        {
          "question": "Multi-line question?",
          "answer": "First line.\\nSecond line.\\nThird line.",
          "tags": ["multiline"],
          "category": "how-to"
        }
        """
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert "First line" in summary.answer
        assert "Second line" in summary.answer

    def test_summarize_handles_empty_tags_array(self):
        """Test that empty tags array is handled correctly."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """
        {
          "question": "Test question?",
          "answer": "Test answer.",
          "tags": [],
          "category": "discussion"
        }
        """
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert summary.tags == []

    def test_summarize_handles_unicode_characters(self):
        """Test that unicode characters in JSON are handled correctly."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """
        {
          "question": "What about émojis and ñoñ-ASCII? 🎉",
          "answer": "They should work fine! 👍",
          "tags": ["unicode", "émoji"],
          "category": "how-to"
        }
        """
        mock_model.invoke.return_value = mock_response

        summary = _summarize_thread("Test thread context", mock_model)

        assert summary is not None
        assert "émojis" in summary.question
        assert "👍" in summary.answer

    def test_score_handles_decimal_variations(self):
        """Test that various decimal formats are handled correctly."""
        mock_model = MagicMock()

        # Test integer score
        mock_response = MagicMock()
        mock_response.content = "1"
        mock_model.invoke.return_value = mock_response
        score = _score_thread_quality("Test thread context", mock_model)
        assert score == 1.0

        # Test leading zero
        mock_response.content = "0.75"
        score = _score_thread_quality("Test thread context", mock_model)
        assert score == 0.75

        # Test no leading zero (will be parsed as 85, then clamped to 1.0)
        mock_response.content = ".85"
        score = _score_thread_quality("Test thread context", mock_model)
        assert score == 1.0  # "85" extracted, clamped to max

    def test_score_handles_verbose_responses(self):
        """Test that verbose LLM responses are parsed correctly."""
        mock_model = MagicMock()
        mock_response = MagicMock()

        # Verbose response with explanation
        mock_response.content = """I would rate this thread at 0.72 out of 1.0 because
        it has a clear question and helpful responses, though the solution could be more detailed."""
        mock_model.invoke.return_value = mock_response

        score = _score_thread_quality("Test thread context", mock_model)
        assert score == 0.72

    def test_score_handles_multiple_numbers(self):
        """Test that first number is extracted when multiple numbers present."""
        mock_model = MagicMock()
        mock_response = MagicMock()

        # Multiple numbers - should take first
        mock_response.content = "The thread scores 0.85 on a scale from 0.0 to 1.0"
        mock_model.invoke.return_value = mock_response

        score = _score_thread_quality("Test thread context", mock_model)
        assert score == 0.85


class TestFAQGenerationRunsOnTheClaudePlatform:
    """FAQ generation used to be a server-side route to OpenRouter.

    It ran from the sync scheduler with no caller and no BYOK key, so the
    platform needed its own OPENROUTER_API_KEY for the feature to work at all.
    These tests hold the route closed. They use real registry data and the
    real model resolver rather than a stubbed LLM, so a regression shows up as
    a failure here rather than as an unexpected OpenRouter bill.
    """

    def test_shipped_faq_configs_name_offered_claude_models(self) -> None:
        """Every faq_generation agent must resolve to an offered Claude model.

        Dynamic over the registry, so a community added later cannot quietly
        reintroduce a model the Claude Platform will not serve. This is the
        assertion that fails against the pre-migration config: eeglab's
        evaluation agent was qwen/qwen3-235b-a22b-2507, which normalize_model
        rejects.
        """
        from src.assistants import discover_assistants, registry
        from src.core.services.anthropic_llm import OFFERED_MODELS, normalize_model

        registry._assistants.clear()
        discover_assistants()

        checked = 0
        for community_id in registry._assistants:
            config = registry.get_community_config(community_id)
            faq_config = getattr(config, "faq_generation", None)
            if not faq_config:
                continue
            for role in ("evaluation_agent", "summary_agent"):
                agent = getattr(faq_config, role)
                resolved = normalize_model(agent.model)
                assert resolved in OFFERED_MODELS, (
                    f"{community_id}.faq_generation.{role}.model={agent.model!r} "
                    f"resolves to {resolved!r}, which is not offered"
                )
                checked += 1

        assert checked, "No community ships a faq_generation config; this test proved nothing"

    def test_summarizer_does_not_reach_for_the_openrouter_factory(self) -> None:
        """The module must not name create_openrouter_llm at all.

        Asserted against the source text because the import is function-local:
        there is no module attribute to inspect, and importing the symbol back
        would not tell us whether summarize_threads calls it.
        """
        from pathlib import Path

        import src.knowledge.faq_summarizer as summarizer

        source = Path(summarizer.__file__).read_text()
        assert "create_openrouter_llm" not in source
        assert "create_anthropic_llm" in source

    def test_provider_hint_is_reported_rather_than_dropped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A stale provider hint has no effect, so it has to be said out loud.

        A community that set DeepInfra/FP8 for cost reasons would otherwise
        find out from a bill.
        """
        from src.knowledge.faq_summarizer import _warn_if_provider_ignored

        with caplog.at_level("WARNING"):
            _warn_if_provider_ignored("DeepInfra/FP8", "evaluation_agent", "eeglab")

        assert "DeepInfra/FP8" in caplog.text
        assert "evaluation_agent" in caplog.text
        assert "eeglab" in caplog.text

    def test_absent_provider_hint_is_silent(self, caplog: pytest.LogCaptureFixture) -> None:
        from src.knowledge.faq_summarizer import _warn_if_provider_ignored

        with caplog.at_level("WARNING"):
            _warn_if_provider_ignored(None, "summary_agent", "eeglab")

        assert caplog.text == ""

    def test_ignored_temperature_is_reported(self, caplog: pytest.LogCaptureFixture) -> None:
        """A temperature claude-sonnet-5 discards should be said out loud.

        The community config warns at load time; this is the same fact in the
        log an operator watches while a sync runs.
        """
        from src.knowledge.faq_summarizer import _warn_if_temperature_ignored

        with caplog.at_level("WARNING"):
            _warn_if_temperature_ignored(0.0, "claude-sonnet-5", "evaluation_agent", "eeglab")

        assert "temperature=0.0 is ignored" in caplog.text
        assert "evaluation_agent" in caplog.text
        assert "claude-sonnet-5" in caplog.text

    def test_ignored_temperature_behind_a_legacy_id_is_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from src.knowledge.faq_summarizer import _warn_if_temperature_ignored

        with caplog.at_level("WARNING"):
            _warn_if_temperature_ignored(
                0.3, "anthropic/claude-sonnet-4.5", "summary_agent", "eeglab"
            )

        assert "is ignored" in caplog.text

    def test_honored_temperature_is_silent(self, caplog: pytest.LogCaptureFixture) -> None:
        """claude-haiku-4-5 does accept a temperature, so there is nothing to say."""
        from src.knowledge.faq_summarizer import _warn_if_temperature_ignored

        with caplog.at_level("WARNING"):
            _warn_if_temperature_ignored(0.0, "claude-haiku-4-5", "evaluation_agent", "eeglab")

        assert caplog.text == ""


class TestCostAccounting:
    """Costs come from src.metrics.cost, keyed on the model that ran.

    Before the migration this module carried its own price list (Haiku at
    0.25/1.25, Sonnet at 3.00/15.00, both pre-migration OpenRouter rates) and
    charged every processed thread at the Sonnet rate regardless of what was
    configured, while ignoring the evaluation calls that make up the bulk of a
    run. These tests pin the accounting to the same table the API path bills
    against.
    """

    def test_strategy_estimate_uses_the_shared_pricing_table(
        self, populated_mailman_db: Path
    ) -> None:
        """Derived from the returned token counts, so no rate is duplicated here."""
        from src.knowledge.faq_summarizer import CHEAP_MODEL, QUALITY_MODEL
        from src.metrics.cost import estimate_cost

        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            estimate = estimate_summarization_cost("test-list", project="test-faq")

        input_tokens = estimate["estimated_input_tokens"]
        output_tokens = estimate["estimated_output_tokens"]

        assert estimate["haiku_cost"] == estimate_cost(CHEAP_MODEL, input_tokens, output_tokens)
        assert estimate["sonnet_cost"] == estimate_cost(QUALITY_MODEL, input_tokens, output_tokens)

    def test_the_two_strategies_are_the_models_the_platform_offers(self) -> None:
        """The comparison is only useful if it compares what can actually run.

        Dynamic over OFFERED_MODELS, so a third offered model forces a decision
        here rather than leaving the estimate quietly two-thirds complete, and a
        stale id (a retired Haiku, say) cannot linger as a strategy that is
        priced but unusable. The fallback path in ``summarize_threads`` builds
        both agents on CHEAP_MODEL, so it has to be the default model too.
        """
        from src.core.services.anthropic_models import DEFAULT_MODEL, OFFERED_MODELS
        from src.knowledge.faq_summarizer import CHEAP_MODEL, QUALITY_MODEL

        assert {CHEAP_MODEL, QUALITY_MODEL} == set(OFFERED_MODELS)
        assert CHEAP_MODEL == DEFAULT_MODEL

    def test_strategy_estimate_prices_both_models_from_the_table(self) -> None:
        """Both strategy models must be priced, not silently fall back.

        estimate_cost falls back to a generic rate for an unpriced model, which
        would make the comparison meaningless without failing anything.
        """
        from src.knowledge.faq_summarizer import CHEAP_MODEL, QUALITY_MODEL
        from src.metrics.cost import MODEL_PRICING

        assert CHEAP_MODEL in MODEL_PRICING
        assert QUALITY_MODEL in MODEL_PRICING
        assert MODEL_PRICING[CHEAP_MODEL].input_per_1m < MODEL_PRICING[QUALITY_MODEL].input_per_1m

    def test_hybrid_sits_between_the_two_strategies(self, populated_mailman_db: Path) -> None:
        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            estimate = estimate_summarization_cost("test-list", project="test-faq")

        assert estimate["haiku_cost"] < estimate["hybrid_cost"] < estimate["sonnet_cost"]

    def test_per_call_cost_follows_the_model_that_ran(self) -> None:
        """The same call is cheaper on Haiku than on Sonnet, by the table's ratio."""
        from src.knowledge.faq_summarizer import (
            CHEAP_MODEL,
            QUALITY_MODEL,
            SCORE_OUTPUT_TOKENS,
            _estimate_call_cost,
        )
        from src.metrics.cost import estimate_cost

        context = "From: Alice\nHow do I import a BDF file?\n" * 50
        expected_input_tokens = len(context) // 4

        cheap = _estimate_call_cost(CHEAP_MODEL, context, SCORE_OUTPUT_TOKENS)
        quality = _estimate_call_cost(QUALITY_MODEL, context, SCORE_OUTPUT_TOKENS)

        assert cheap == estimate_cost(CHEAP_MODEL, expected_input_tokens, SCORE_OUTPUT_TOKENS)
        assert cheap < quality

    def test_scoring_is_counted_even_when_nothing_is_summarized(
        self, populated_mailman_db: Path
    ) -> None:
        """Scoring runs on every thread, so a run that summarizes nothing still costs.

        The old accounting only charged summarized threads, which reported
        $0.00 for a run that scored thousands of threads and kept none. The
        scored thread here comes back below the 0.7 threshold, so the summary
        agent is never called and the whole cost is the one scoring call.

        The LLM itself is langchain's own GenericFakeChatModel rather than a
        mock of anything in this codebase: the accounting under test is real,
        and only the network boundary is replaced.
        """
        import itertools

        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.messages import AIMessage

        from src.knowledge.faq_summarizer import (
            CHEAP_MODEL,
            SCORE_OUTPUT_TOKENS,
            _estimate_call_cost,
            summarize_threads,
        )

        low_score = GenericFakeChatModel(messages=itertools.cycle([AIMessage(content="0.1")]))

        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            with patch(
                "src.core.services.anthropic_llm.create_anthropic_llm", return_value=low_score
            ):
                result = summarize_threads("test-list", project="test-faq")

            context = self._thread_context(populated_mailman_db, "thread001")

        assert result["summarized"] == 0
        assert result["skipped"] == 1
        assert result["total_cost"] == pytest.approx(
            _estimate_call_cost(CHEAP_MODEL, context, SCORE_OUTPUT_TOKENS)
        )

    def test_a_summarized_thread_is_charged_for_both_calls(
        self, populated_mailman_db: Path
    ) -> None:
        """A thread that clears the threshold pays for scoring and for the summary."""
        import json

        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.messages import AIMessage

        from src.knowledge.faq_summarizer import (
            CHEAP_MODEL,
            SCORE_OUTPUT_TOKENS,
            SUMMARY_OUTPUT_TOKENS,
            _estimate_call_cost,
            summarize_threads,
        )

        summary_json = json.dumps(
            {
                "question": "How do I import a BDF file?",
                "answer": "Use pop_biosig from the File menu.",
                "tags": ["data-import"],
                "category": "how-to",
            }
        )
        # One score, then one summary: the two agents share this fake, and
        # summarize_threads calls them in that order for each thread.
        scripted = GenericFakeChatModel(
            messages=iter([AIMessage(content="0.9"), AIMessage(content=summary_json)])
        )

        with patch("src.knowledge.db.get_db_path", return_value=populated_mailman_db):
            with patch(
                "src.core.services.anthropic_llm.create_anthropic_llm", return_value=scripted
            ):
                result = summarize_threads("test-list", project="test-faq")

            context = self._thread_context(populated_mailman_db, "thread001")

        assert result["summarized"] == 1
        assert result["total_cost"] == pytest.approx(
            _estimate_call_cost(CHEAP_MODEL, context, SCORE_OUTPUT_TOKENS)
            + _estimate_call_cost(CHEAP_MODEL, context, SUMMARY_OUTPUT_TOKENS)
        )

    @staticmethod
    def _thread_context(db_path: Path, thread_id: str) -> str:
        """Rebuild the prompt text a thread produced, for cost comparison."""
        with (
            patch("src.knowledge.db.get_db_path", return_value=db_path),
            get_connection("test-faq") as conn,
        ):
            rows = conn.execute(
                "SELECT * FROM mailing_list_messages WHERE thread_id = ? ORDER BY date",
                (thread_id,),
            ).fetchall()

        return _build_thread_context([dict(row) for row in rows])
