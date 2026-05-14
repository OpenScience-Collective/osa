"""Integration tests for complete docstring workflow."""

from pathlib import Path

import pytest

from src.knowledge.db import get_db_path, get_stats, init_db
from src.knowledge.search import get_full_docstring, search_docstrings
from src.tools.knowledge import (
    create_get_full_docstring_tool,
    create_search_docstrings_tool,
)

# Verbatim production docstring for eeg_context (6598 chars, pulled from the
# live eeglab.db on osa-dev). Used by the issue #276 regression tests to
# pin behavior against the real artifact rather than an abbreviated copy.
EEG_CONTEXT_REAL_DOCSTRING = (
    Path(__file__).parent.parent / "fixtures" / "eeg_context_docstring.txt"
).read_text()

EEG_CONTEXT_DOCSTRING = """EEG_CONTEXT - returns (in output 'delays') a matrix giving, for each event of specified
                ("target") type(s), the latency (in ms) to the Nth preceding and/or following
                urevents (if any) of specified ("neighbor") type(s). Return the target event
                and urevent numbers, the neighbor urevent numbers, and the values of specified
                urevent field(s) for each of the neighbor urevents.
Usage:
            >>  [targs,urnbrs,urnbrtypes,delays,tfields,urnfields] = ...
                         eeg_context(EEG,{targets},{neighbors},[positions],{fields},alltargs);
Required input:
EEG         - EEGLAB dataset structure containing EEG.event and EEG.urevent sub-structures

Optional inputs:
targets     - string or cell array of strings naming event type(s) of the specified target
              events {default | []: all events}
neighbors   - string or cell array of strings naming event type(s) of the specified
              neighboring urevents {default | []: any neighboring events}.
[positions] - int vector giving the relative positions of 'neighbor' type urevents to return.
fields      - string or cell array of strings naming one or more (ur)event field(s) to return
              values for neighbor urevents. {default: no field info returned}
alltargs    - string ('all'|[]) if 'all', return information about all target urevents,
              even those on which no epoch in the current dataset is centered.
Outputs:
 targs      - size(ntargets,4) matrix giving the indices of target events in the event
              structure in column 1 and in the urevent structure in column 2.
 urnbrs     - matrix of indices of "neighbor" events in the URevent structure (NaN if none).
 urnbrtypes - int array giving the urnbrs event type indices in the {neighbor} cell array.
 delays     - matrix giving, for each {targets} type event, the latency of the delay (in ms).
 tfields    - real or cell array of values of the requested (ur)event field(s) for the target.
 urnfields  - real or cell array of values of the requested (ur)event field(s) for the neighbor.
"""


@pytest.fixture
def test_project():
    """Provide a test project name."""
    return "test-integration-docstrings"


@pytest.fixture
def clean_db(test_project):
    """Ensure clean database for each test."""
    db_path = get_db_path(test_project)
    if db_path.exists():
        db_path.unlink()
    init_db(test_project)
    yield test_project
    # Cleanup after test
    if db_path.exists():
        db_path.unlink()


def test_search_empty_database(clean_db):
    """Test searching with empty database returns no results."""
    results = search_docstrings("test query", project=clean_db, limit=5)
    assert len(results) == 0


def test_search_with_data(clean_db):
    """Test searching docstrings after inserting data."""
    from src.knowledge.db import get_connection, upsert_docstring

    # Insert test docstrings
    with get_connection(clean_db) as conn:
        upsert_docstring(
            conn,
            repo="test/repo",
            file_path="test_func.m",
            language="matlab",
            symbol_name="test_function",
            symbol_type="function",
            docstring="This function tests the loadset functionality for EEG data",
            line_number=1,
        )
        upsert_docstring(
            conn,
            repo="test/repo",
            file_path="helper.py",
            language="python",
            symbol_name="process_data",
            symbol_type="function",
            docstring="Process raw data and return cleaned results",
            line_number=10,
        )
        conn.commit()

    # Search for matlab function (simple single-word query)
    results = search_docstrings("loadset", project=clean_db, limit=5, language="matlab")
    assert len(results) == 1, f"Expected 1 result, got {len(results)}"
    assert results[0].title == "test_function (function) - test_func.m"
    assert "loadset" in results[0].snippet.lower()

    # Search for python function (simple query)
    results = search_docstrings("process", project=clean_db, limit=5, language="python")
    assert len(results) == 1
    assert "process_data" in results[0].title

    # Search without language filter (should find both)
    results = search_docstrings("data", project=clean_db, limit=5)
    assert len(results) == 2


