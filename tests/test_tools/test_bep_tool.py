"""Tests for the BIDS BEP lookup tool."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import get_connection, init_db, upsert_bep_item


@pytest.fixture
def bep_db(tmp_path: Path):
    """Create a test database with BEP data."""
    db_path = tmp_path / "knowledge" / "bids.db"

    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db("bids")

        with get_connection("bids") as conn:
            upsert_bep_item(
                conn,
                bep_number="032",
                title="Microelectrode electrophysiology",
                status="proposed",
                pull_request_url="https://github.com/bids-standard/bids-specification/pull/1705",
                pull_request_number=1705,
                html_preview_url="https://bids-specification--1705.org.readthedocs.build/en/1705/modality-specific-files/microelectrode-electrophysiology.html",
                leads='["Cody Baker", "Ben Dichter"]',
                content="Microelectrode Electrophysiology data for neuropixels probes and other recording devices.",
            )
            upsert_bep_item(
                conn,
                bep_number="020",
                title="Eye Tracking including Gaze Position and Pupil Size",
                status="proposed",
                pull_request_url="https://github.com/bids-standard/bids-specification/pull/1128",
                pull_request_number=1128,
                html_preview_url="https://bids-specification--1128.org.readthedocs.build/en/1128/modality-specific-files/physiological-recordings.html#eye-tracking",
                leads='["Benjamin de Haas"]',
                content="Eye tracking data including gaze position and pupil size measurements.",
            )
            upsert_bep_item(
                conn,
                bep_number="004",
                title="Susceptibility Weighted Imaging",
                status="draft",
                google_doc_url="https://docs.google.com/document/d/1kyw9mGgacNqeMbp4xZet3RnDhcMmf4_BmRgKaOkO2Sc/",
            )
            conn.commit()

    return db_path


class TestLookupBep:
    """Tests for the lookup_bep tool."""

    def test_lookup_by_number(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with (
            patch("src.assistants.bids.tools.get_db_path", return_value=bep_db),
            patch("src.knowledge.db.get_db_path", return_value=bep_db),
        ):
            result = lookup_bep.invoke({"query": "032"})

        assert "BEP032" in result
        assert "Microelectrode electrophysiology" in result
        assert "proposed" in result
        assert "1705" in result

    def test_lookup_by_bep_prefix(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with (
            patch("src.assistants.bids.tools.get_db_path", return_value=bep_db),
            patch("src.knowledge.db.get_db_path", return_value=bep_db),
        ):
            result = lookup_bep.invoke({"query": "BEP032"})

        assert "BEP032" in result

    def test_lookup_by_keyword(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with (
            patch("src.assistants.bids.tools.get_db_path", return_value=bep_db),
            patch("src.knowledge.db.get_db_path", return_value=bep_db),
        ):
            result = lookup_bep.invoke({"query": "neuropixels"})

        assert "BEP032" in result

    def test_lookup_eye_tracking(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with (
            patch("src.assistants.bids.tools.get_db_path", return_value=bep_db),
            patch("src.knowledge.db.get_db_path", return_value=bep_db),
        ):
            result = lookup_bep.invoke({"query": "eye tracking"})

        assert "BEP020" in result

    def test_lookup_draft_bep(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with (
            patch("src.assistants.bids.tools.get_db_path", return_value=bep_db),
            patch("src.knowledge.db.get_db_path", return_value=bep_db),
        ):
            result = lookup_bep.invoke({"query": "004"})

        assert "BEP004" in result
        assert "draft" in result
        assert "Google Doc" in result

    def test_lookup_no_results(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with (
            patch("src.assistants.bids.tools.get_db_path", return_value=bep_db),
            patch("src.knowledge.db.get_db_path", return_value=bep_db),
        ):
            result = lookup_bep.invoke({"query": "nonexistent data type xyz"})

        assert "No BEPs found" in result

    def test_lookup_shows_links(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with (
            patch("src.assistants.bids.tools.get_db_path", return_value=bep_db),
            patch("src.knowledge.db.get_db_path", return_value=bep_db),
        ):
            result = lookup_bep.invoke({"query": "032"})

        assert "PR:" in result
        assert "Preview:" in result
        assert "pull/1705" in result

    def test_lookup_db_not_found(self, tmp_path: Path):
        from src.assistants.bids.tools import lookup_bep

        fake_path = tmp_path / "nonexistent" / "bids.db"
        with patch("src.assistants.bids.tools.get_db_path", return_value=fake_path):
            result = lookup_bep.invoke({"query": "032"})

        assert "not initialized" in result


class TestSearchBeps:
    """Direct tests for the search_beps function."""

    def test_search_by_number(self, bep_db: Path):
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            results = search_beps("032", project="bids")

        assert len(results) == 1
        assert results[0].bep_number == "032"
        assert results[0].title == "Microelectrode electrophysiology"
        assert results[0].status == "proposed"

    def test_search_by_keyword(self, bep_db: Path):
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            results = search_beps("neuropixels", project="bids")

        assert len(results) >= 1
        assert any(r.bep_number == "032" for r in results)

    def test_search_returns_leads(self, bep_db: Path):
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            results = search_beps("032", project="bids")

        assert results[0].leads == ["Cody Baker", "Ben Dichter"]

    def test_search_no_results(self, bep_db: Path):
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            results = search_beps("nonexistent xyz", project="bids")

        assert results == []

    def test_search_bep_prefix(self, bep_db: Path):
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            results = search_beps("BEP020", project="bids")

        assert len(results) == 1
        assert results[0].bep_number == "020"

    def test_search_snippet_truncation(self, bep_db: Path):
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            results = search_beps("032", project="bids")

        # Content is short, should not be truncated
        assert not results[0].snippet.endswith("...")


class TestUpsertAndFTSTrigger:
    """Tests for upsert update behavior and FTS5 trigger sync."""

    def test_upsert_updates_existing_bep(self, bep_db: Path):
        """Upserting a BEP with the same number should update, not duplicate."""
        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            with get_connection("bids") as conn:
                upsert_bep_item(
                    conn,
                    bep_number="032",
                    title="Updated Title for BEP032",
                    status="closed",
                )
                conn.commit()

            with get_connection("bids") as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM bep_items WHERE bep_number = '032'"
                ).fetchone()[0]
                assert count == 1

                row = conn.execute(
                    "SELECT title, status FROM bep_items WHERE bep_number = '032'"
                ).fetchone()
                assert row["title"] == "Updated Title for BEP032"
                assert row["status"] == "closed"

    def test_fts_index_updates_on_upsert(self, bep_db: Path):
        """FTS index should reflect updated content after upsert."""
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            # Update BEP032 with new content
            with get_connection("bids") as conn:
                upsert_bep_item(
                    conn,
                    bep_number="032",
                    title="Microelectrode electrophysiology",
                    status="proposed",
                    content="Unique content about intracranial recordings and Utah arrays.",
                )
                conn.commit()

            # New content should be searchable via FTS
            results = search_beps("Utah arrays", project="bids")
            assert len(results) >= 1
            assert any(r.bep_number == "032" for r in results)

            # Old content should still match (title unchanged)
            results = search_beps("electrophysiology", project="bids")
            assert len(results) >= 1


class TestEdgeCases:
    """Tests for edge cases in BEP search."""

    def test_empty_query(self, bep_db: Path):
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            results = search_beps("", project="bids")

        # Empty query is not a BEP number, goes to FTS; phrase '""' matches nothing
        assert isinstance(results, list)

    def test_whitespace_query(self, bep_db: Path):
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            results = search_beps("   ", project="bids")

        assert isinstance(results, list)

    def test_fts5_special_chars(self, bep_db: Path):
        """FTS5 operators in queries should be sanitized, not cause errors."""
        from src.knowledge.search import search_beps

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            # These should not raise even though they contain FTS5 syntax
            for query in ["AND OR NOT", "eye*", 'test"quote', "NEAR(a b)"]:
                results = search_beps(query, project="bids")
                assert isinstance(results, list)

    def test_lookup_bep_table_missing_in_existing_db(self, tmp_path: Path):
        """lookup_bep should handle a DB that exists but has no bep_items table."""
        from src.assistants.bids.tools import lookup_bep

        db_path = tmp_path / "knowledge" / "bids.db"
        db_path.parent.mkdir(parents=True)
        # Create an empty database (no tables)
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE dummy (id INTEGER)")
        conn.close()

        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            result = lookup_bep.invoke({"query": "032"})

        assert "not initialized" in result
