"""Tests for ``scripts/plan_consistency.py``: the pure parts, with no network.

The streams in ``tests/fixtures`` are recordings of real runs against the develop
deployment (``propose_plan_stream.sse``: an analysis request that ended at its plan;
``search_datasets_stream.sse``: a ``nemar_search_datasets`` call, which ends in ``done``).
The script itself makes paid LLM calls and is run by hand.
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts import plan_consistency as pc
from src.agents.model_routing import wants_analysis

FIXTURES = Path(__file__).parent.parent / "fixtures"
PLAN_STREAM = FIXTURES / "propose_plan_stream.sse"
SEARCH_STREAM = FIXTURES / "search_datasets_stream.sse"


def _steps(*titles: str, detail: str = "") -> list[dict[str, str]]:
    return [{"title": t, "detail": detail} for t in titles]


def _recorded_catalog() -> set[str]:
    found = pc.parse_search_stream(SEARCH_STREAM.read_text().splitlines())
    assert found
    return found


def _staging_prefix() -> str:
    prefix = os.path.commonprefix(sorted(_recorded_catalog()))
    assert prefix, "the recorded catalog's ids share no prefix"
    return prefix


def _recorded_plan_args() -> dict:
    for line in PLAN_STREAM.read_text().splitlines():
        if line.startswith("data:") and '"event": "tool_request"' in line:
            return json.loads(line[len("data:") :])["args"]
    raise AssertionError("no tool_request in the recorded plan stream")


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
        # develop reads staging: every id shares the prefix of the recorded catalog's ids
        assert request.dataset_id.startswith(_staging_prefix()), request.key
    assert len({r.key for r in pc.REQUESTS}) == len(pc.REQUESTS)


def test_parse_plan_stream_returns_the_plan_of_a_real_recording():
    record = pc.parse_plan_stream(PLAN_STREAM.read_text().splitlines())
    assert record.kind == "plan"
    assert record.session_id
    assert 1 <= len(record.steps) <= 8
    assert all(step["title"] and step["detail"] for step in record.steps)
    args = _recorded_plan_args()
    assert record.steps == args["steps"]  # none dropped, none reordered
    assert record.findings == args["findings"]
    assert record.est_peak_mb == args["est_peak_mb"]
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


def test_raw_runs_round_trip_and_are_renormalized_with_the_current_rules():
    record = pc.parse_plan_stream(PLAN_STREAM.read_text().splitlines())
    stale = asdict(record) | {"sequence": ["stale"]}
    failed = asdict(pc.RunRecord("error", pc.ERROR, note="HTTP 429"))
    raw = {"runs": {r.key: [stale, failed] for r in pc.REQUESTS}}
    first, second = pc.records_from_raw(raw)[pc.REQUESTS[0]]
    assert first.sequence == record.sequence  # a plan is normalized again from its steps
    assert second.sequence == pc.ERROR  # a failed run keeps its outcome


@pytest.mark.parametrize(
    ("operation", "titles"),
    [
        (
            "rereference",
            [
                "Apply average reference",
                "Re-reference to the average",
                "Reference the data to the average",
                "Apply a common average reference",
                "Re-reference to linked mastoids",
            ],
        ),
        ("epoch", ["Baseline-correct", "Epoch around target onsets", "Cut trials"]),
        (
            "stats",
            [
                "Count trials per condition",
                "Compute epochs per event type",
                "Tabulate epochs per event type",
            ],
        ),
        ("average", ["Average the epochs", "Compute the ERP"]),
        ("stats", ["Compute mean amplitude per condition"]),
        (
            "filter",
            ["Remove power-line noise", "Notch filter at 60 Hz power line", "Filter out mains hum"],
        ),
        ("reject", ["Withdraw bad trials"]),
        ("spectrum", ["Compute band power", "Estimate the power spectral density"]),
        ("plot", ["Draw a figure", "Make a figure of the result"]),
    ],
)
def test_wording_variants_of_one_operation_agree(operation, titles):
    assert {pc.normalize_step({"title": t}) for t in titles} == {operation}


def _plan_line(steps) -> str:
    event = {"event": "tool_request", "tool": "propose_plan", "args": {"steps": steps}}
    return f"data: {json.dumps(event)}"


def test_string_steps_are_kept_as_titles():
    record = pc.parse_plan_stream([_plan_line(["Load the data", "Plot the ERP"])])
    assert record.kind == "plan"
    assert record.steps == [{"title": "Load the data"}, {"title": "Plot the ERP"}]
    assert record.sequence == ("load", "plot")
    assert record.note == ""


def test_steps_that_are_neither_objects_nor_strings_are_dropped_and_reported():
    record = pc.parse_plan_stream([_plan_line([{"title": "Load it"}, 7, None, "Plot it"])])
    assert record.sequence == ("load", "plot")
    assert "2 step(s) dropped" in record.note
    only_bad = pc.parse_plan_stream([_plan_line([7, None])])
    assert only_bad.kind == "no_plan"
    assert "no steps" in only_bad.note
    assert "2 step(s) dropped" in only_bad.note


def test_a_stream_past_its_deadline_is_an_error_record():
    lines = PLAN_STREAM.read_text().splitlines()
    record = pc.parse_plan_stream(lines, deadline=time.monotonic() - 1)
    assert record.kind == "error"
    assert "time limit" in record.note


def test_a_stream_that_only_trickles_keep_alives_hits_the_deadline():
    keep_alive = itertools.repeat(": keep-alive")  # never ends, never an event
    start = time.monotonic()
    record = pc.parse_plan_stream(keep_alive, deadline=start + 0.05)
    assert record.kind == "error"
    assert time.monotonic() - start < 5


def test_the_search_parser_raises_past_its_deadline():
    lines = SEARCH_STREAM.read_text().splitlines()
    with pytest.raises(TimeoutError):
        pc.parse_search_stream(lines, deadline=time.monotonic() - 1)


def _recorded_search(*_: str) -> set[str] | None:
    return pc.parse_search_stream(SEARCH_STREAM.read_text().splitlines())


def test_confirm_datasets_passes_when_the_recorded_catalog_has_every_id():
    pc.confirm_datasets("https://x.test", pc.REQUESTS, "q", "o", search=_recorded_search)


def test_confirm_datasets_aborts_naming_the_missing_id():
    absent = pc.PlanRequest("k", "Kind", "xx0999zz", "Plot dataset xx0999zz")
    with pytest.raises(SystemExit) as exc:
        pc.confirm_datasets(
            "https://x.test", [*pc.REQUESTS, absent], "q", "o", search=_recorded_search
        )
    assert "xx0999zz" in str(exc.value)
    assert "https://x.test" in str(exc.value)


def test_confirm_datasets_retries_a_missing_result_and_an_exception():
    calls: list[int] = []

    def flaky(base_url: str, query: str, origin: str) -> set[str] | None:
        calls.append(1)
        if len(calls) == 1:
            return None  # the model did not call the tool
        if len(calls) == 2:
            raise RuntimeError("HTTP 502")
        return _recorded_search(base_url, query, origin)

    pc.confirm_datasets("https://x.test", pc.REQUESTS, "q", "o", attempts=3, search=flaky)
    assert len(calls) == 3


def test_confirm_datasets_aborts_when_every_attempt_fails():
    def broken(*_: str) -> set[str] | None:
        raise RuntimeError("HTTP 502")

    with pytest.raises(SystemExit, match="HTTP 502"):
        pc.confirm_datasets("https://x.test", pc.REQUESTS, "q", "o", search=broken)
    with pytest.raises(SystemExit, match="no nemar_search_datasets result"):
        pc.confirm_datasets("https://x.test", pc.REQUESTS, "q", "o", search=lambda *_: None)


@pytest.mark.parametrize(("repeats", "expected"), [(1, 1), (4, 3), (5, 4), (8, 6)])
def test_needed_is_three_quarters_rounded_up(repeats, expected):
    assert pc.needed(repeats) == expected
    assert pc.passes(expected, repeats)
    assert not pc.passes(expected - 1, repeats)


def test_the_report_header_uses_the_real_repeat_count():
    record = pc.parse_plan_stream(PLAN_STREAM.read_text().splitlines())
    report = pc.render_report("https://x.test", 8, {pc.REQUESTS[0]: [record] * 8}, "2026-01-01")
    assert "at least 6 of 8 repeats" in report
    assert "| 8/8 | pass |" in report


def test_the_report_says_what_the_shares_cannot_show():
    one_step = pc.parse_plan_stream([_plan_line(["Read and plot 10 s"])])
    vague = pc.parse_plan_stream([_plan_line(["Select Pz", "Plot"])])
    runs = {pc.REQUESTS[3]: [one_step] * 4, pc.REQUESTS[0]: [vague] * 4}
    report = pc.render_report("https://x.test", 4, runs, "2026-01-01")
    assert "## Caveats" in report
    assert "post hoc" in report
    assert f"{pc.REQUESTS[3].kind}: the top sequence is a single step" in report
    assert f"{pc.REQUESTS[0].kind}: 4 step(s) across the runs are `other`" in report


def test_raw_files_keep_their_requests_and_check_them_on_load():
    record = pc.parse_plan_stream(PLAN_STREAM.read_text().splitlines())
    results = {r: [record, record] for r in pc.REQUESTS}
    raw = json.loads(json.dumps(pc.raw_payload("https://x.test", "2026-01-01", results)))
    assert raw["requests"][pc.REQUESTS[0].key] == {
        "message": pc.REQUESTS[0].message,
        "dataset_id": pc.REQUESTS[0].dataset_id,
    }
    assert set(pc.records_from_raw(raw)) == set(pc.REQUESTS)

    key = pc.REQUESTS[1].key
    edited = json.loads(json.dumps(raw))
    edited["requests"][key]["message"] = "A different request."
    with pytest.raises(ValueError, match=key):
        pc.records_from_raw(edited)

    edited = json.loads(json.dumps(raw))
    edited["requests"][key]["dataset_id"] = "xx0999zz"
    with pytest.raises(ValueError, match="differs"):
        pc.records_from_raw(edited)


def test_raw_files_with_a_missing_request_or_uneven_repeats_are_refused():
    record = pc.parse_plan_stream(PLAN_STREAM.read_text().splitlines())
    results = {r: [record, record] for r in pc.REQUESTS}
    raw = json.loads(json.dumps(pc.raw_payload("https://x.test", "2026-01-01", results)))

    missing = json.loads(json.dumps(raw))
    del missing["runs"][pc.REQUESTS[2].key]
    with pytest.raises(ValueError, match=pc.REQUESTS[2].key):
        pc.records_from_raw(missing)

    uneven = json.loads(json.dumps(raw))
    uneven["runs"][pc.REQUESTS[0].key].pop()
    with pytest.raises(ValueError, match="different numbers of repeats"):
        pc.records_from_raw(uneven)


def test_titles_audited_after_the_first_live_run_keep_their_operation():
    # The two edits the first live run caused: an inspection step is `load`, and
    # tabulating epoch counts is `stats`, not epoching.
    assert pc.normalize_step({"title": "Check channel labels"}) == "load"
    assert pc.normalize_step({"title": "Tabulate epochs per subject and event type"}) == "stats"


def test_short_stems_need_word_boundaries():
    # "draw" inside "Withdraw" and "figure" in "Figure out" are not plotting
    assert pc.normalize_step({"title": "Withdraw bad trials"}) == "reject"
    assert pc.normalize_step({"title": "Figure out which channels are bad"}) != "plot"


def test_step_basis_says_what_decided_the_operation():
    assert pc.step_basis({"title": "Plot ERP", "detail": "Filter first."}) == ("plot", "title")
    assert pc.step_basis({"title": "Next step", "detail": "Apply a notch filter."}) == (
        "filter",
        "detail",
    )
    assert pc.step_basis({"title": "Ponder", "detail": "Think."}) == ("other", "none")
    assert pc.step_basis("Load the data") == ("load", "title")
    assert pc.step_basis({"detail": "Plot it."}) == ("plot", "detail")


def test_the_report_shows_the_detail_that_decided_a_vague_title():
    steps = [
        {"title": "Next step", "detail": "Apply a 1 to 40 Hz band-pass filter to the EEG."},
        {"detail": "Plot the evoked response. " + "More words. " * 20},
        {"title": "Plot ERP", "detail": "Epoch first."},
    ]
    record = pc.parse_plan_stream([_plan_line(steps)])
    runs = {pc.REQUESTS[0]: [record] * 4}
    report = pc.render_report("https://x.test", 4, runs, "2026-01-01")
    assert "| 1 | plan | 1 | filter | detail | Next step [detail: Apply a 1 to 40 Hz" in report
    assert "| (no title) [detail: Plot the evoked response." in report
    assert "| 1 | plan | 3 | plot | title | Plot ERP |" in report  # title decided: no detail
    assert "..." in report  # a long detail is truncated
    assert "8 of 12 step(s) were classified by their detail" in report


def test_save_raw_writes_a_file_that_loads_again(tmp_path):
    record = pc.parse_plan_stream(PLAN_STREAM.read_text().splitlines())
    results = {r: [record, record] for r in pc.REQUESTS}
    path = tmp_path / "raw.json"
    pc.save_raw(path, "https://x.test", "2026-01-01", results)
    rebuilt = pc.records_from_raw(json.loads(path.read_text()))
    assert [[run.sequence for run in runs] for runs in rebuilt.values()] == [
        [record.sequence] * 2 for _ in pc.REQUESTS
    ]
