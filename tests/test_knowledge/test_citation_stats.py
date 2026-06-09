"""Tests for citation stats aggregation and the cites_doi linkage column.

Uses a real temporary SQLite database (only the DB path is redirected); no
business logic is mocked.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import get_connection, init_db, upsert_paper
from src.knowledge.search import CitationStats, get_citation_stats

DOI_A = "10.1016/j.jneumeth.2003.10.009"
DOI_B = "10.1016/j.neuroimage.2019.05.026"


def _add_paper(conn, external_id, *, created_at, cites_doi=None, source="openalex"):
    upsert_paper(
        conn,
        source=source,
        external_id=external_id,
        title=f"Citing paper {external_id}",
        first_message=None,
        url=f"https://doi.org/10.test/{external_id}",
        created_at=created_at,
        cites_doi=cites_doi,
    )


@pytest.fixture
def citations_db(tmp_path: Path):
    """Temp DB with citing papers across two canonical DOIs and several years."""
    db_path = tmp_path / "knowledge" / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()
        with get_connection() as conn:
            # DOI_A: 2 in 2019, 1 in 2020
            _add_paper(conn, "a1", created_at="2019-05-01", cites_doi=DOI_A)
            _add_paper(conn, "a2", created_at="2019-11-20", cites_doi=DOI_A)
            _add_paper(conn, "a3", created_at="2020", cites_doi=DOI_A)
            # DOI_B: 1 in 2020, 1 in 2021
            _add_paper(conn, "b1", created_at="2020-02-02", cites_doi=DOI_B)
            _add_paper(conn, "b2", created_at="2021-07-07", cites_doi=DOI_B)
            # Keyword-search paper (no citation link) - excluded from stats
            _add_paper(conn, "k1", created_at="2022", cites_doi=None)
            # Citing paper with an unusable date - excluded from year buckets
            _add_paper(conn, "x1", created_at="", cites_doi=DOI_A)
            _add_paper(conn, "x2", created_at=None, cites_doi=DOI_B)
            conn.commit()
        yield db_path


class TestGetCitationStats:
    def test_returns_citation_stats_object(self, citations_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            stats = get_citation_stats(project="eeglab")
        assert isinstance(stats, CitationStats)

    def test_total_excludes_unlinked_and_undated(self, citations_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            stats = get_citation_stats(project="eeglab")
        # 5 linked papers with valid years (a1,a2,a3,b1,b2); k1 unlinked,
        # x1/x2 undated are excluded.
        assert stats.total == 5

    def test_per_year_aggregates_across_dois(self, citations_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            stats = get_citation_stats(project="eeglab")
        assert stats.per_year == {"2019": 2, "2020": 2, "2021": 1}

    def test_per_year_is_sorted_ascending(self, citations_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            stats = get_citation_stats(project="eeglab")
        assert list(stats.per_year.keys()) == sorted(stats.per_year.keys())

    def test_by_paper_stacked_breakdown(self, citations_db: Path):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            stats = get_citation_stats(project="eeglab")
        assert stats.by_paper == {
            DOI_A: {"2019": 2, "2020": 1},
            DOI_B: {"2020": 1, "2021": 1},
        }

    def test_empty_database(self, tmp_path: Path):
        db_path = tmp_path / "knowledge" / "empty.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db()
            stats = get_citation_stats(project="eeglab")
        assert stats.total == 0
        assert stats.per_year == {}
        assert stats.by_paper == {}


class TestCitesDoiUpsert:
    def test_backfill_sets_link_on_existing_row(self, tmp_path: Path):
        """A row first stored without a link gets it on a later citation sync."""
        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db()
            with get_connection() as conn:
                _add_paper(conn, "p1", created_at="2020", cites_doi=None)
                _add_paper(conn, "p1", created_at="2020", cites_doi=DOI_A)
                conn.commit()
                row = conn.execute(
                    "SELECT cites_doi FROM papers WHERE external_id = 'p1'"
                ).fetchone()
        assert row["cites_doi"] == DOI_A

    def test_first_link_wins_over_later_link(self, tmp_path: Path):
        """COALESCE keeps the first recorded canonical DOI for overlapping papers."""
        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db()
            with get_connection() as conn:
                _add_paper(conn, "p1", created_at="2020", cites_doi=DOI_A)
                _add_paper(conn, "p1", created_at="2020", cites_doi=DOI_B)
                conn.commit()
                row = conn.execute(
                    "SELECT cites_doi FROM papers WHERE external_id = 'p1'"
                ).fetchone()
        assert row["cites_doi"] == DOI_A

    def test_keyword_sync_does_not_erase_link(self, tmp_path: Path):
        """A later keyword sync (cites_doi=None) must not clobber an existing link."""
        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db()
            with get_connection() as conn:
                _add_paper(conn, "p1", created_at="2020", cites_doi=DOI_A)
                _add_paper(conn, "p1", created_at="2020", cites_doi=None)
                conn.commit()
                row = conn.execute(
                    "SELECT cites_doi FROM papers WHERE external_id = 'p1'"
                ).fetchone()
        assert row["cites_doi"] == DOI_A


class TestCitesDoiMigration:
    def test_migration_adds_column_to_legacy_papers_table(self, tmp_path: Path):
        """A papers table created before cites_doi gains the column via init_db."""
        db_path = tmp_path / "knowledge" / "legacy.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            # Simulate a pre-migration schema: papers without cites_doi.
            with get_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE papers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        first_message TEXT,
                        status TEXT NOT NULL DEFAULT 'published',
                        url TEXT NOT NULL,
                        created_at TEXT,
                        synced_at TEXT NOT NULL,
                        UNIQUE(source, external_id)
                    )
                    """
                )
                conn.commit()
                cols_before = [r[1] for r in conn.execute("PRAGMA table_info(papers)")]
            assert "cites_doi" not in cols_before

            # Running init_db must migrate the existing table in place.
            init_db()
            with get_connection() as conn:
                cols_after = [r[1] for r in conn.execute("PRAGMA table_info(papers)")]
                # The new column is usable for inserts after migration.
                _add_paper(conn, "p1", created_at="2020", cites_doi=DOI_A)
                conn.commit()
                row = conn.execute(
                    "SELECT cites_doi FROM papers WHERE external_id = 'p1'"
                ).fetchone()

        assert "cites_doi" in cols_after
        assert row["cites_doi"] == DOI_A
