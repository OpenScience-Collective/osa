"""Integration tests for complete docstring workflow."""

import pytest

from src.knowledge.db import get_db_path, get_stats, init_db
from src.knowledge.search import get_full_docstring, search_docstrings
from src.tools.knowledge import (
    create_get_full_docstring_tool,
    create_search_docstrings_tool,
)

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
    """The search tool should hint at the follow-up tool name in its output."""
    _insert_eeg_context(clean_db)

    tool = create_search_docstrings_tool(clean_db, "EEGLAB", language="matlab")
    result = tool.invoke({"query": "eeg_context", "limit": 1})

    assert f"get_{clean_db}_full_docstring" in result, (
        "Search tool output should mention the full-docstring follow-up tool name"
    )
