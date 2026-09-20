"""Integration tests against the real Claude Platform on AWS endpoint.

These tests make real, paid API calls through create_anthropic_llm()'s
server mode. Keep prompts tiny so a run costs cents.

Skip condition: the plan for this module specified
``skipif(not os.getenv("ANTHROPIC_API_KEY"), ...)``, but in this worktree
ANTHROPIC_API_KEY (like ANTHROPIC_BASE_URL and ANTHROPIC_WORKSPACE_ID) lives
only in the .env file read by pydantic-settings; it is never exported into
the process environment, so os.getenv would never see it here and the
condition would always skip. create_anthropic_llm() itself reads these
credentials through Settings, so Settings is the correct source of truth
for "is server mode configured" and is used for the skip check instead.
"""

import base64
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from src.api.config import get_settings
from src.core.services.anthropic_llm import OFFERED_MODELS, create_anthropic_llm
from tests.helpers.images import BAR_FIXTURES, bar_chart_png, tallest_and_shortest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm,
    pytest.mark.skipif(
        not get_settings().anthropic_api_key,
        reason="ANTHROPIC_API_KEY is not configured (server mode requires it)",
    ),
]


def _extract_text(content: str | list) -> str:
    """Extract plain text from AIMessage content.

    Content is a plain string when thinking is off (or the model chose not
    to think for a trivial prompt), and a list of content blocks (a
    "thinking" block followed by a "text" block, or just "text") when
    thinking is on. Both shapes were observed while writing this test
    against the live endpoint, so extraction has to handle both.
    """
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


@tool
def get_secret_number() -> int:
    """Return the current secret number."""
    return 42


class TestHaikuDefaultThinking:
    """claude-haiku-4-5 with its default (budget-style) thinking configuration."""

    def test_answers_short_question(self) -> None:
        llm = create_anthropic_llm(model="claude-haiku-4-5")
        response = llm.invoke([HumanMessage(content="Reply with exactly: OK")])

        text = _extract_text(response.content)
        assert text.strip() != ""
        assert response.usage_metadata is not None
        assert response.usage_metadata["input_tokens"] > 0
        assert response.usage_metadata["output_tokens"] > 0

    def test_tool_call_round_trip_with_thinking_on(self) -> None:
        """Tool calls plus extended thinking, through the same bound object.

        The old LiteLLM wrapper's public invoke()/bind_tools() path would
        have worked; what actually broke was its _generate()/_agenerate()
        delegating to self.llm._generate(), which raises AttributeError once
        bind_tools() has replaced self.llm with a RunnableBinding (a
        RunnableBinding does not expose _generate). CachingChatAnthropic
        avoids that failure mode entirely (see its class docstring), and
        this test exercises the same bind_tools() -> invoke() path to prove
        it end to end against the live endpoint.
        """
        llm = create_anthropic_llm(model="claude-haiku-4-5")
        bound = llm.bind_tools([get_secret_number])

        messages: list[AIMessage | HumanMessage | ToolMessage] = [
            HumanMessage(
                content="Call get_secret_number and state the result in one short sentence."
            )
        ]
        first = bound.invoke(messages)
        assert first.tool_calls, f"Expected a tool call, got content: {first.content!r}"

        messages.append(first)
        for call in first.tool_calls:
            result = get_secret_number.invoke(call["args"])
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

        final = bound.invoke(messages)
        text = _extract_text(final.content)
        assert "42" in text


class TestSonnetAdaptiveThinking:
    """claude-sonnet-5 with its default (adaptive) thinking configuration."""

    def test_answers_short_question(self) -> None:
        llm = create_anthropic_llm(model="claude-sonnet-5")
        response = llm.invoke([HumanMessage(content="Reply with exactly: OK")])

        text = _extract_text(response.content)
        assert text.strip() != ""
        assert response.usage_metadata is not None
        assert response.usage_metadata["input_tokens"] > 0
        assert response.usage_metadata["output_tokens"] > 0


class TestPromptCaching:
    """Proves prompt caching actually works on the wire.

    No unit test can prove this: tests/test_core/test_anthropic_llm.py only
    asserts that a cache_control marker is present in the outgoing payload,
    never that the Claude Platform on AWS endpoint actually honors it. This
    sends the same large system prefix twice and checks that the second
    call's usage_metadata reports tokens read from cache.
    """

    def test_second_call_with_shared_system_prefix_reports_cache_read(self) -> None:
        # Haiku's minimum cacheable prefix is about 4096 tokens; repeat a
        # paragraph enough times to sit comfortably above that so this test
        # is not sensitive to the exact tokenizer count. A unique run marker
        # is mixed in so this test's cache entry cannot be a stale hit left
        # over from a previous run of this same test (which would let the
        # assertion pass without this run's own two calls proving anything).
        run_marker = uuid.uuid4().hex
        paragraph = (
            f"Run {run_marker}: the Open Science Assistant helps researchers "
            "work with BIDS, HED, and EEGLAB by answering precise, "
            "citation-backed questions for small research communities "
            "running their own lab servers. "
        )
        system_prompt = paragraph * 300

        # thinking=None keeps the generated output tiny and avoids any
        # budget/max_tokens interaction; caching (enable_caching defaults to
        # True) is exactly what this test is exercising.
        llm = create_anthropic_llm(model="claude-haiku-4-5", max_tokens=32, thinking=None)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="Reply with exactly: OK"),
        ]

        first = llm.invoke(messages)
        assert first.usage_metadata is not None
        first_cache_read = (first.usage_metadata.get("input_token_details") or {}).get(
            "cache_read"
        ) or 0
        assert first_cache_read == 0, (
            "First call with a freshly unique system prefix should not hit an "
            f"existing cache entry; got usage_metadata={first.usage_metadata!r}"
        )

        second = llm.invoke(messages)
        assert second.usage_metadata is not None
        input_token_details = second.usage_metadata.get("input_token_details") or {}
        cache_read = input_token_details.get("cache_read") or 0
        assert cache_read > 0, (
            "Expected a non-zero cache_read on the second call sharing the "
            f"same system prefix; got usage_metadata={second.usage_metadata!r}. "
            "Prompt caching is not taking effect against the live endpoint."
        )


