"""Does the NEMAR analysis model propose the same plan for the same request? (E2.5, #663)

    uv run python scripts/plan_consistency.py --repeats 4 --out /tmp/plan-report.md
    uv run python scripts/plan_consistency.py --base-url https://api.osc.earth/osa-dev

WARNING: this script makes PAID LLM calls (the analysis model, once per repeat, plus a
cheap dataset search) against a live deployment. It is run by hand and never by CI: it
lives under ``scripts/``, outside pytest's ``testpaths``, and CI also deselects the
``network`` and ``llm`` markers. Its pure functions are tested in
``tests/test_scripts/test_plan_consistency.py`` from recorded streams, without a network.

What it does. Five fixed analysis requests, each sent ``--repeats`` times (default 4),
one after another, every repeat on a new session (no ``session_id``), to
``POST {base}/nemar/chat``. Nothing is executed: a run stops at the first ``tool_request``
for ``propose_plan`` (the plan-first gate, ``src/agents/plan_first.py``) and the
connection is closed there, so no ``/chat/resume`` is needed and no code runs in a
browser. A ``tool_request`` for another tool, a ``done`` or an ``error`` before it is a
non-plan outcome.

How plans are compared. Each plan step is mapped to one operation of a small fixed
vocabulary by the keyword rules in ``RULES`` (the title first, then the detail, the first
rule in priority order that matches wins), and two plans agree when their sequences of
operations are equal. Wording does not matter, order does. A request passes when its most
common sequence occurs in at least 3 of 4 repeats (the same share for another
``--repeats``, rounded up). A run that proposed no plan (``('no_plan',)``) or failed
(``('error',)``) never counts as agreement. The report prints the raw step titles beside
the operations (and, for a step whose title matched no rule, the detail that decided it)
so a reviewer can audit the rules. Every run is saved as JSON after each repeat, so a
crash or interrupt keeps what was paid for; ``--from-raw`` re-renders it.

Limits of the comparison, which the report repeats. A step no rule matches is ``other``,
and two ``other`` steps compare equal, so unlike steps can agree; a plan of one step
agrees with itself trivially. The rules were tuned against the first live run and
adjusted in review (see ``RULES``), so a share measured with the tuned table is not an independent measurement.
Each repeat also has an overall time limit (``MAX_RUN_SECONDS``), because the read timeout
alone is never hit by a stream that keeps sending events.

Before any repeat the script asks the same deployment for ``nemar_search_datasets`` and
aborts, naming the id, if a dataset a request names is not in the result: the develop
deployment reads NEMAR's staging server, which holds only ``xx0999NN`` test datasets.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeVar

DEFAULT_BASE_URL = "https://develop-widget.osc.earth/osa"
DEFAULT_ORIGIN = "https://develop-demo.osc.earth"
# A custom User-Agent: the worker answers urllib's default with a 403.
USER_AGENT = "osa-plan-consistency/1.0"
COMMUNITY = "nemar"
CLIENT_TOOLS = ["execute_code", "get_full_output", "propose_plan"]
MEMORY_BUDGET_MB = 1024
# The staging catalog's marker; ids themselves are not searchable, names are.
DEFAULT_SEARCH_QUERY = "TEST COPY"
SEARCH_TOOL = "nemar_search_datasets"
PLAN_TOOL = "propose_plan"

T = TypeVar("T")

REPEATS = 4
# The read timeout is per read, so a stream that keeps sending events never hits it; this
# is the limit on the whole stream of one repeat (or one search).
MAX_RUN_SECONDS = 600.0
# A request passes when its top sequence covers at least this share of the repeats.
PASS_NUMERATOR, PASS_DENOMINATOR = 3, 4

NO_PLAN: tuple[str, ...] = ("no_plan",)
ERROR: tuple[str, ...] = ("error",)
NON_AGREEMENT: tuple[tuple[str, ...], ...] = (NO_PLAN, ERROR)


@dataclass(frozen=True)
class PlanRequest:
    """One fixed analysis request. Every message contains a word that trips
    ``wants_analysis`` (plot, analyze, compute), or the quick model would answer."""

    key: str
    kind: str
    dataset_id: str
    message: str


REQUESTS: tuple[PlanRequest, ...] = (
    PlanRequest(
        "erp_by_condition",
        "ERP by condition",
        "xx099904",
        "Plot the average ERP at Pz for target versus non-target stimuli in dataset xx099904.",
    ),
    PlanRequest(
        "band_power",
        "Band power",
        "xx099906",
        "Compute the delta, theta, alpha and beta band power for the first recording of "
        "dataset xx099906 and plot it as a bar chart.",
    ),
    PlanRequest(
        "epoch_counts",
        "Epoch counts",
        "xx099904",
        "Analyze dataset xx099904: compute how many epochs there are per event type "
        "across all its subjects and plot the counts.",
    ),
    PlanRequest(
        "raw_segment",
        "Raw segment plot",
        "xx099905",
        "Plot a 10 second segment of the raw EEG from the first recording of dataset xx099905.",
    ),
    PlanRequest(
        "topography",
        "Topography",
        "xx099905",
        "Plot the scalp topography of mu band (8 to 12 Hz) power for the first "
        "recording of dataset xx099905.",
    ),
)

# --- Normalization ---------------------------------------------------------------------

VOCABULARY = (
    "load",
    "filter",
    "rereference",
    "epoch",
    "reject",
    "average",
    "spectrum",
    "plot",
    "stats",
    "export",
    "other",
)

# The one table that decides what a step is. Read it top to bottom: the FIRST row whose
# pattern matches wins, so the row order is the priority order. Rows go from the step's
# product to its preparation, so "Epoch and average at Pz" is `average` and "Load and
# plot" is `plot`. `rereference` sits above `average` so "re-reference to the average"
# is not read as an averaging step. Patterns are matched case-insensitively against the
# title; only when no row matches the title is the detail tried the same way, and a step
# neither matches is `other`. `load` also takes the inspection steps (check, find, list:
# reading what the data is), which the vocabulary has no word for. A reviewer tunes the
# reading by editing this table and re-renders a saved run with `--from-raw`; the report
# prints raw titles to check it against. This table was edited after the first live
# run (inspection steps read as `load`, "tabulate epochs" read as `stats`, average-reference
# wording as `rereference`) and again in review (word boundaries on `draw` and `figure`,
# power-line wording as `filter`), so the shares of that run are post hoc; the titles that
# settled the edits are pinned in the tests.
RULES: tuple[tuple[str, str], ...] = (
    (
        "plot",
        r"plot|\bfigures?\b(?! out)|visuali[sz]|topograph|topomap|scalp map|chart|graph|display|\bdraw",
    ),
    ("export", r"export|\bsave\b|\bcsv\b|write (?:out|to)|store (?:the )?results?"),
    (
        "stats",
        r"statistic|t-?test|anova|signific|correlat|compar|summari[sz]|tally|tabulat|"
        r"\bcounts?\b|number of|(?:epochs?|trials?|events?) per (?:event|condition|subject|type|class)|"
        r"peak (?:latenc|amplitude)|latenc(?:y|ies)|mean (?:amplitude|value)s? per",
    ),
    (
        "spectrum",
        r"spectr|\bpsd\b|welch|\bfft\b|wavelet|time-frequency|band[- ]?power|\bpower\b(?![- ]line)",
    ),
    (
        "rereference",
        r"re-?referenc|(?:average|common)[- ]referenc|referenc\w*(?: \w+){0,3} to (?:the )?(?:average|mastoids?)|"
        r"common average|\bcar\b|linked mastoid",
    ),
    ("average", r"averag|grand mean|\bmean\b|\berps?\b|evoked|contrast"),
    (
        "reject",
        r"reject|artifact|artefact|bad[- ](?:channels?|epochs?|segments?|trials?)|drop|exclud|"
        r"outlier|\bica\b|peak-to-peak|amplitude threshold",
    ),
    ("epoch", r"epoch|trial|baseline|time-locked|event-locked|\bcut\b"),
    (
        "filter",
        r"filter|band-?pass|high-?pass|low-?pass|notch|resampl|downsampl|detrend|smooth|"
        r"power[- ]line|line[- ]noise|mains",
    ),
    (
        "load",
        r"\bload|\bread\b|fetch|\bopen\b|import|inspect|retriev|access|"
        r"check|confirm|\bfind\b|identify|\blist\b|describe|look up",
    ),
)
_COMPILED_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (op, re.compile(pattern, re.IGNORECASE)) for op, pattern in RULES
)


def classify_text(text: str) -> str | None:
    """The first rule's operation that matches ``text``, or None when none does."""
    for op, pattern in _COMPILED_RULES:
        if pattern.search(text):
            return op
    return None


