"""Integration tests for complete docstring workflow."""

import pytest

from src.knowledge.db import get_db_path, get_stats, init_db
from src.knowledge.search import search_docstrings
from src.tools.knowledge import create_search_docstrings_tool


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