def test_tool_with_empty_db(clean_db):
    """Test tool returns helpful message when database is empty."""
    tool = create_search_docstrings_tool(clean_db, "Test Community", language="matlab")

    # Search in empty database
    result = tool.invoke({"query": "test", "limit": 5})

    assert isinstance(result, str)
    assert "No code documentation found" in result


def test_tool_with_data(clean_db):
    """Test tool returns formatted results."""
    from src.knowledge.db import get_connection, upsert_docstring

    # Insert test docstring
    with get_connection(clean_db) as conn:
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/popfunc/pop_loadset.m",
            language="matlab",
            symbol_name="pop_loadset",
            symbol_type="function",
            docstring="pop_loadset() - load an EEG dataset",
            line_number=5,
        )
        conn.commit()

    # Create and invoke tool
    tool = create_search_docstrings_tool(clean_db, "EEGLAB", language="matlab")
    result = tool.invoke({"query": "loadset", "limit": 5})

    # Verify formatted output
    assert isinstance(result, str)
    assert "pop_loadset" in result
    assert "View source on GitHub" in result
    assert "github.com" in result


def test_database_stats_after_insert(clean_db):
    """Test that database stats reflect inserted docstrings."""
    from src.knowledge.db import get_connection, upsert_docstring

    # Initially empty
    stats = get_stats(clean_db)
    assert stats["docstrings_total"] == 0
    assert stats["docstrings_matlab"] == 0
    assert stats["docstrings_python"] == 0

    # Insert docstrings
    with get_connection(clean_db) as conn:
        upsert_docstring(
            conn,
            repo="test/repo",
            file_path="test.m",
            language="matlab",
            symbol_name="test1",
            symbol_type="function",
            docstring="Test docstring",
            line_number=1,
        )
        upsert_docstring(
            conn,
            repo="test/repo",
            file_path="test.py",
            language="python",
            symbol_name="test2",
            symbol_type="function",
            docstring="Test docstring",
            line_number=1,
        )
        conn.commit()

    # Check updated stats
    stats = get_stats(clean_db)
    assert stats["docstrings_total"] == 2
    assert stats["docstrings_matlab"] == 1
    assert stats["docstrings_python"] == 1


def test_upsert_updates_existing(clean_db):
    """Test that upserting the same docstring updates it."""
    from src.knowledge.db import get_connection, upsert_docstring

    with get_connection(clean_db) as conn:
        # Insert first version
        upsert_docstring(
            conn,
            repo="test/repo",
            file_path="test.m",
            language="matlab",
            symbol_name="my_func",
            symbol_type="function",
            docstring="Original docstring",
            line_number=1,
        )
        conn.commit()

        # Update with new docstring
        upsert_docstring(
            conn,
            repo="test/repo",
            file_path="test.m",
            language="matlab",
            symbol_name="my_func",
            symbol_type="function",
            docstring="Updated docstring",
            line_number=1,
        )
        conn.commit()

    # Should only have one entry with updated content
    results = search_docstrings("docstring", project=clean_db)
    assert len(results) == 1
    assert "Updated" in results[0].snippet


