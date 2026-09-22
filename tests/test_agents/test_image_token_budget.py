"""A conversation that carries a picture has to fit the budget.

This file exists because of a correction. The browser-execution design note and issue
#422 both assert that `count_tokens_approximately` cannot count images, citing a
`repr()` fallback that measures a 250 KB PNG at roughly 85,000 tokens against an 80,000
budget, and conclude that this repository needs its own image-aware counter.

That was true, and is not true of the version this project runs. langchain-core grew a
`tokens_per_image` penalty, so the helper now walks content blocks and charges a flat
rate per image instead of stringifying base64. Measured here rather than asserted:
1.2.6 has no such parameter, 1.6.3 does, and `pyproject.toml` now floors the dependency
at 1.6 for exactly this reason.

So there is no custom counter. What there is instead is a floor and these tests, which
fail if the floor ever slips or upstream reverts, because the failure mode is silent:
nothing raises, the budget is simply blown and the oldest messages are dropped.
"""

import base64

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from src.agents.base import DEFAULT_MAX_CONVERSATION_TOKENS
from src.api.tool_results import MAX_IMAGES, ClientToolResult, ToolResultImage
from src.api.tool_results import build_live_tool_message as live_message

CALL_ID = "toolu_01aaaaaaaaaaaaaaaaaaaaaa"

# Deliberately larger than any real figure measured in issue #422, whose largest was a
# 430,440-character spectrogram. If a fixture this big does not blow the budget, no real
# plot will either.
HUGE_B64 = base64.b64encode(b"\x89PNG\r\n" + b"\x00" * 600_000).decode()

#: What Anthropic actually charges for a largest-allowed image, give or take: it bills
#: about `width * height / 750` tokens and caps near this. Passed explicitly because the
#: upstream default of 85 is OpenAI's LOW-RESOLUTION cost, and under-counting is the
#: dangerous direction. Over-counting spends a little headroom; under-counting means
#: believing a request fits when it does not.
ANTHROPIC_TOKENS_PER_IMAGE = 1600


def _image(data: str = HUGE_B64) -> ToolResultImage:
    return ToolResultImage(mime="image/png", data_base64=data, width=1024, height=768)


def _conversation(image_count: int = 1) -> list:
    return [
        HumanMessage(content="Plot the alpha power."),
        AIMessage(
            content="Running it.",
            tool_calls=[{"name": "execute_code", "args": {"code": "plot()"}, "id": CALL_ID}],
        ),
        live_message(
            ClientToolResult(
                call_id=CALL_ID,
                summary="peak 10.2 Hz",
                images=[_image() for _ in range(image_count)],
            )
        ),
    ]


class TestTheFixtureIsBigEnoughToMatter:
    def test_it_dwarfs_the_largest_real_figure_measured(self) -> None:
        """Guards the premise of every test below.

        If this shrank, the budget tests would keep passing while measuring something
        that could never have blown a budget in the first place.
        """
        assert len(HUGE_B64) > 430_440, f"only {len(HUGE_B64)} base64 chars"


class TestUpstreamCountsImagesAsImages:
    def test_the_installed_helper_takes_a_per_image_penalty(self) -> None:
        """A downgrade below the 1.6 floor removes this parameter, and this is what
        says so out loud rather than letting the budget quietly break."""
        import inspect

        assert "tokens_per_image" in inspect.signature(count_tokens_approximately).parameters

    def test_base64_is_not_counted_as_text(self) -> None:
        """The whole correction, stated as a measurement.

        Were the payload stringified, 600 KB of image would land near 200,000 tokens.
        """
        counted = count_tokens_approximately(_conversation())

        assert counted < 1_000, f"image payload appears to be counted as text: {counted}"

    def test_an_image_costs_the_penalty_and_nothing_more(self) -> None:
        with_image = count_tokens_approximately(
            [
                ToolMessage(
                    content=[{"type": "image", "source": {"data": HUGE_B64}}], tool_call_id="c"
                )
            ],
            tokens_per_image=ANTHROPIC_TOKENS_PER_IMAGE,
        )
        without = count_tokens_approximately(
            [ToolMessage(content=[], tool_call_id="c")],
            tokens_per_image=ANTHROPIC_TOKENS_PER_IMAGE,
        )

        assert with_image - without == ANTHROPIC_TOKENS_PER_IMAGE

    @pytest.mark.parametrize(
        "block",
        [
            {"type": "image", "source": {"type": "base64", "data": HUGE_B64}},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{HUGE_B64}"}},
        ],
        ids=["anthropic_native", "data_url"],
    )
    def test_both_block_spellings_are_charged_as_images(self, block: dict) -> None:
        """Four spellings survive to the provider intact (issue #421). The two that
        carry a distinct `type` are the two upstream keys on."""
        counted = count_tokens_approximately(
            [ToolMessage(content=[block], tool_call_id="c")],
            tokens_per_image=ANTHROPIC_TOKENS_PER_IMAGE,
        )

        assert counted < ANTHROPIC_TOKENS_PER_IMAGE + 100

    def test_text_beside_an_image_still_counts(self) -> None:
        """Charging the message a flat rate and discarding its text would lose the
        summary, which is the part the model actually reasons over."""
        summary = "peak 10.2 Hz. " * 200

        with_text = count_tokens_approximately(
            [
                ToolMessage(
                    content=[
                        {"type": "text", "text": summary},
                        {"type": "image", "source": {"data": HUGE_B64}},
                    ],
                    tool_call_id="c",
                )
            ]
        )
        image_only = count_tokens_approximately(
            [
                ToolMessage(
                    content=[{"type": "image", "source": {"data": HUGE_B64}}], tool_call_id="c"
                )
            ]
        )

        assert with_text - image_only == pytest.approx(len(summary) / 4, rel=0.1)


class TestTheBudgetHolds:
    def test_a_full_result_fits_at_the_anthropic_rate(self) -> None:
        """The worst case this phase's caps allow: the maximum number of images, each
        larger than any real figure, charged at Anthropic's ceiling rather than
        OpenAI's low-resolution default."""
        counted = count_tokens_approximately(
            _conversation(image_count=MAX_IMAGES),
            tokens_per_image=ANTHROPIC_TOKENS_PER_IMAGE,
        )

        assert counted < DEFAULT_MAX_CONVERSATION_TOKENS