def _title_detail(step: Any) -> tuple[Any, Any]:
    """A step's title and detail; a bare string is a title."""
    if isinstance(step, dict):
        return step.get("title"), step.get("detail")
    return step, None


def step_basis(step: Any) -> tuple[str, str]:
    """The operation of one step and what decided it: ``title``, ``detail`` or ``none``.

    The title is tried first, then the detail; a step neither classifies is ``other``,
    decided by ``none``.
    """
    for basis, text in zip(("title", "detail"), _title_detail(step), strict=True):
        if isinstance(text, str) and (op := classify_text(text)):
            return op, basis
    return "other", "none"


def normalize_step(step: Any) -> str:
    """One step to one operation of ``VOCABULARY``: title first, then detail, else other."""
    return step_basis(step)[0]


def normalize_plan(steps: Sequence[Any] | None) -> tuple[str, ...]:
    """A plan's steps as a tuple of operation names; an empty or missing plan is ``no_plan``."""
    if not steps:
        return NO_PLAN
    return tuple(normalize_step(step) for step in steps)


def consistency(sequences: Iterable[tuple[str, ...]]) -> tuple[tuple[str, ...], int]:
    """The most common sequence and how many of the runs have it.

    ``('no_plan',)`` and ``('error',)`` never count as agreement: they are left out of the
    count, so a request whose runs all failed returns ``((), 0)``. A tie goes to the
    sequence seen first. The caller knows the number of repeats.
    """
    counts = Counter(seq for seq in sequences if seq not in NON_AGREEMENT)
    if not counts:
        return (), 0
    return counts.most_common(1)[0]