def test_docstring_size_limit(clean_db):
    """Test that docstrings are truncated if too large."""
    from src.knowledge.db import get_connection, upsert_docstring

    # Create a very large docstring
    large_doc = "x" * 15000

    with get_connection(clean_db) as conn:
        upsert_docstring(
            conn,
            repo="test/repo",
            file_path="test.m",
            language="matlab",
            symbol_name="big_func",
            symbol_type="function",
            docstring=large_doc,
            line_number=1,
        )
        conn.commit()

        # Check that it was truncated
        row = conn.execute(
            "SELECT docstring FROM docstrings WHERE symbol_name='big_func'"
        ).fetchone()
        assert row is not None
        assert len(row["docstring"]) <= 10000


def test_branch_in_github_url(clean_db):
    """Test that docstrings store and use correct branch in GitHub URLs."""
    from src.knowledge.db import get_connection, upsert_docstring

    with get_connection(clean_db) as conn:
        # Insert docstring with specific branch
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/test.m",
            language="matlab",
            symbol_name="test_func",
            symbol_type="function",
            docstring="Test function",
            line_number=42,
            branch="develop",  # EEGLAB uses 'develop' not 'main'
        )
        conn.commit()

    # Search and verify URL uses correct branch
    results = search_docstrings("test", project=clean_db)
    assert len(results) == 1
    assert "/blob/develop/" in results[0].url, (
        f"Expected /blob/develop/ in URL, got: {results[0].url}"
    )
    assert "#L42" in results[0].url  # Line number should be included


def test_exact_symbol_match_ranks_above_wrappers(clean_db):
    """Test that exact symbol_name matches rank above wrapper functions.

    Reproduces issue #141: the standalone erpimage() function was buried
    at rank 10 behind pop_erpimage, std_erpimage, etc. because its large
    docstring diluted BM25 term frequency scores. FTS5 bm25() column
    weights should boost symbol_name matches to fix this.
    """
    from src.knowledge.db import get_connection, upsert_docstring

    with get_connection(clean_db) as conn:
        # Wrapper with short docstring (BM25 would rank this higher)
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/popfunc/pop_erpimage.m",
            language="matlab",
            symbol_name="pop_erpimage",
            symbol_type="function",
            docstring="pop_erpimage() - GUI wrapper for erpimage. Calls erpimage internally.",
            line_number=1,
        )
        # Another wrapper
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/studyfunc/std_erpimage.m",
            language="matlab",
            symbol_name="std_erpimage",
            symbol_type="function",
            docstring="std_erpimage() - STUDY wrapper for erpimage computations.",
            line_number=1,
        )
        # Core function with large docstring (BM25 would rank this lower)
        large_docstring = (
            "erpimage() - Plot an event-related image of EEG data. "
            + "Parameters: data - input EEG data matrix. " * 200
        )
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/sigprocfunc/erpimage.m",
            language="matlab",
            symbol_name="erpimage",
            symbol_type="function",
            docstring=large_docstring,
            line_number=1,
        )
        conn.commit()

    results = search_docstrings("erpimage", project=clean_db, limit=3)
    assert len(results) == 3
    # The exact symbol_name match should be first
    assert results[0].title == "erpimage (function) - functions/sigprocfunc/erpimage.m", (
        f"Expected exact match 'erpimage' first, got: {results[0].title}"
    )


def test_branch_fallback_for_null(clean_db):
    """Test that NULL branch values fallback to 'main' in URLs."""
    from src.knowledge.db import get_connection

    with get_connection(clean_db) as conn:
        # Manually insert without specifying branch (uses DEFAULT 'main')
        conn.execute(
            """
            INSERT INTO docstrings (repo, file_path, language, symbol_name,
                                   symbol_type, docstring, line_number, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                "test/repo",
                "old_file.m",
                "matlab",
                "old_func",
                "function",
                "Old docstring from before branch tracking",
                10,
            ),
        )
        conn.commit()

    # Search and verify URL falls back to 'main'
    results = search_docstrings("old", project=clean_db)
    assert len(results) == 1
    assert "/blob/main/" in results[0].url, (
        f"Expected fallback to /blob/main/, got: {results[0].url}"
    )


def _insert_eeg_context(project: str) -> None:
    """Insert the eeg_context docstring used by the issue #276 regression tests."""
    from src.knowledge.db import get_connection, upsert_docstring

    with get_connection(project) as conn:
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/popfunc/eeg_context.m",
            language="matlab",
            symbol_name="eeg_context",
            symbol_type="function",
            docstring=EEG_CONTEXT_DOCSTRING,
            line_number=1,
            branch="develop",
        )
        conn.commit()