@tool
def execute_code(code: str) -> str:
    """Run Python in the user's browser and return what it printed."""
    # Declared for its schema only: these tests synthesize the tool result the
    # browser would have returned, so nothing here ever runs the code.
    return f"ran {code}"


@tool
def report_bars(tallest_position: int, shortest_position: int) -> str:
    """Report which bar is tallest and which is shortest.

    Positions count from the left, starting at 1.
    """
    # The answer channel, not an action: the test reads the arguments off the
    # tool call rather than out of prose, so this body never runs.
    return f"tallest {tallest_position}, shortest {shortest_position}"


class TestToolResultImages:
    """Does a figure made in the browser actually reach the model?

    The browser-execution design note has ``execute_code`` run in the user's
    browser and return its output as a tool result with the plots included, and
    lists "whether the provider layer accepts image content blocks on a TOOL
    message" as an open question, noted as asserted earlier in that note
    without being verified.

    ``tests/test_core/test_tool_result_image_transport.py`` settles the half
    that needs no network: the image survives every layer in this process and
    leaves as an Anthropic image block nested in the tool result. Only a real
    request can settle the other half, which is that the endpoint accepts that
    payload and the model looks at the picture.

    Run against every offered model rather than the default one. The design
    note's premise is that plots reach "the model", and a widget lets the
    person pick; a result that held only for one model would not support that,
    and driving the list means a third offered model cannot quietly skip it.

    Three live runs answered the question and rejected a fixture each time, and
    every rejection was the fixture's fault rather than the transport's. The
    first drew "734" in a 5x7 bitmap font and drew back "724", then "704" after
    the glyph was redrawn: first and last digit right both times, so the
    picture was arriving, and five-pixel-wide glyphs simply do not survive the
    downscaling an image goes through on its way into a model. The third asked
    about seven bars and got the index wrong while reading the chart correctly.
    Both failures shared a shape worth remembering: the model could see the
    picture and the fixture could not carry the answer. What the fixture asks
    is now as easy as the question allows, so that a wrong answer means a wrong
    picture. See tests/helpers/images.py.
    """

    def _report_bars(self, model: str, heights: list[float]) -> dict:
        """One browser tool round trip, with the figure on the tool result.

        The answer comes back as the arguments of a forced ``report_bars`` call
        rather than as text, and the prompt states how many bars there are.
        Stating the count leaks nothing, since which bar is tallest lives only
        in the picture, and it removes the one thing the live runs kept failing
        on: asked about a four-bar chart, claude-haiku-4-5 named the tallest
        bar correctly and then reported the shortest as bar 5. The question is
        whether the figure arrives, so every part of it that is not about the
        figure is made as easy as it can be.

        An earlier version asked for "two digits and
        nothing else" and parsed the reply; claude-haiku-4-5 answered with a
        numbered list instead ("1. Bar 1: Medium height ... 5. Bar 5:
        Tallest"), which reads the figure perfectly and parses to the wrong
        answer, because the first digit in the reply belongs to the list and
        not to a bar. A prompt asking for a format is a request; a tool schema
        is a structure.
        """
        llm = create_anthropic_llm(model=model, thinking=None, max_tokens=256)
        bound = llm.bind_tools([execute_code, report_bars], tool_choice="report_bars")

        call_id = f"toolu_{uuid.uuid4().hex[:24]}"
        tool_content: list[dict] = [
            {"type": "text", "text": "Figure rendered."},
            {
                "type": "image",
                "base64": base64.b64encode(bar_chart_png(heights)).decode(),
                "mime_type": "image/png",
            },
        ]

        messages: list[AIMessage | HumanMessage | ToolMessage] = [
            HumanMessage(
                content=(
                    "Run the code, then look at the bar chart it produced. It has "
                    f"exactly {len(heights)} bars, numbered 1 to {len(heights)} from "
                    "the left. Report which one is tallest and which one is shortest."
                )
            ),
            AIMessage(
                content=[{"type": "text", "text": "Running it."}],
                tool_calls=[
                    {
                        "name": "execute_code",
                        "args": {"code": "render_figure()"},
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content=tool_content, tool_call_id=call_id),
        ]
        response = bound.invoke(messages)
        reports = [call for call in response.tool_calls if call["name"] == "report_bars"]
        assert reports, (
            f"{model} did not call report_bars, which was the only tool offered to it; "
            f"it replied {response.content!r}. Nothing can be concluded about the image "
            "from this run."
        )
        return reports[0]["args"]

    @pytest.mark.parametrize("fixture", sorted(BAR_FIXTURES))
    @pytest.mark.parametrize("model", sorted(OFFERED_MODELS))
    def test_model_reads_the_figure_returned_on_a_tool_message(
        self, model: str, fixture: str
    ) -> None:
        heights = BAR_FIXTURES[fixture]
        tallest, shortest = tallest_and_shortest(heights)

        answer = self._report_bars(model, heights)

        assert (answer.get("tallest_position"), answer.get("shortest_position")) == (
            tallest,
            shortest,
        ), (
            f"{model} misreported the bars of fixture {fixture!r}: expected tallest "
            f"{tallest} and shortest {shortest}, got {answer!r}. Every request in this "
            "class carries identical text and differs only in the picture, so an answer "
            "that does not track the picture means the image did not arrive, and the "
            "browser execution design cannot return plots on the tool result."
        )