def needed(repeats: int) -> int:
    """How many of ``repeats`` runs must agree: 3 of 4, or the same share rounded up."""
    return -(-repeats * PASS_NUMERATOR // PASS_DENOMINATOR)


def passes(count: int, repeats: int) -> bool:
    """Whether ``count`` of ``repeats`` runs agreeing is enough: 3 of 4, or the same share."""
    return repeats > 0 and count * PASS_DENOMINATOR >= repeats * PASS_NUMERATOR


# --- The SSE stream ----------------------------------------------------------------------


def iter_events(lines: Iterable[str], deadline: float | None = None) -> Iterator[dict[str, Any]]:
    """The JSON objects of an SSE stream's ``data:`` lines, lazily and in order.

    Lazy on purpose: a caller that returns at the event it wants stops reading, and
    closing the response then ends the connection. A line that is not JSON is skipped.
    Past ``deadline`` (a ``time.monotonic()`` value), the next line raises TimeoutError,
    even a keep-alive line, so a stream that never ends cannot hold a repeat forever.
    """
    for raw in lines:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("overall time limit for one stream exceeded")
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[len("data:") :].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


@dataclass
class RunRecord:
    """What one repeat produced. ``steps`` are the raw ``{title, detail}`` of the plan."""

    kind: Literal["plan", "no_plan", "error"]
    sequence: tuple[str, ...]
    steps: list[dict[str, Any]] = field(default_factory=list)
    findings: str = ""
    est_peak_mb: int | None = None
    session_id: str = ""
    note: str = ""


def coerce_steps(raw_steps: Any) -> tuple[list[dict[str, Any]], int]:
    """The plan's steps as dicts, and how many entries could not be one.

    A string is a step with that title (``normalize_step`` reads strings too); a dict is
    kept; anything else is dropped and counted, so the caller can say so in the report.
    """
    steps: list[dict[str, Any]] = []
    dropped = 0
    for step in raw_steps if isinstance(raw_steps, list) else []:
        if isinstance(step, dict):
            steps.append(step)
        elif isinstance(step, str):
            steps.append({"title": step})
        else:
            dropped += 1
    return steps, dropped


def parse_plan_stream(lines: Iterable[str], deadline: float | None = None) -> RunRecord:
    """Read a ``/chat`` stream up to the first outcome and stop there.

    A ``tool_request`` for ``propose_plan`` is the plan (its args are returned and nothing
    after it is read). A ``tool_request`` for another tool or a ``done`` is no plan; an
    ``error`` event, a stream that ends with no outcome, or one still open at ``deadline``
    is an error.
    """
    session_id = ""
    try:
        for event in iter_events(lines, deadline):
            kind = event.get("event")
            if kind == "session":
                session_id = str(event.get("session_id", ""))
            elif kind == "tool_request":
                tool = event.get("tool")
                if tool != PLAN_TOOL:
                    return RunRecord(
                        "no_plan", NO_PLAN, session_id=session_id, note=f"tool_request for {tool}"
                    )
                args = event.get("args") if isinstance(event.get("args"), dict) else {}
                steps, dropped = coerce_steps(args.get("steps"))
                peak = args.get("est_peak_mb")
                notes = []
                if not steps:
                    notes.append("propose_plan with no steps")
                if dropped:
                    notes.append(f"{dropped} step(s) dropped: neither an object nor a string")
                return RunRecord(
                    "plan" if steps else "no_plan",
                    normalize_plan(steps),
                    steps=steps,
                    findings=str(args.get("findings", "")),
                    est_peak_mb=peak if isinstance(peak, int) else None,
                    session_id=session_id,
                    note="; ".join(notes),
                )
            elif kind == "done":
                return RunRecord(
                    "no_plan", NO_PLAN, session_id=session_id, note="done before propose_plan"
                )
            elif kind == "error":
                return RunRecord(
                    "error",
                    ERROR,
                    session_id=session_id,
                    note=f"error event: {event.get('message', '')}",
                )
    except TimeoutError as exc:
        return RunRecord("error", ERROR, session_id=session_id, note=str(exc))
    return RunRecord("error", ERROR, session_id=session_id, note="stream ended with no outcome")


# Dataset ids inside a search tool's output, which arrives as the repr of a ToolMessage
# whose content is JSON, so the quotes around the id may be escaped.
_DATASET_ID = re.compile(r'dataset_id\\*"\s*:\s*\\*"([^"\\]+)')


def parse_search_stream(lines: Iterable[str], deadline: float | None = None) -> set[str] | None:
    """The dataset ids of the first ``nemar_search_datasets`` result in a stream.

    Stops reading at that result. None when the stream has no such result (the model did
    not call the tool, or the stream failed first), which is not the same as an empty
    catalog, so the caller can tell them apart. Past ``deadline`` it raises TimeoutError.
    """
    for event in iter_events(lines, deadline):
        if event.get("event") == "tool_end" and event.get("name") == SEARCH_TOOL:
            return set(_DATASET_ID.findall(str(event.get("output", ""))))
        if event.get("event") in ("done", "error"):
            return None
    return None


def missing_dataset_ids(requests: Iterable[PlanRequest], found: set[str]) -> list[str]:
    """The dataset ids that requests name and a search result lacks, in request order."""
    missing: list[str] = []
    for request in requests:
        if request.dataset_id not in found and request.dataset_id not in missing:
            missing.append(request.dataset_id)
    return missing


# --- The network (httpx is imported inside, so importing this module needs no network) -----


def _chat(
    base_url: str,
    body: dict[str, Any],
    origin: str,
    parse: Callable[[Iterable[str], float], T],
    timeout: float,
) -> T:
    """POST one streaming ``/nemar/chat`` request, hand its lines to ``parse``, then close.

    ``parse(lines, deadline)`` returns what the caller wants from the stream; so does this.
    """
    import httpx

    url = f"{base_url.rstrip('/')}/{COMMUNITY}/chat"
    headers = {"Origin": origin, "Content-Type": "application/json", "User-Agent": USER_AGENT}
    client = httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0))
    with client, client.stream("POST", url, json=body, headers=headers) as response:
        if response.status_code != 200:
            response.read()
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        return parse(response.iter_lines(), time.monotonic() + MAX_RUN_SECONDS)