def test_search_snippet_includes_outputs_section(clean_db):
    """Regression for #276: snippet must include the Outputs section.

    Before the fix the snippet was capped at 200 chars, so the LLM never
    saw output names past the first sentence. The cap is now 1500 chars,
    which captures Usage/Parameters/Outputs for typical MATLAB docstrings.
    """
    _insert_eeg_context(clean_db)

    results = search_docstrings("eeg_context", project=clean_db, limit=1)
    assert len(results) == 1
    snippet = results[0].snippet

    # Outputs section must be present
    assert "Outputs:" in snippet, (
        f"Snippet missing Outputs section (len={len(snippet)}): {snippet[:300]}..."
    )

    # All six documented outputs must appear in the snippet
    for output_name in ("targs", "urnbrs", "urnbrtypes", "delays", "tfields", "urnfields"):
        assert output_name in snippet, (
            f"Output '{output_name}' missing from snippet (len={len(snippet)})"
        )


def test_get_full_docstring_returns_complete_content(clean_db):
    """get_full_docstring must return the entire stored docstring (no truncation)."""
    _insert_eeg_context(clean_db)

    results = get_full_docstring("eeg_context", project=clean_db)
    assert len(results) == 1
    full = results[0].snippet

    # The returned snippet must equal the stored docstring byte-for-byte
    assert full == EEG_CONTEXT_DOCSTRING, (
        f"Expected full docstring ({len(EEG_CONTEXT_DOCSTRING)} chars), got {len(full)} chars"
    )


def test_get_full_docstring_is_case_insensitive(clean_db):
    """Exact symbol lookup should ignore case to match what an LLM might pass."""
    _insert_eeg_context(clean_db)

    for query in ("eeg_context", "EEG_CONTEXT", "Eeg_Context"):
        results = get_full_docstring(query, project=clean_db)
        assert len(results) == 1, f"Failed for query={query!r}"


def test_get_full_docstring_no_match(clean_db):
    """Unknown symbols return empty list, not an error."""
    _insert_eeg_context(clean_db)
    assert get_full_docstring("does_not_exist", project=clean_db) == []


def test_full_docstring_tool_returns_outputs(clean_db):
    """End-to-end: the LLM-facing tool must surface all 6 outputs of eeg_context."""
    _insert_eeg_context(clean_db)

    tool = create_get_full_docstring_tool(clean_db, "EEGLAB", language="matlab")
    result = tool.invoke({"symbol_name": "eeg_context"})

    assert isinstance(result, str)
    assert "View source on GitHub" in result
    for output_name in ("targs", "urnbrs", "urnbrtypes", "delays", "tfields", "urnfields"):
        assert output_name in result, f"Output '{output_name}' missing from tool output"


def test_full_docstring_tool_unknown_symbol(clean_db):
    """Tool must return a helpful message (not crash) for unknown symbols."""
    _insert_eeg_context(clean_db)

    tool = create_get_full_docstring_tool(clean_db, "EEGLAB", language="matlab")
    result = tool.invoke({"symbol_name": "nope_not_real"})

    assert isinstance(result, str)
    assert "No docstring found" in result


def test_search_tool_points_to_full_docstring_tool(clean_db):
    """The search tool should hint at the follow-up tool name when truncation occurred."""
    _insert_eeg_context(clean_db)

    tool = create_search_docstrings_tool(clean_db, "EEGLAB", language="matlab")
    result = tool.invoke({"query": "eeg_context", "limit": 1})

    # eeg_context (~1900 chars) exceeds the 1500-char snippet cap, so the
    # hint must be appended.
    assert f"get_{clean_db}_full_docstring" in result, (
        "Search tool output should mention the full-docstring follow-up tool name"
    )


