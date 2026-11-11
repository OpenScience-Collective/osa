"""One real search overview, from the real quick model, over recorded results.

The companion test closer to the wire that `.rules/testing_guidelines.md` asks for next to
``tests/test_api/test_search_overview.py``, which drives the route with a scripted model:
that a real model follows the prompt, writes the JSON it asks for and uses the ``[[id]]``
markers is not observable offline.

The input is ``tests/fixtures/overview_results.json``: rows recorded from the NEMAR
staging API (``api-test.nemar.org/datasets``, the ``xx0999NN`` test datasets). What is
asserted is what must hold of any answer, not its wording: every id the answer cites is one
of the ids it was given, and the summary has words and fits the length cap.

A real, paid API call (one short request on the quick model).
"""

import json
from pathlib import Path

import pytest

from src.api.config import get_settings
from src.api.overview import (
    MAX_PICKS,
    OVERVIEW_MAX_TOKENS,
    OVERVIEW_REASONING_EFFORT,
    SUMMARY_MAX_WORDS,
    DatasetSegment,
    OverviewRequest,
    _plain_words,
    generate_overview,
)
from src.core.services.anthropic_llm import create_anthropic_llm
from src.core.services.anthropic_models import HAIKU

pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm,
    pytest.mark.skipif(
        not get_settings().anthropic_api_key,
        reason="ANTHROPIC_API_KEY is not configured (server mode requires it)",
    ),
]

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "overview_results.json"


async def test_a_real_overview_cites_only_the_results_it_was_given() -> None:
    request = OverviewRequest.model_validate(json.loads(FIXTURE.read_text()))
    given = {row.id for row in request.results}
    chat_model = create_anthropic_llm(
        model=HAIKU,
        reasoning_effort=OVERVIEW_REASONING_EFFORT,
        max_tokens=OVERVIEW_MAX_TOKENS,
        settings=get_settings(),
    )

    run = await generate_overview(chat_model, request, model=HAIKU)

    response = run.response
    assert set(response.cited_ids) <= given
    for segment in response.summary:
        if isinstance(segment, DatasetSegment):
            assert segment.dataset_id in given
    assert response.summary
    assert 0 < _plain_words(response.summary) <= SUMMARY_MAX_WORDS
    assert len(response.picks) <= MAX_PICKS
    assert {pick.id for pick in response.picks} <= given
    assert response.shown == len(request.results)
    assert run.usage.input_tokens > 0
    assert run.usage.output_tokens > 0