def run_once(base_url: str, request: PlanRequest, origin: str, timeout: float = 300.0) -> RunRecord:
    """One repeat: a new session (no ``session_id``) that stops at the plan."""
    body = {
        "message": request.message,
        "stream": True,
        "client_tools": CLIENT_TOOLS,
        "page_context": {"dataset_id": request.dataset_id, "memory_budget_mb": MEMORY_BUDGET_MB},
    }
    try:
        return _chat(base_url, body, origin, parse_plan_stream, timeout)
    except Exception as exc:  # network, timeout or HTTP status: an error outcome, not a crash
        return RunRecord("error", ERROR, note=f"{type(exc).__name__}: {exc}")


def search_catalog(
    base_url: str, query: str, origin: str, timeout: float = 120.0
) -> set[str] | None:
    """The dataset ids a ``nemar_search_datasets`` call on this deployment returns."""
    body = {
        "message": f'Call nemar_search_datasets with query "{query}" and limit 100, '
        "then list every dataset id it returns.",
        "stream": True,
        "client_tools": CLIENT_TOOLS,
        "page_context": {"memory_budget_mb": MEMORY_BUDGET_MB},
    }
    return _chat(base_url, body, origin, parse_search_stream, timeout)


def confirm_datasets(
    base_url: str,
    requests: Sequence[PlanRequest],
    query: str,
    origin: str,
    attempts: int = 2,
    search: Callable[[str, str, str], set[str] | None] = search_catalog,
) -> None:
    """Abort (SystemExit) unless every dataset a request names is in the deployment's search.

    ``search(base_url, query, origin)`` returns the ids, or None when the model made no
    ``nemar_search_datasets`` call; it is a parameter so a test can feed it a recorded
    stream. A None result and an exception are both retried, up to ``attempts`` times.
    """
    found: set[str] | None = None
    failure = f"no {SEARCH_TOOL} result"
    for _ in range(attempts):
        try:
            found = search(base_url, query, origin)
        except Exception as exc:
            failure = f"the dataset search failed ({type(exc).__name__}: {exc})"
            continue
        if found is not None:
            break
        failure = f"no {SEARCH_TOOL} result"
    if found is None:
        raise SystemExit(f"abort: {failure} from {base_url} after {attempts} tries")
    if missing := missing_dataset_ids(requests, found):
        raise SystemExit(
            f"abort: dataset id(s) {', '.join(missing)} not in {SEARCH_TOOL} "
            f"(query {query!r}) on {base_url}; found: {', '.join(sorted(found)) or 'nothing'}"
        )