def test_snippet_cap_truncates_past_boundary(clean_db):
    """Regression for #276 reviewer feedback: pin the 1500-char snippet cap.

    Constructs a docstring whose marker content sits past char 1500.
    Asserts the snippet does NOT contain it (truncation occurred at the
    cap) and that get_full_docstring DOES return it. Without this test
    a future revert of DOCSTRING_SNIPPET_MAX_LENGTH could silently
    reintroduce the original bug.
    """
    from src.knowledge.db import get_connection, upsert_docstring

    # 1600 chars of padding so the marker is past the 1500-char cap.
    padding = "lorem ipsum dolor sit amet, " * 60  # ~28 chars * 60 = ~1680
    assert len(padding) > 1500
    marker = "ZZZ_PAST_CAP_MARKER_ZZZ"
    docstring = padding + "\nOutputs:\n  " + marker

    with get_connection(clean_db) as conn:
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/test_cap.m",
            language="matlab",
            symbol_name="cap_boundary_fn",
            symbol_type="function",
            docstring=docstring,
            line_number=1,
        )
        conn.commit()

    # Snippet truncates before the marker
    results = search_docstrings("cap_boundary_fn", project=clean_db, limit=1)
    assert len(results) == 1
    snippet = results[0].snippet
    assert len(snippet) <= 1500 + 3  # cap + "..."
    assert snippet.endswith("..."), "Snippet should be marked as truncated"
    assert marker not in snippet, "Snippet must not contain content past the cap"

    # Full fetch returns it
    full_results = get_full_docstring("cap_boundary_fn", project=clean_db)
    assert len(full_results) == 1
    assert marker in full_results[0].snippet, "Full fetch must return content past cap"


def test_get_full_docstring_repo_filter_disambiguates(clean_db):
    """repo filter must narrow multi-repo matches to one."""
    from src.knowledge.db import get_connection, upsert_docstring

    with get_connection(clean_db) as conn:
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/plot.m",
            language="matlab",
            symbol_name="plot",
            symbol_type="function",
            docstring="EEGLAB plot helper",
            line_number=1,
        )
        upsert_docstring(
            conn,
            repo="fieldtrip/fieldtrip",
            file_path="utilities/plot.m",
            language="matlab",
            symbol_name="plot",
            symbol_type="function",
            docstring="FieldTrip plot helper",
            line_number=1,
        )
        conn.commit()

    # No filter: both rows returned (and ordered deterministically)
    both = get_full_docstring("plot", project=clean_db)
    assert len(both) == 2
    repos = [r.url.split("github.com/")[1].split("/blob/")[0] for r in both]
    assert repos == sorted(repos), "ORDER BY repo must give deterministic order"

    # Filter narrows to the chosen repo
    filtered = get_full_docstring("plot", project=clean_db, repo="sccn/eeglab")
    assert len(filtered) == 1
    assert "sccn/eeglab" in filtered[0].url


def test_get_full_docstring_language_filter(clean_db):
    """language filter must narrow to the chosen language."""
    from src.knowledge.db import get_connection, upsert_docstring

    with get_connection(clean_db) as conn:
        upsert_docstring(
            conn,
            repo="org/repo",
            file_path="lib/load.m",
            language="matlab",
            symbol_name="load_data",
            symbol_type="function",
            docstring="MATLAB load_data",
            line_number=1,
        )
        upsert_docstring(
            conn,
            repo="org/repo",
            file_path="lib/load.py",
            language="python",
            symbol_name="load_data",
            symbol_type="function",
            docstring="Python load_data",
            line_number=1,
        )
        conn.commit()

    matlab_only = get_full_docstring("load_data", project=clean_db, language="matlab")
    assert len(matlab_only) == 1
    assert "MATLAB load_data" in matlab_only[0].snippet

    python_only = get_full_docstring("load_data", project=clean_db, language="python")
    assert len(python_only) == 1
    assert "Python load_data" in python_only[0].snippet


