"""Tests for the BIDS BEP lookup tool."""

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

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            result = lookup_bep.invoke({"query": "032"})

        assert "BEP032" in result
        assert "Microelectrode electrophysiology" in result
        assert "proposed" in result
        assert "1705" in result

    def test_lookup_by_bep_prefix(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            result = lookup_bep.invoke({"query": "BEP032"})

        assert "BEP032" in result

    def test_lookup_by_keyword(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            result = lookup_bep.invoke({"query": "neuropixels"})

        assert "BEP032" in result

    def test_lookup_eye_tracking(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            result = lookup_bep.invoke({"query": "eye tracking"})

        assert "BEP020" in result

    def test_lookup_draft_bep(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            result = lookup_bep.invoke({"query": "004"})

        assert "BEP004" in result
        assert "draft" in result
        assert "Google Doc" in result

    def test_lookup_no_results(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            result = lookup_bep.invoke({"query": "nonexistent data type xyz"})

        assert "No BEPs found" in result

    def test_lookup_shows_links(self, bep_db: Path):
        from src.assistants.bids.tools import lookup_bep

        with patch("src.knowledge.db.get_db_path", return_value=bep_db):
            result = lookup_bep.invoke({"query": "032"})

        assert "PR:" in result
        assert "Preview:" in result
        assert "pull/1705" in result

    def test_lookup_db_not_found(self, tmp_path: Path):
        from src.assistants.bids.tools import lookup_bep

        fake_path = tmp_path / "nonexistent" / "bids.db"
        with patch("src.knowledge.db.get_db_path", return_value=fake_path):
            result = lookup_bep.invoke({"query": "032"})

        assert "not initialized" in result