# --- The report --------------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _seq(sequence: tuple[str, ...]) -> str:
    return " > ".join(sequence) if sequence else "none"


DETAIL_CHARS = 80


def _title_cell(step: Any, basis: str) -> str:
    """The raw title for the audit table; a step the title did not decide shows its detail too."""
    title, detail = _title_detail(step)
    shown = title.strip() if isinstance(title, str) and title.strip() else "(no title)"
    if basis != "title" and isinstance(detail, str) and detail.strip():
        text = " ".join(detail.split())
        if len(text) > DETAIL_CHARS:
            text = text[: DETAIL_CHARS - 3] + "..."
        shown += f" [detail: {text}]"
    return _cell(shown)


def caveats(results: dict[PlanRequest, list[RunRecord]]) -> list[str]:
    """What a reader must know before trusting the shares, derived from these runs."""
    lines = [
        "The normalization rules were tuned against the first live run and then widened in "
        "review for titles audited from it, so shares measured with the table are post hoc, "
        "not an independent measurement.",
        "A step no rule matches is `other`, and two `other` steps compare equal.",
    ]
    decided = Counter(
        step_basis(step)[1] for runs in results.values() for run in runs for step in run.steps
    )
    if decided["detail"]:
        lines.append(
            f"{decided['detail']} of {sum(decided.values())} step(s) were classified by their "
            "detail because the title matched no rule; the table shows that detail."
        )
    for request, runs in results.items():
        top, _ = consistency(run.sequence for run in runs)
        if len(top) == 1:
            lines.append(
                f"{request.kind}: the top sequence is a single step ({top[0]}), "
                "so its agreement is weak evidence."
            )
        others = sum(run.sequence.count("other") for run in runs)
        if others:
            lines.append(f"{request.kind}: {others} step(s) across the runs are `other`.")
        notes = sorted({run.note for run in runs if run.kind == "plan" and run.note})
        lines += [f"{request.kind}: {note}." for note in notes]
    return lines


