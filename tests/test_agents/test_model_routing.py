"""Quick and analysis model routing: the decisions, the summary limit, and tool binding.

Everything here runs real code on real values; the only stand-in in this suite is the model
(`tests/helpers/chat_models.py`), used where a model's text is needed.
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.model_routing import (
    SUMMARY_MAX_WORDS,
    count_words,
    history_transcript,
    limit_words,
    reader_turn,
    routing_applies,
    seeded_message,
    summarize_history,
    tier_for_turn,
    wants_analysis,
)
from src.core.config.community import (
    START_ANALYSIS_TOOL_NAME,
    ClientToolConfig,
    CommunityConfig,
    ExtensionsConfig,
    PythonRuntimeConfig,
    RuntimeConfig,
)
from src.tools.client_tools import CLIENT_TOOL_KILL_SWITCH_ENV, build_client_tools
from tests.helpers.chat_models import ScriptedChatModel


def _config(**overrides) -> CommunityConfig:
    kwargs = {
        "id": "routed",
        "name": "Routed",
        "description": "Routes turns",
        "extensions": ExtensionsConfig(
            client_tools=[
                ClientToolConfig(name="execute_code", runtime="python", description="Run code.")
            ]
        ),
        "runtime": RuntimeConfig(
            python=PythonRuntimeConfig(pyodide_version="314.0.6", lockfile="runtime/l.json")
        ),
        "quick_model": "haiku",
        "analysis_model": "sonnet",
    }
    kwargs.update(overrides)
    return CommunityConfig(**kwargs)


DECLARED = {"execute_code", "get_full_output"}


class TestWantsAnalysis:
    @pytest.mark.parametrize(
        "message",
        [
            "plot the ERP for famous faces",
            "run this on sub-002",
            "compute the power spectrum",
            "Please ANALYZE the recording",
            "analyse sub-001",
            "show me an ERP image",
            "filter the data at 1 Hz",
            "plot the power spectra of sub-001",
            "run it again",
            "can you execute that?",
            "I am running it now",
        ],
    )
    def test_true_for_a_request_to_run_plot_or_analyze(self, message: str) -> None:
        assert wants_analysis(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "find datasets with face stimuli",
            "what is a dataset",
            "which datasets have 70 channels",
            "tell me about the trunk of the study",
            "is this a fitness study",
            # Ordinary BIDS and NEMAR vocabulary: "run" is an entity, "spectrum" is part of
            # a condition name, "filter" and "fit" are everyday words. Each of these used
            # to hand a reader to the analysis model for the rest of the conversation.
            "how many runs does each subject have",
            "which datasets have run-01 events",
            "find datasets of children with autism spectrum disorder",
            "filter datasets by modality",
            "show me the fits",
            "which datasets ran a visual oddball task",
        ],
    )
    def test_false_for_a_question_or_a_search(self, message: str) -> None:
        assert wants_analysis(message) is False

    def test_matches_whole_words_only(self) -> None:
        assert wants_analysis("a runner and a plotter") is False


class TestLimitWords:
    def _long_text(self) -> str:
        sentences = [
            f"Sentence number {i} says something about the study and its recordings."
            for i in range(40)
        ]
        return " ".join(sentences)

    def test_returns_at_most_the_limit_from_a_400_word_input(self) -> None:
        words = [f"w{i}" for i in range(400)]
        assert count_words(limit_words(" ".join(words))) <= SUMMARY_MAX_WORDS

    def test_keeps_a_dataset_id_that_is_in_the_kept_part(self) -> None:
        words = ["on002718"] + [f"w{i}" for i in range(399)]
        limited = limit_words(" ".join(words))
        assert "on002718" in limited.split()

    def test_drops_a_dataset_id_that_is_past_the_limit(self) -> None:
        words = [f"w{i}" for i in range(399)] + ["on002718"]
        assert "on002718" not in limit_words(" ".join(words))

    def test_a_short_text_is_returned_as_it_is(self) -> None:
        assert limit_words("  The reader chose on002718.  ") == "The reader chose on002718."

    def test_prefers_to_end_on_a_sentence(self) -> None:
        limited = limit_words(self._long_text())
        assert limited.endswith(".")
        assert count_words(limited) <= SUMMARY_MAX_WORDS

    def test_with_no_sentence_end_it_cuts_at_the_limit_with_an_ellipsis(self) -> None:
        limited = limit_words(" ".join(["word"] * 300), max_words=50)
        assert count_words(limited) == 50
        assert limited.endswith("…")

    def test_never_splits_a_word(self) -> None:
        limited = limit_words(" ".join(f"on{i:06d}" for i in range(300)), max_words=20)
        assert all(w.startswith("on") for w in limited.rstrip("…").split())


class TestHistoryTranscript:
    def test_is_built_from_human_and_ai_text_only(self) -> None:
        history = [
            HumanMessage(content="find datasets with face stimuli"),
            AIMessage(
                content=[
                    {"type": "thinking", "thinking": "secret reasoning", "signature": "x"},
                    {"type": "text", "text": "The closest match is on002718."},
                    {"type": "tool_use", "id": "t1", "name": "nemar_search", "input": {}},
                ],
                tool_calls=[{"name": "nemar_search", "args": {}, "id": "t1", "type": "tool_call"}],
            ),
            ToolMessage(content="TOOL OUTPUT BLOB", tool_call_id="t1"),
        ]
        transcript = history_transcript(history)
        assert "find datasets with face stimuli" in transcript
        assert "on002718" in transcript
        assert "secret reasoning" not in transcript
        assert "TOOL OUTPUT BLOB" not in transcript
        assert "nemar_search" not in transcript

    def test_keeps_the_newest_text_when_cut(self) -> None:
        history = [HumanMessage(content="old " * 100), HumanMessage(content="newest on002718")]
        transcript = history_transcript(history, max_chars=60)
        assert transcript.endswith("newest on002718")
        assert len(transcript) <= 60


class TestReaderTurn:
    def test_splits_the_latest_human_message_from_what_came_before(self) -> None:
        earlier = [HumanMessage(content="hi"), AIMessage(content="hello")]
        message, history = reader_turn([*earlier, HumanMessage(content="plot it")])
        assert message == "plot it"
        assert history == earlier

    def test_a_first_message_has_no_history(self) -> None:
        assert reader_turn([HumanMessage(content="plot it")]) == ("plot it", [])


class TestSummarizeHistory:
    def test_limits_what_the_model_writes(self) -> None:
        model = ScriptedChatModel(responses=[AIMessage(content=" ".join(["word"] * 400))])
        summary = asyncio.run(summarize_history(model, [HumanMessage(content="hi")]))
        assert count_words(summary) <= SUMMARY_MAX_WORDS

    def test_the_model_is_asked_for_the_limit_and_sees_only_the_transcript(self) -> None:
        model = ScriptedChatModel(responses=[AIMessage(content="The reader chose on002718.")])
        history = [HumanMessage(content="I chose on002718"), AIMessage(content="Noted.")]
        asyncio.run(summarize_history(model, history))
        system, human = model.seen_message_lists[0]
        assert str(SUMMARY_MAX_WORDS) in system.content
        assert "on002718" in human.content

    def test_an_empty_summary_falls_back_to_the_readers_own_words(self) -> None:
        model = ScriptedChatModel(responses=[AIMessage(content="")])
        history = [HumanMessage(content="I want on002718"), AIMessage(content="ok")]
        assert "on002718" in asyncio.run(summarize_history(model, history))

    def test_seeded_message_carries_the_summary_and_the_message(self) -> None:
        text = seeded_message("The reader chose on002718.", "plot the ERP")
        assert "on002718" in text
        assert "plot the ERP" in text


class TestRoutingApplies:
    def test_a_routed_request_applies(self) -> None:
        assert routing_applies(_config(), DECLARED, None) is True

    def test_a_community_without_the_pair_never_routes(self) -> None:
        plain = _config(quick_model=None, analysis_model=None)
        assert routing_applies(plain, DECLARED, None) is False
        assert tier_for_turn(plain, DECLARED, None, session_mode="quick", message="plot") is None

    def test_a_request_that_names_a_model_is_not_routed(self) -> None:
        assert routing_applies(_config(), DECLARED, "sonnet") is False

    def test_a_request_that_does_not_declare_execute_code_is_not_routed(self) -> None:
        assert routing_applies(_config(), {"get_full_output"}, None) is False
        assert routing_applies(_config(), None, None) is False

    def test_the_kill_switch_turns_routing_off(self, monkeypatch) -> None:
        monkeypatch.setenv(CLIENT_TOOL_KILL_SWITCH_ENV, "1")
        assert routing_applies(_config(), DECLARED, None) is False

    def test_no_config_is_not_routed(self) -> None:
        assert routing_applies(None, DECLARED, None) is False


class TestTierForTurn:
    def _tier(self, **kwargs):
        args = {"session_mode": "quick", "message": "find datasets", "resuming": False}
        args.update(kwargs)
        return tier_for_turn(_config(), DECLARED, None, **args)

    def test_a_question_is_quick(self) -> None:
        assert self._tier() == "quick"

    def test_a_keyword_message_is_analysis(self) -> None:
        assert self._tier(message="plot the ERP") == "analysis"

    def test_an_analysis_session_is_analysis_whatever_the_message(self) -> None:
        assert self._tier(session_mode="analysis") == "analysis"

    def test_a_resume_uses_the_sessions_tier(self) -> None:
        assert self._tier(resuming=True, message=None, session_mode="analysis") == "analysis"
        assert self._tier(resuming=True, message="plot it", session_mode="quick") == "quick"


class TestToolBinding:
    def _names(self, tier) -> set[str]:
        return {t.name for t in build_client_tools(_config(), DECLARED, tier=tier)}

    def test_a_quick_turn_binds_no_execute_code_and_binds_start_analysis(self) -> None:
        names = self._names("quick")
        assert "execute_code" not in names
        assert "get_full_output" not in names
        assert START_ANALYSIS_TOOL_NAME in names

    def test_an_analysis_turn_binds_what_it_always_did(self) -> None:
        names = self._names("analysis")
        assert {"execute_code", "get_full_output"} <= names
        assert START_ANALYSIS_TOOL_NAME not in names

    def test_a_turn_with_no_tier_binds_what_it_always_did(self) -> None:
        assert self._names(None) == self._names("analysis")

    def test_a_caller_cannot_declare_start_analysis_into_existence(self) -> None:
        declared = {*DECLARED, START_ANALYSIS_TOOL_NAME}
        names = {t.name for t in build_client_tools(_config(), declared, tier="analysis")}
        assert START_ANALYSIS_TOOL_NAME not in names

    def test_start_analysis_has_no_permission_gate_and_takes_no_required_args(self) -> None:
        tool = next(
            t
            for t in build_client_tools(_config(), DECLARED, tier="quick")
            if t.name == START_ANALYSIS_TOOL_NAME
        )
        assert tool.requires_permission is False
        assert tool.args_schema.model_validate({}).model_dump() == {"reason": ""}
