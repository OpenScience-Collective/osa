"""Tests for the BEP sync module.

Tests use real data from the BIDS website and specification repos.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.bep_sync import (
    _extract_pr_number,
    _fetch_beps_yaml,
    _format_leads,
    sync_beps,
)
from src.knowledge.db import get_connection, init_db


class TestExtractPrNumber:
    """Tests for PR number extraction from URLs."""

    def test_standard_url(self):
        url = "https://github.com/bids-standard/bids-specification/pull/1705"
        assert _extract_pr_number(url) == 1705

    def test_no_match(self):
        assert _extract_pr_number("https://docs.google.com/document/d/abc") is None

    def test_trailing_slash_no_match(self):
        assert _extract_pr_number("https://github.com/org/repo/pull/") is None


class TestFormatLeads:
    """Tests for lead name formatting."""

    def test_normal_leads(self):
        leads = [
            {"given-names": "Viviana", "family-names": "Siless"},
            {"given-names": "Chris", "family-names": "Markiewicz"},
        ]
        result = json.loads(_format_leads(leads))
        assert result == ["Viviana Siless", "Chris Markiewicz"]

    def test_empty_leads(self):
        assert _format_leads([]) is None
        assert _format_leads(None) is None

    def test_blank_names(self):
        leads = [{"given-names": " ", "family-names": " "}]
        assert _format_leads(leads) is None

    def test_missing_keys(self):
        leads = [{"given-names": "Viviana"}]
        result = json.loads(_format_leads(leads))
        assert result == ["Viviana"]

    def test_empty_dict(self):
        leads = [{}]
        assert _format_leads(leads) is None


class TestFetchBepsYaml:
    """Tests that fetch real data from the BIDS website repo."""

    @pytest.mark.network
    def test_fetch_real_beps_yaml(self):
        """Fetch the actual beps.yml and verify structure."""
        import httpx

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            beps = _fetch_beps_yaml(client)

        assert len(beps) > 0
        # Every entry should have a number and title
        for bep in beps:
            assert "number" in bep, f"BEP entry missing 'number': {bep}"
            assert "title" in bep, f"BEP entry missing 'title': {bep}"

    @pytest.mark.network
    def test_beps_with_prs_exist(self):
        """Verify that some BEPs have pull_request fields."""
        import httpx

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            beps = _fetch_beps_yaml(client)

        beps_with_prs = [b for b in beps if b.get("pull_request")]
        assert len(beps_with_prs) >= 5, (
            f"Expected at least 5 BEPs with PRs, found {len(beps_with_prs)}"
        )


class TestSyncBeps:
    """Integration tests for the full sync flow."""

    @pytest.mark.network
    def test_sync_stores_beps_in_db(self, tmp_path: Path):
        """Run a real sync and verify data is stored."""
        db_path = tmp_path / "knowledge" / "bids.db"

        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("bids")
            stats = sync_beps("bids")

        assert stats["total"] > 0
        assert stats["with_content"] >= 0  # might be 0 if all PRs are closed

        # Verify data is in the database
        with (
            patch("src.knowledge.db.get_db_path", return_value=db_path),
            get_connection("bids") as conn,
        ):
            count = conn.execute("SELECT COUNT(*) FROM bep_items").fetchone()[0]
            assert count == stats["total"]

            # Check a known BEP exists (BEP032 is well-established)
            row = conn.execute("SELECT title FROM bep_items WHERE bep_number = '032'").fetchone()
            assert row is not None
            assert "electrophysiology" in row["title"].lower()

    @pytest.mark.network
    def test_sync_idempotent(self, tmp_path: Path):
        """Running sync twice should not duplicate entries."""
        db_path = tmp_path / "knowledge" / "bids.db"

        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("bids")
            stats1 = sync_beps("bids")
            stats2 = sync_beps("bids")

        assert stats1["total"] == stats2["total"]

        with (
            patch("src.knowledge.db.get_db_path", return_value=db_path),
            get_connection("bids") as conn,
        ):
            count = conn.execute("SELECT COUNT(*) FROM bep_items").fetchone()[0]
            assert count == stats1["total"]