def render_report(
    base_url: str, repeats: int, results: dict[PlanRequest, list[RunRecord]], today: str
) -> str:
    """A markdown report: a per-request table, then every plan beside its raw titles."""
    out = [
        "# NEMAR plan consistency (E2.5, #663)",
        "",
        f"Deployment: `{base_url}`; date: {today}; repeats per request: {repeats}; "
        f"pass: top sequence in at least {needed(repeats)} of {repeats} repeats "
        f"({PASS_NUMERATOR} of {PASS_DENOMINATOR}, rounded up).",
        "",
        "| Request | Dataset | Sequences (count) | Top share | Pass |",
        "|---|---|---|---|---|",
    ]
    for request, runs in results.items():
        counts = Counter(run.sequence for run in runs)
        _, top = consistency(run.sequence for run in runs)
        distinct = "<br>".join(f"{_cell(_seq(seq))} (x{n})" for seq, n in counts.most_common())
        verdict = "pass" if passes(top, repeats) else "FAIL"
        out.append(
            f"| {_cell(request.kind)} | {request.dataset_id} | {distinct} | {top}/{repeats} | {verdict} |"
        )
    out += ["", "## Caveats", ""]
    out += [f"- {line}" for line in caveats(results)]
    out += ["", "## Plans, run by run", ""]
    for request, runs in results.items():
        out += [f"### {request.kind} ({request.dataset_id})", "", f"> {request.message}", ""]
        out += [
            "| Run | Outcome | Step | Operation | Decided by | Raw title |",
            "|---|---|---|---|---|---|",
        ]
        for i, run in enumerate(runs, start=1):
            if not run.steps:
                out.append(
                    f"| {i} | {run.kind} | - | {_cell(_seq(run.sequence))} | - | {_cell(run.note)} |"
                )
                continue
            for n, (step, op) in enumerate(zip(run.steps, run.sequence, strict=True), start=1):
                basis = step_basis(step)[1]
                out.append(f"| {i} | plan | {n} | {op} | {basis} | {_title_cell(step, basis)} |")
        out.append("")
    out += [
        "## Normalization rules",
        "",
        "First match wins, top to bottom; title first, then detail.",
        "",
    ]
    out += ["| Priority | Operation | Pattern |", "|---|---|---|"]
    out += [f"| {i} | {op} | `{_cell(pat)}` |" for i, (op, pat) in enumerate(RULES, start=1)]
    out += [f"| {len(RULES) + 1} | other | (nothing matched) |", ""]
    return "\n".join(out)


def raw_payload(
    base_url: str, today: str, results: dict[PlanRequest, list[RunRecord]]
) -> dict[str, Any]:
    """What ``--raw-out`` saves: the runs, plus the requests they answered."""
    return {
        "base_url": base_url,
        "date": today,
        "requests": {r.key: {"message": r.message, "dataset_id": r.dataset_id} for r in results},
        "runs": {r.key: [asdict(run) for run in runs] for r, runs in results.items()},
    }


