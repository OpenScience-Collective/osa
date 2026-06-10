"""Tests for the public citations feed endpoint: GET /{community_id}/citations.

Uses a real registered community, a temporary SQLite knowledge database with
citing papers, and the config gate toggled per test. No business logic is
mocked except in TestCitationsFeedErrors, where get_citation_stats is patched
at the router call boundary to inject DB/unexpected errors and verify the
503/500 responses.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.community import create_community_router
from src.assistants import discover_assistants, registry
from src.core.config.community import PublicFeedsConfig
from src.knowledge.db import get_connection, init_db, upsert_paper

COMMUNITY_ID = "eeglab"
DOI_A = "10.1016/j.jneumeth.2003.10.009"
DOI_B = "10.1016/j.neuroimage.2019.05.026"

discover_assistants()


@pytest.fixture
def citations_db(tmp_path: Path) -> Iterator[Path]:
    """Temp knowledge DB with citing papers across two canonical DOIs."""
    db_path = tmp_path / "knowledge" / "test.db"
    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db(COMMUNITY_ID)
        with get_connection(COMMUNITY_ID) as conn:
            rows = [
                ("a1", "2019-05-01", DOI_A),
                ("a2", "2019-11-20", DOI_A),
                ("a3", "2020", DOI_A),
                ("b1", "2020-02-02", DOI_B),
                ("k1", "2021", None),  # keyword-only, excluded from stats
            ]
            for external_id, created_at, cites_doi in rows:
                upsert_paper(
                    conn,
                    source="openalex",
                    external_id=external_id,
                    title=f"Paper {external_id}",
                    first_message=None,
                    url=f"https://doi.org/10.test/{external_id}",
                    created_at=created_at,
                    cites_doi=cites_doi,
                )
            conn.commit()
        yield db_path


@pytest.fixture
def citations_enabled() -> Iterator[None]:
    info = registry.get(COMMUNITY_ID)
    assert info is not None and info.community_config is not None
    original = info.community_config.public_feeds
    info.community_config.public_feeds = PublicFeedsConfig(citations=True)
    try:
        yield
    finally:
        info.community_config.public_feeds = original


@pytest.fixture
def citations_disabled_none() -> Iterator[None]:
    info = registry.get(COMMUNITY_ID)
    assert info is not None and info.community_config is not None
    original = info.community_config.public_feeds
    info.community_config.public_feeds = None
    try:
        yield
    finally:
        info.community_config.public_feeds = original


@pytest.fixture
def citations_flag_false() -> Iterator[None]:
    info = registry.get(COMMUNITY_ID)
    assert info is not None and info.community_config is not None
    original = info.community_config.public_feeds
    info.community_config.public_feeds = PublicFeedsConfig(citations=False)
    try:
        yield
    finally:
        info.community_config.public_feeds = original


@pytest.fixture
def citations_enabled_no_config() -> Iterator[None]:
    """Feed enabled but the community has no citations config block."""
    info = registry.get(COMMUNITY_ID)
    assert info is not None and info.community_config is not None
    orig_feeds = info.community_config.public_feeds
    orig_citations = info.community_config.citations
    info.community_config.public_feeds = PublicFeedsConfig(citations=True)
    info.community_config.citations = None
    try:
        yield
    finally:
        info.community_config.public_feeds = orig_feeds
        info.community_config.citations = orig_citations


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_community_router(COMMUNITY_ID))
    return TestClient(app)


class TestCitationsFeedGate:
    """The endpoint is opt-in via public_feeds.citations."""

    @pytest.mark.usefixtures("citations_disabled_none")
    def test_disabled_when_public_feeds_none(self, client, citations_db):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        assert resp.status_code == 404

    @pytest.mark.usefixtures("citations_flag_false")
    def test_disabled_when_flag_false(self, client, citations_db):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        assert resp.status_code == 404

    @pytest.mark.usefixtures("citations_enabled")
    def test_enabled_returns_200(self, client, citations_db):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        assert resp.status_code == 200


@pytest.mark.usefixtures("citations_enabled")
class TestCitationsFeedContent:
    def test_total_and_per_year(self, client, citations_db):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        body = resp.json()
        assert body["community_id"] == COMMUNITY_ID
        assert body["total"] == 4  # a1,a2,a3,b1 ; k1 unlinked excluded
        assert body["per_year"] == {"2019": 2, "2020": 2}

    def test_by_paper_stacked_breakdown(self, client, citations_db):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        by_paper = resp.json()["by_paper"]
        assert by_paper == {
            DOI_A: {"2019": 2, "2020": 1},
            DOI_B: {"2020": 1},
        }

    def test_canonical_dois_from_config(self, client, citations_db):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        canonical = resp.json()["canonical_dois"]
        # eeglab config tracks these canonical DOIs.
        assert DOI_A in canonical
        assert DOI_B in canonical

    def test_cache_control_header(self, client, citations_db):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        assert resp.headers["Cache-Control"] == "public, max-age=3600"

    def test_labels_from_config(self, client, citations_db):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        labels = resp.json()["labels"]
        # eeglab config defines human-readable labels for its canonical DOIs.
        assert labels.get(DOI_A) == "EEGLAB (Delorme 2004)"
        assert labels.get(DOI_B) == "ICLabel (Pion-Tonachini 2019)"
        # Mixed-case DOI suffix survives the config -> endpoint round-trip.
        assert labels.get("10.1162/IMAG.a.136") == "LSL (Kothe 2025)"


class TestCitationsFeedNoConfig:
    """Feed enabled for a community without a citations config block."""

    @pytest.mark.usefixtures("citations_enabled_no_config")
    def test_canonical_dois_empty_when_no_citations_config(self, client, citations_db):
        with patch("src.knowledge.db.get_db_path", return_value=citations_db):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        body = resp.json()
        assert resp.status_code == 200
        assert body["canonical_dois"] == []
        assert body["labels"] == {}
        # Stats still come from the DB regardless of config presence.
        assert body["total"] == 4


@pytest.mark.usefixtures("citations_enabled")
class TestCitationsFeedErrors:
    def test_db_error_returns_503(self, client):
        with patch(
            "src.api.routers.community.get_citation_stats",
            side_effect=sqlite3.OperationalError("db is locked"),
        ):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        assert resp.status_code == 503

    def test_unexpected_error_returns_500(self, client):
        with patch(
            "src.api.routers.community.get_citation_stats",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.get(f"/{COMMUNITY_ID}/citations")
        assert resp.status_code == 500
