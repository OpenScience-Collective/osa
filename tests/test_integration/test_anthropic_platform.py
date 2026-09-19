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
import re
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from src.api.config import get_settings
from src.core.services.anthropic_llm import create_anthropic_llm
from tests.helpers.images import bar_chart_png

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


# The fixture figure: five bars, tallest fourth from the left, shortest second.
# Neither position appears in any text block, so a reply naming them can only
# have come from the picture.
BAR_HEIGHTS = [0.45, 0.2, 0.7, 1.0, 0.35]
TALLEST_POSITION = "4"
SHORTEST_POSITION = "2"


class TestToolResultImages:
    """Does a figure made in the browser actually reach the model?

    The browser-execution design note plans for ``execute_code`` to run in the
    user's browser and return its output as a tool result with the plots
    included, and lists "whether the provider layer accepts image content
    blocks on a TOOL message" as an open question, noted as asserted earlier in
    that note without being verified.

    ``tests/test_core/test_tool_result_image_transport.py`` settles the half
    that needs no network: the image survives every layer in this process and
    leaves as an Anthropic image block nested in the tool result. Only a real
    request can settle the other half, which is that the endpoint accepts that
    payload and the model looks at the picture.

    The first two live runs answered it and rejected a fixture at the same
    time. That fixture drew "734" in a 5x7 bitmap font; the replies were "724"
    and then "704" after the glyph was redrawn. Both got the first and last
    digit right, which is proof the picture was arriving and being read, so
    what failed was the font: five-pixel-wide glyphs do not survive the
    downscaling an image goes through on its way into a model. The fixture is
    now a bar chart, which does not degrade that way and is closer to what this
    runtime will really be asked about.

    So a failure here that reports plausible but wrong bars is a fixture
    problem in ``tests/helpers/images.py``; a failure that reports nothing, or
    says no figure was provided, is the transport.
    """

    def _figure_round_trip(self, *, with_image: bool) -> list[str]:
        """One browser tool round trip, with or without the figure attached.

        Returns the digits of the reply, in order. The model is asked for two
        bare numbers, but a sentence naming a bar twice ("the 4th bar (bar 4)")
        would repeat a digit, so the assertions read the first digit and then
        look for the second anywhere after it rather than expecting an exact
        pair.
        """
        llm = create_anthropic_llm(model="claude-haiku-4-5", thinking=None, max_tokens=64)
        bound = llm.bind_tools([execute_code])

        call_id = f"toolu_{uuid.uuid4().hex[:24]}"
        tool_content: list[dict] = [{"type": "text", "text": "Figure rendered."}]
        if with_image:
            tool_content.append(
                {
                    "type": "image",
                    "base64": base64.b64encode(bar_chart_png(BAR_HEIGHTS)).decode(),
                    "mime_type": "image/png",
                }
            )

        messages: list[AIMessage | HumanMessage | ToolMessage] = [
            HumanMessage(
                content=(
                    "Run the code, then look at the bar chart it produced and reply "
                    "with two numbers and nothing else: the position of the tallest "
                    "bar, then the position of the shortest bar, counting from the "
                    "left starting at 1."
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
        return re.findall(r"\d", _extract_text(bound.invoke(messages).content))

    def test_model_reads_a_figure_returned_on_a_tool_message(self) -> None:
        digits = self._figure_round_trip(with_image=True)

        assert digits and digits[0] == TALLEST_POSITION, (
            "The model did not name the tallest bar in the figure attached to the "
            f"tool result; the digits in its reply were {digits!r}. Either the "
            "endpoint dropped the image block or the model did not receive it, and "
            "the browser execution design cannot return plots on the tool result."
        )
        assert SHORTEST_POSITION in digits[1:], (
            f"The model named the tallest bar but not the shortest; digits {digits!r}."
        )

    def test_the_same_round_trip_without_the_figure_cannot_report_the_bars(self) -> None:
        """The control: the bar positions are in the picture and nowhere else.

        Without this, the test above would still pass if the answer ever leaked
        into a text block, into the tool arguments, or into the prompt, and it
        would then be green while proving nothing about images.
        """
        digits = self._figure_round_trip(with_image=False)

        assert not (digits[:1] == [TALLEST_POSITION] and SHORTEST_POSITION in digits[1:]), (
            f"A round trip carrying no image still produced {digits!r}, which is "
            "the answer the figure holds. It is reachable without the picture, so "
            "the image test above proves nothing."
        )
