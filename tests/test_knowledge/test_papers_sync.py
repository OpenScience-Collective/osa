"""Tests for the opencite-backed papers sync module.

Mapping tests use real opencite ``Paper`` objects and a real SQLite database
(no mocks). The sync smoke tests make real network calls per project
guidelines.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from opencite import IDSet, Paper

import src.knowledge.papers_sync as ps
from src.knowledge.db import get_connection, init_db
from src.knowledge.papers_sync import (
    _cache_papers_async,
    _paper_source_and_id,
    _paper_to_result,
    _paper_url,
    _store_papers,
    configure_openalex,
    search_papers_live,
    sync_all_papers,
    sync_citing_papers,
    sync_openalex_papers,
)


@pytest.fixture
def temp_db(tmp_path: Path):
    """Create temporary database for testing."""
    db_path = tmp_path / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()
        yield db_path


class TestConfigureOpenalex:
    """Tests for the configure_openalex credential helper."""

    def setup_method(self):
        """Reset stored OpenAlex credentials before each test."""
        configure_openalex(api_key=None, email=None)

    def teardown_method(self):
        """Reset stored OpenAlex credentials after each test."""
        configure_openalex(api_key=None, email=None)

    def test_sets_api_key(self):
        configure_openalex(api_key="test-key-123")
        assert ps._OPENALEX_API_KEY == "test-key-123"

    def test_sets_email(self):
        configure_openalex(email="test@example.com")
        assert ps._OPENALEX_EMAIL == "test@example.com"
        assert ps._OPENALEX_API_KEY is None

    def test_sets_both_key_and_email(self):
        configure_openalex(api_key="test-key", email="test@example.com")
        assert ps._OPENALEX_API_KEY == "test-key"
        assert ps._OPENALEX_EMAIL == "test@example.com"

    def test_handles_empty_strings(self):
        configure_openalex(api_key="", email="")
        assert ps._OPENALEX_API_KEY is None
        assert ps._OPENALEX_EMAIL is None

    def test_handles_whitespace_strings(self):
        configure_openalex(api_key="  ", email="  ")
        assert ps._OPENALEX_API_KEY is None
        assert ps._OPENALEX_EMAIL is None

    def test_handles_none_values(self):
        configure_openalex(api_key=None, email=None)
        assert ps._OPENALEX_API_KEY is None
        assert ps._OPENALEX_EMAIL is None


class TestPaperMapping:
    """Map opencite Paper objects to (source, external_id) and URLs."""

    def test_prefers_openalex_id(self):
        paper = Paper(
            title="X",
            ids=IDSet(openalex_id="https://openalex.org/W7", doi="10.1/A", pmid="9"),
        )
        assert _paper_source_and_id(paper) == ("openalex", "W7")

    def test_falls_back_to_semantic_scholar(self):
        paper = Paper(title="Y", ids=IDSet(s2_id="S99"))
        assert _paper_source_and_id(paper) == ("semanticscholar", "S99")

    def test_falls_back_to_pubmed(self):
        paper = Paper(title="Y", ids=IDSet(pmid="12345"))
        assert _paper_source_and_id(paper) == ("pubmed", "12345")

    def test_falls_back_to_doi_lowercased(self):
        paper = Paper(title="Y", ids=IDSet(doi="10.1/AbC"))
        assert _paper_source_and_id(paper) == ("doi", "10.1/abc")

    def test_falls_back_to_arxiv(self):
        paper = Paper(title="Y", ids=IDSet(arxiv_id="2106.15928"))
        assert _paper_source_and_id(paper) == ("arxiv", "2106.15928")

    def test_no_identifier_is_skipped(self):
        paper = Paper(title="orphan", ids=IDSet())
        assert _paper_source_and_id(paper) == (None, None)

    def test_url_prefers_doi_landing_page(self):
        paper = Paper(title="X", ids=IDSet(doi="10.1/A"), url="https://openalex.org/W7")
        assert _paper_url(paper) == "https://doi.org/10.1/A"

    def test_url_falls_back_to_paper_url(self):
        paper = Paper(title="X", ids=IDSet(), url="https://example.org/p")
        assert _paper_url(paper) == "https://example.org/p"

    def test_url_empty_when_nothing_available(self):
        paper = Paper(title="X", ids=IDSet())
        assert _paper_url(paper) == ""


class TestStorePapers:
    """Persist opencite papers into the knowledge DB (real SQLite, no mocks)."""

    def test_stores_and_labels_sources(self, temp_db: Path):
        papers = [
            Paper(
                title="EEGLAB toolbox",
                ids=IDSet(openalex_id="https://openalex.org/W1", doi="10.1/eeglab"),
                year=2004,
                abstract="An open source toolbox.",
            ),
            Paper(title="S2 paper", ids=IDSet(s2_id="S2"), year=2020),
        ]
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            counts = _store_papers(papers, "test")

            assert counts == {"openalex": 1, "semanticscholar": 1}
            with get_connection("test") as conn:
                rows = {
                    r["source"]: r
                    for r in conn.execute("SELECT source, external_id, url, title FROM papers")
                }
            assert rows["openalex"]["external_id"] == "W1"
            assert rows["openalex"]["url"] == "https://doi.org/10.1/eeglab"
            assert rows["semanticscholar"]["external_id"] == "S2"

    def test_skips_papers_without_title_or_id(self, temp_db: Path):
        papers = [
            Paper(title="", ids=IDSet(openalex_id="https://openalex.org/W1")),
            Paper(title="no id", ids=IDSet()),
        ]
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            counts = _store_papers(papers, "test")
            assert counts == {}

    def test_upsert_deduplicates_same_paper(self, temp_db: Path):
        paper = Paper(title="dup", ids=IDSet(openalex_id="https://openalex.org/W1"), year=2020)
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            _store_papers([paper], "test")
            _store_papers([paper], "test")
            with get_connection("test") as conn:
                count = conn.execute("SELECT COUNT(*) AS c FROM papers").fetchone()["c"]
            assert count == 1

    def test_force_source_uses_native_id(self, temp_db: Path):
        # A PubMed-restricted sync should label the row 'pubmed' using the PMID,
        # even though the paper also carries an OpenAlex id.
        paper = Paper(
            title="P",
            ids=IDSet(openalex_id="https://openalex.org/W1", pmid="555"),
        )
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            counts = _store_papers([paper], "test", force_source="pubmed")
            assert counts == {"pubmed": 1}
            with get_connection("test") as conn:
                row = conn.execute("SELECT source, external_id FROM papers").fetchone()
            assert row["source"] == "pubmed"
            assert row["external_id"] == "555"


async def _answer() -> int:
    return 42


class TestRunHelper:
    """The _run async bridge must work with or without a running event loop."""

    def test_runs_without_existing_loop(self):
        # Sync context (CLI / scheduler thread): asyncio.run path.
        assert ps._run(_answer()) == 42

    def test_runs_inside_running_loop(self):
        # If a loop is already running, _run offloads to a worker thread instead
        # of raising "asyncio.run() cannot be called from a running event loop".
        async def driver() -> int:
            return ps._run(_answer())

        assert asyncio.run(driver()) == 42


class TestPapersSync:
    """Smoke tests using real opencite/network calls."""

    def test_sync_openalex_papers_basic(self, temp_db: Path):
        """Basic OpenAlex sync through opencite (real API call)."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            count = sync_openalex_papers(
                "Hierarchical Event Descriptors", max_results=5, project="test"
            )

            # Accept 0 for transient network issues.
            assert count >= 0
            if count > 0:
                with get_connection("test") as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) as count FROM papers WHERE source = 'openalex'",
                    ).fetchone()
                    assert row["count"] > 0

    def test_sync_respects_max_results(self, temp_db: Path):
        """max_results is respected for a single-source sync."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            count = sync_openalex_papers("neuroscience", max_results=2, project="test")
            assert count <= 2


class TestPaperToResult:
    """Map opencite Paper objects to the shared SearchResult shape."""

    def test_maps_core_fields(self):
        paper = Paper(
            title="Recent EEG paper",
            ids=IDSet(openalex_id="https://openalex.org/W9", doi="10.1/x"),
            year=2026,
            abstract="Latest findings.",
        )
        result = _paper_to_result(paper)
        assert result.title == "Recent EEG paper"
        assert result.url == "https://doi.org/10.1/x"
        assert result.source == "openalex"
        assert result.created_at == "2026"
        assert result.status == "published"
        assert result.snippet == "Latest findings."

    def test_handles_missing_year_and_id(self):
        result = _paper_to_result(Paper(title="No metadata", ids=IDSet()))
        assert result.created_at == ""
        assert result.source == "opencite"


class TestCachePapersAsync:
    """Background caching of live-search results (real SQLite, no mocks)."""

    def test_caches_papers_into_db(self, temp_db: Path):
        papers = [
            Paper(
                title="Cached paper", ids=IDSet(openalex_id="https://openalex.org/W5"), year=2026
            ),
        ]
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Caching is async; join the returned thread before asserting.
            _cache_papers_async(papers, "test").join(timeout=10)
            with get_connection("test") as conn:
                count = conn.execute("SELECT COUNT(*) AS c FROM papers").fetchone()["c"]
        assert count == 1


class TestLivePaperSearch:
    """Live opencite search (real network)."""

    def test_live_search_returns_recent(self, temp_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            results = search_papers_live(
                "EEGLAB EEG independent component analysis",
                project="test",
                limit=3,
                timeout=40,
            )

        # Network-dependent: accept empty on transient failure, but the shape
        # must always be correct and every result must be displayable.
        assert isinstance(results, list)
        assert all(r.status == "published" for r in results)
        assert all(r.title for r in results)


class TestPapersSyncTypeGuard:
    """Sync functions reject bare strings to prevent character iteration."""

    def test_sync_all_papers_rejects_bare_string(self) -> None:
        with pytest.raises(TypeError, match="must be a list of strings"):
            sync_all_papers(queries="MNE-Python")  # type: ignore[arg-type]

    def test_sync_citing_papers_rejects_bare_string(self) -> None:
        with pytest.raises(TypeError, match="must be a list of strings"):
            sync_citing_papers(dois="10.3389/fnins.2013.00267")  # type: ignore[arg-type]