def test_get_full_docstring_respects_limit(clean_db):
    """Default limit must cap unbounded payloads for common symbol names."""
    from src.knowledge.db import get_connection, upsert_docstring

    with get_connection(clean_db) as conn:
        for i in range(8):
            upsert_docstring(
                conn,
                repo=f"org/repo{i}",
                file_path=f"file_{i}.m",
                language="matlab",
                symbol_name="init",
                symbol_type="function",
                docstring=f"init implementation {i}",
                line_number=1,
            )
        conn.commit()

    results = get_full_docstring("init", project=clean_db)  # default limit
    assert len(results) == 5, f"Expected default limit of 5, got {len(results)}"

    custom = get_full_docstring("init", project=clean_db, limit=2)
    assert len(custom) == 2


def test_get_full_docstring_skips_empty_stored_docstring(clean_db):
    """An empty stored docstring is logged and skipped, not returned as a phantom hit."""
    from src.knowledge.db import get_connection

    with get_connection(clean_db) as conn:
        # Insert with empty docstring directly (upsert_docstring requires a str
        # but doesn't reject empty)
        conn.execute(
            """
            INSERT INTO docstrings (repo, file_path, language, symbol_name,
                                   symbol_type, docstring, line_number, branch, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("org/repo", "f.m", "matlab", "empty_fn", "function", "", 1, "main"),
        )
        conn.commit()

    results = get_full_docstring("empty_fn", project=clean_db)
    assert results == [], "Empty-bodied rows must be skipped, not returned"


def test_full_docstring_tool_handles_symbol_with_braces(clean_db):
    """Regression: tool's no-match message must not crash on `{` in symbol names."""
    _insert_eeg_context(clean_db)

    tool = create_get_full_docstring_tool(clean_db, "EEGLAB", language="matlab")
    # Earlier code used .format() on an f-string that contained the user's
    # input verbatim; a `{` in the symbol name caused KeyError.
    result = tool.invoke({"symbol_name": "no_{such}_symbol"})
    assert isinstance(result, str)
    assert "No docstring found" in result


def test_search_tool_omits_hint_when_no_truncation(clean_db):
    """Search results that fit within the snippet cap should not nudge full-fetch."""
    from src.knowledge.db import get_connection, upsert_docstring

    with get_connection(clean_db) as conn:
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/short.m",
            language="matlab",
            symbol_name="short_fn",
            symbol_type="function",
            docstring="Tiny docstring that easily fits inside the snippet cap.",
            line_number=1,
        )
        conn.commit()

    tool = create_search_docstrings_tool(clean_db, "EEGLAB", language="matlab")
    result = tool.invoke({"query": "short_fn", "limit": 1})

    assert f"get_{clean_db}_full_docstring" not in result, (
        "Hint should be omitted when no snippet was truncated"
    )


# ---------------------------------------------------------------------------
# Regression tests for issue #276 using the VERBATIM production eeg_context
# docstring. These exist alongside the synthetic tests above because the real
# artifact has section positions that exercise both code paths:
#   - Usage signature with all 6 output names: position 670 (in snippet)
#   - "Outputs:" detail section: position 2041 (past 1500-char snippet cap)
# This means the user's question "what are the outputs?" must be answerable
# from the SNIPPET (via the Usage signature), while "what does each output
# do?" requires a FULL-FETCH follow-up.
# ---------------------------------------------------------------------------

EEG_CONTEXT_OUTPUT_NAMES = (
    "targs",
    "urnbrs",
    "urnbrtypes",
    "delays",
    "tfields",
    "urnfields",
)


def _insert_real_eeg_context(project: str) -> None:
    """Insert the verbatim production eeg_context docstring."""
    from src.knowledge.db import get_connection, upsert_docstring

    with get_connection(project) as conn:
        upsert_docstring(
            conn,
            repo="sccn/eeglab",
            file_path="functions/popfunc/eeg_context.m",
            language="matlab",
            symbol_name="eeg_context",
            symbol_type="function",
            docstring=EEG_CONTEXT_REAL_DOCSTRING,
            line_number=1,
            branch="develop",
        )
        conn.commit()


