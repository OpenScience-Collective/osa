"""Tests for ``scripts/plan_consistency.py``: the pure parts, with no network.

The streams in ``tests/fixtures`` are recordings of real runs against the develop
deployment (``propose_plan_stream.sse``: an analysis request that ended at its plan;
``search_datasets_stream.sse``: a ``nemar_search_datasets`` call, which ends in ``done``).
The script itself makes paid LLM calls and is run by hand.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from scripts import plan_consistency as pc
from src.agents.model_routing import wants_analysis

FIXTURES = Path(__file__).parent.parent / "fixtures"
PLAN_STREAM = FIXTURES / "propose_plan_stream.sse"
SEARCH_STREAM = FIXTURES / "search_datasets_stream.sse"


def _steps(*titles: str, detail: str = "") -> list[dict[str, str]]:
    return [{"title": t, "detail": detail} for t in titles]


def _top_level_imports() -> set[str]:
    names: set[str] = set()
    for node in ast.parse(Path(pc.__file__).read_text()).body:
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_network_import_at_module_top_level():
    assert not _top_level_imports() & {"httpx", "requests", "urllib", "aiohttp", "socket"}


def test_different_wording_of_the_same_operations_gives_the_same_tuple():
    a = _steps(
        "Load the recording", "Band-pass filter 1 to 40 Hz", "Epoch around targets", "Plot the ERP"
    )
    b = _steps(
        "Read the data file", "Apply a high-pass and low-pass filter", "Cut trials", "Draw a figure"
    )
    assert pc.normalize_plan(a) == pc.normalize_plan(b)
    assert pc.normalize_plan(a) == ("load", "filter", "epoch", "plot")


def test_a_different_order_gives_a_different_tuple():
    forward = _steps("Load data", "Filter the signal", "Plot it")
    swapped = _steps("Load data", "Plot it", "Filter the signal")
    assert pc.normalize_plan(forward) != pc.normalize_plan(swapped)


def test_a_vague_title_is_classified_by_its_detail():
    steps = [
        {"title": "Next step", "detail": "Apply a 1 to 40 Hz band-pass filter to the EEG."},
        {"title": "Final step", "detail": "Plot the evoked response."},
    ]
    assert pc.normalize_plan(steps) == ("filter", "plot")


def test_the_title_wins_over_the_detail():
    step = {"title": "Plot ERP", "detail": "Epoch, average and filter first."}
    assert pc.normalize_step(step) == "plot"


def test_an_unrecognised_step_is_other():
    assert pc.normalize_plan([{"title": "Ponder", "detail": "Think about it."}]) == ("other",)
    assert pc.normalize_step({"title": 5, "detail": None}) == "other"


def test_every_rule_names_an_operation_of_the_vocabulary():
    assert {op for op, _ in pc.RULES} < set(pc.VOCABULARY)
    assert "other" in pc.VOCABULARY


@pytest.mark.parametrize("steps", [None, [], ()])
def test_an_empty_or_missing_plan_is_no_plan(steps):
    assert pc.normalize_plan(steps) == ("no_plan",)


def test_three_of_four_agree():
    a, b = ("load", "plot"), ("load", "epoch", "plot")
    assert pc.consistency([a, a, b, a]) == (a, 3)
    assert pc.passes(3, 4)
    assert not pc.passes(2, 4)


def test_no_plan_and_error_never_count_as_agreement():
    a = ("load", "plot")
    assert pc.consistency([a, pc.NO_PLAN, pc.NO_PLAN, pc.NO_PLAN]) == (a, 1)
    assert pc.consistency([pc.ERROR] * 4) == ((), 0)
    assert pc.consistency([pc.NO_PLAN] * 4) == ((), 0)
    assert pc.consistency([a, a, pc.ERROR, pc.NO_PLAN]) == (a, 2)


def test_every_request_names_one_dataset_and_trips_the_analysis_router():
    assert len(pc.REQUESTS) == 5
    for request in pc.REQUESTS:
        assert wants_analysis(request.message), request.key
        assert request.dataset_id in request.message
        assert request.dataset_id.startswith("xx0999")  # develop reads staging, never nm-ids
    assert len({r.key for r in pc.REQUESTS}) == len(pc.REQUESTS)


def test_parse_plan_stream_returns_the_plan_of_a_real_recording():
    record = pc.parse_plan_stream(PLAN_STREAM.read_text().splitlines())
    assert record.kind == "plan"
    assert record.session_id
    assert 1 <= len(record.steps) <= 8
    assert all(step["title"] and step["detail"] for step in record.steps)
    assert record.findings
    assert isinstance(record.est_peak_mb, int)
    assert record.sequence == pc.normalize_plan(record.steps)


def test_parse_plan_stream_stops_at_the_tool_request():
    lines = PLAN_STREAM.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if '"event": "tool_request"' in line)
    # What follows the tool_request would change the outcome if it were read.
    tail = ['data: {"event": "error", "message": "must not be read"}', "not even json"]
    stream = iter([*lines[: index + 1], *tail])
    record = pc.parse_plan_stream(stream)
    assert record.kind == "plan"
    assert list(stream) == tail  # the parser consumed nothing past the tool_request


def test_a_stream_that_ends_in_done_has_no_plan():
    record = pc.parse_plan_stream(SEARCH_STREAM.read_text().splitlines())
    assert record.kind == "no_plan"
    assert record.sequence == pc.NO_PLAN


def test_a_tool_request_for_execute_code_is_no_plan():
    line = 'data: {"event": "tool_request", "tool": "execute_code", "args": {"code": "1"}}'
    assert pc.parse_plan_stream([line]).sequence == pc.NO_PLAN


def test_an_error_event_or_a_truncated_stream_is_an_error():
    assert (
        pc.parse_plan_stream(['data: {"event": "error", "message": "boom"}']).sequence == pc.ERROR
    )
    assert (
        pc.parse_plan_stream(['data: {"event": "session", "session_id": "s"}']).sequence == pc.ERROR
    )
    assert pc.parse_plan_stream([]).sequence == pc.ERROR


def test_the_search_parser_reads_dataset_ids_from_a_real_recording():
    found = pc.parse_search_stream(SEARCH_STREAM.read_text().splitlines())
    assert found
    assert all(re.fullmatch(r"xx0999\d\d", dataset_id) for dataset_id in found)
    assert pc.parse_search_stream(PLAN_STREAM.read_text().splitlines()) is None


def test_missing_dataset_ids_names_what_the_search_lacks():
    found = pc.parse_search_stream(SEARCH_STREAM.read_text().splitlines())
    assert found is not None
    assert pc.missing_dataset_ids(pc.REQUESTS, found) == []
    wanted = list(dict.fromkeys(r.dataset_id for r in pc.REQUESTS))
    assert pc.missing_dataset_ids(pc.REQUESTS, set()) == wanted
    assert pc.missing_dataset_ids(pc.REQUESTS, set(wanted[1:])) == wanted[:1]


def test_the_report_has_a_row_per_request_and_the_raw_titles():
    record = pc.parse_plan_stream(PLAN_STREAM.read_text().splitlines())
    request = pc.REQUESTS[0]
    failed = pc.RunRecord("error", pc.ERROR, note="HTTP 429")
    runs = {request: [record, record, record, failed]}
    report = pc.render_report("https://example.test/osa", 4, runs, "2026-01-01")
    assert f"| {request.kind} | {request.dataset_id} |" in report
    assert "| 3/4 | pass |" in report
    for step in record.steps:
        assert step["title"] in report
    assert "HTTP 429" in report