def save_raw(
    path: Path, base_url: str, today: str, results: dict[PlanRequest, list[RunRecord]]
) -> None:
    """Write the runs so far to ``path``; called after every repeat, so a crash keeps them."""
    path.write_text(json.dumps(raw_payload(base_url, today, results), indent=2))


def records_from_raw(raw: dict[str, Any]) -> dict[PlanRequest, list[RunRecord]]:
    """Rebuild the runs a ``--raw-out`` file saved, normalizing the steps with today's rules.

    Raises ValueError when a request is missing from the file, when the file's request
    text or dataset id differs from ``REQUESTS`` (the plans would be shown beside a
    request they did not answer), or when the requests have different repeat counts. A
    file from before the requests were saved has no ``requests`` key and is not checked.
    """
    saved_requests = raw.get("requests", {})
    results: dict[PlanRequest, list[RunRecord]] = {}
    for request in REQUESTS:
        if request.key not in raw["runs"]:
            raise ValueError(f"the raw file has no runs for request {request.key!r}")
        saved = saved_requests.get(request.key)
        if saved and saved != {"message": request.message, "dataset_id": request.dataset_id}:
            raise ValueError(
                f"request {request.key!r} in the raw file differs from REQUESTS "
                f"(saved: {saved}); the plans answered a different request"
            )
        runs: list[RunRecord] = []
        for entry in raw["runs"][request.key]:
            run = RunRecord(**{**entry, "sequence": tuple(entry["sequence"])})
            if run.kind == "plan":
                run.sequence = normalize_plan(run.steps)
            runs.append(run)
        results[request] = runs
    if len({len(runs) for runs in results.values()}) > 1:
        raise ValueError("the requests in the raw file have different numbers of repeats")
    return results


# --- Entry point -------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="API base (default: %(default)s)"
    )
    parser.add_argument("--repeats", type=int, default=REPEATS, help="repeats per request")
    parser.add_argument("--out", type=Path, help="write the markdown report here (default: stdout)")
    parser.add_argument(
        "--raw-out",
        type=Path,
        help="write every run, with step details, as JSON after each repeat "
        "(default for a live run: a file in the temp directory)",
    )
    parser.add_argument(
        "--origin", default=DEFAULT_ORIGIN, help="Origin header (default: %(default)s)"
    )
    parser.add_argument(
        "--search-query",
        default=DEFAULT_SEARCH_QUERY,
        help="query for the dataset check (default: %(default)r)",
    )
    parser.add_argument(
        "--from-raw",
        type=Path,
        help="no network: re-render the report from a --raw-out file with the current rules",
    )
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    if args.from_raw:
        raw = json.loads(args.from_raw.read_text())
        base_url, today = raw["base_url"], raw["date"]
        try:
            results = records_from_raw(raw)
        except ValueError as exc:
            raise SystemExit(f"abort: {exc}") from exc
    else:
        base_url, today = args.base_url, date.today().isoformat()
        confirm_datasets(base_url, REQUESTS, args.search_query, args.origin)
        if args.raw_out is None:
            args.raw_out = Path(tempfile.gettempdir()) / f"plan-consistency-{today}.raw.json"
        print(f"raw runs saved to {args.raw_out} after each repeat", file=sys.stderr)
        results = {}
        for request in REQUESTS:
            runs: list[RunRecord] = []
            results[request] = runs
            for i in range(1, args.repeats + 1):
                run = run_once(base_url, request, args.origin)
                runs.append(run)
                if args.raw_out:
                    save_raw(args.raw_out, base_url, today, results)
                print(
                    f"{request.key} {i}/{args.repeats}: {_seq(run.sequence)} {run.note}",
                    file=sys.stderr,
                )
    repeats = len(next(iter(results.values())))

    report = render_report(base_url, repeats, results, today)
    if args.out:
        args.out.write_text(report + "\n")
        print(f"report written to {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