def test_real_eeg_context_snippet_names_all_outputs(clean_db):
    """The user's "what are the outputs of eeg_context?" must be answerable
    from the search snippet alone, against the REAL production docstring.

    The Outputs: detail section sits past char 1500 in the real docstring,
    but the Usage signature `[targs,urnbrs,urnbrtypes,delays,tfields,urnfields]`
    is at position 670 and falls inside the snippet. This is the exact bug
    from #276 — proving the fix works on the actual artifact, not just our
    synthetic abbreviation.
    """
    _insert_real_eeg_context(clean_db)

    results = search_docstrings("eeg_context", project=clean_db, limit=1)
    assert len(results) == 1
    snippet = results[0].snippet

    # The Usage signature names all 6 outputs; verify all are present in the snippet
    for name in EEG_CONTEXT_OUTPUT_NAMES:
        assert name in snippet, (
            f"Output '{name}' missing from snippet of real eeg_context docstring "
            f"(snippet len={len(snippet)})"
        )

    # The snippet should be marked truncated (real docstring is 6598 chars > 1500)
    assert snippet.endswith("..."), (
        "Real eeg_context snippet should be marked truncated; "
        "otherwise the LLM won't know to ask for full content"
    )


def test_real_eeg_context_search_tool_hints_full_fetch(clean_db):
    """The search tool must instruct the LLM to follow up with the full-fetch
    tool, since the Outputs: detail section sits past the snippet cap."""
    _insert_real_eeg_context(clean_db)

    tool = create_search_docstrings_tool(clean_db, "EEGLAB", language="matlab")
    result = tool.invoke({"query": "eeg_context", "limit": 1})

    assert f"get_{clean_db}_full_docstring" in result, (
        "Search tool output for the real eeg_context docstring must hint at "
        "the full-fetch tool so the LLM can answer detailed output questions"
    )


def test_real_eeg_context_full_fetch_exposes_outputs_section(clean_db):
    """Detailed "what does each output do?" answers require the Outputs: section,
    which the full-fetch tool must surface for the real eeg_context docstring."""
    _insert_real_eeg_context(clean_db)

    tool = create_get_full_docstring_tool(clean_db, "EEGLAB", language="matlab")
    result = tool.invoke({"symbol_name": "eeg_context"})

    # The Outputs: header must be present
    assert "Outputs:" in result, "Outputs: section must appear in full-fetch output"

    # Each output should appear AT LEAST TWICE in the result (once in the
    # Usage signature, once in its own description in Outputs:). This is
    # the signal that the detailed descriptions reached the LLM.
    for name in EEG_CONTEXT_OUTPUT_NAMES:
        occurrences = result.count(name)
        assert occurrences >= 2, (
            f"Output '{name}' should appear at least twice (Usage + Outputs: "
            f"sections) in full-fetch output; found {occurrences}"
        )

    # Spot-check that distinctive Outputs:-section prose made it through.
    # This text comes from the per-output descriptions, past char 1500 in
    # the real docstring.
    assert "size(ntargets,4) matrix" in result, (
        "Outputs:-section detail about `targs` matrix shape is missing; "
        "the full-fetch tool may not be returning content past char 1500"
    )


def test_real_eeg_context_full_fetch_returns_complete_docstring(clean_db):
    """Full-fetch must return the entire 6598-char production docstring
    byte-for-byte (up to the 10K storage cap, which is well above this size)."""
    _insert_real_eeg_context(clean_db)

    results = get_full_docstring("eeg_context", project=clean_db)
    assert len(results) == 1
    assert results[0].snippet == EEG_CONTEXT_REAL_DOCSTRING, (
        f"Full-fetch should return the verbatim production docstring "
        f"({len(EEG_CONTEXT_REAL_DOCSTRING)} chars); got {len(results[0].snippet)} chars"
    )
