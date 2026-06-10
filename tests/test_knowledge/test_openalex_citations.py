"""Tests for the direct OpenAlex citation client.

Uses httpx.MockTransport to serve canned OpenAlex responses at the transport
layer (an HTTP fixture, not a mock of business logic) so the client's parsing,
pagination, and error handling are exercised without network access.
"""

import httpx
import pytest

from src.knowledge.openalex_citations import (
    CitingPaper,
    OpenAlexCitationClient,
    _strip_doi,
    _strip_id,
)


class TestCitesFilter:
    def test_single_work_id(self):
        assert OpenAlexCitationClient._cites_filter("W1") == "cites:W1"

    def test_multiple_work_ids_or_joined(self):
        assert OpenAlexCitationClient._cites_filter(["W1", "W2", "W3"]) == "cites:W1|W2|W3"

    def test_filters_empty_ids(self):
        assert OpenAlexCitationClient._cites_filter(["W1", "", "W2"]) == "cites:W1|W2"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            OpenAlexCitationClient._cites_filter([])


def _client(handler) -> OpenAlexCitationClient:
    transport = httpx.MockTransport(handler)
    return OpenAlexCitationClient(email="t@example.org", client=httpx.Client(transport=transport))


class TestHelpers:
    def test_strip_id(self):
        assert _strip_id("https://openalex.org/W123") == "W123"
        assert _strip_id("W123") == "W123"
        assert _strip_id(None) == ""

    def test_strip_doi(self):
        assert _strip_doi("https://doi.org/10.1/x") == "10.1/x"
        assert _strip_doi("10.1/x") == "10.1/x"
        assert _strip_doi(None) is None


class TestResolveWorkId:
    def test_resolves_doi_to_work_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/works/doi:10.1/x" in str(request.url)
            return httpx.Response(200, json={"id": "https://openalex.org/W999"})

        with _client(handler) as c:
            assert c.resolve_work_id("10.1/x") == "W999"

    def test_unresolved_doi_returns_none(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "not found"})

        with _client(handler) as c:
            assert c.resolve_work_id("10.1/missing") is None

    def test_includes_mailto_param(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["mailto"] = request.url.params.get("mailto")
            return httpx.Response(200, json={"id": "https://openalex.org/W1"})

        with _client(handler) as c:
            c.resolve_work_id("10.1/x")
        assert seen["mailto"] == "t@example.org"


class TestCountsByYear:
    def test_parses_group_by_counts(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params.get("group_by") == "publication_year"
            assert request.url.params.get("filter") == "cites:W1"
            return httpx.Response(
                200,
                json={
                    "meta": {"count": 17},
                    "group_by": [
                        {"key": "2024", "count": 10},
                        {"key": "2023", "count": 5},
                        {"key": "2022", "count": 2},
                    ],
                },
            )

        with _client(handler) as c:
            counts = c.counts_by_year("W1")
        assert counts == {2024: 10, 2023: 5, 2022: 2}

    def test_version_group_uses_or_joined_filter(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["filter"] = request.url.params.get("filter")
            return httpx.Response(200, json={"group_by": [{"key": "2024", "count": 5}]})

        with _client(handler) as c:
            counts = c.counts_by_year(["W1", "W2"])
        assert seen["filter"] == "cites:W1|W2"
        assert counts == {2024: 5}

    def test_skips_non_year_buckets(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "group_by": [
                        {"key": "2024", "count": 3},
                        {"key": "unknown", "count": 9},
                        {"key": None, "count": 1},
                    ]
                },
            )

        with _client(handler) as c:
            counts = c.counts_by_year("W1")
        assert counts == {2024: 3}


class TestRecentCitingPapers:
    def test_paginates_with_cursor(self):
        # Two pages: cursor "*" -> two works + next_cursor "p2"; "p2" -> one work, end.
        def handler(request: httpx.Request) -> httpx.Response:
            cursor = request.url.params.get("cursor")
            assert request.url.params.get("sort") == "publication_date:desc"
            if cursor == "*":
                return httpx.Response(
                    200,
                    json={
                        "meta": {"next_cursor": "p2"},
                        "results": [
                            {
                                "id": "https://openalex.org/W10",
                                "doi": "https://doi.org/10.1/a",
                                "title": "Newest",
                                "publication_date": "2026-01-01",
                            },
                            {
                                "id": "https://openalex.org/W11",
                                "doi": None,
                                "title": "Second",
                                "publication_date": "2025-06-01",
                            },
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "meta": {"next_cursor": None},
                    "results": [
                        {
                            "id": "https://openalex.org/W12",
                            "doi": "10.1/c",
                            "title": "Third",
                            "publication_date": "2025-01-01",
                        }
                    ],
                },
            )

        with _client(handler) as c:
            papers = c.recent_citing_papers("W1", limit=100)

        assert [p.openalex_id for p in papers] == ["W10", "W11", "W12"]
        assert all(isinstance(p, CitingPaper) for p in papers)
        assert papers[0].doi == "10.1/a"  # url-form DOI normalized
        assert papers[1].doi is None
        assert papers[0].url == "https://doi.org/10.1/a"

    def test_respects_limit_across_pages(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # Always offer a next cursor; the client must stop at the limit.
            return httpx.Response(
                200,
                json={
                    "meta": {"next_cursor": "more"},
                    "results": [
                        {
                            "id": f"https://openalex.org/W{request.url.params.get('cursor')}",
                            "doi": None,
                            "title": "P",
                            "publication_date": "2025-01-01",
                        }
                    ],
                },
            )

        with _client(handler) as c:
            papers = c.recent_citing_papers("W1", limit=3)
        assert len(papers) == 3

    def test_stops_on_empty_results_page(self):
        # A non-null cursor with no results must not spin forever.
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if request.url.params.get("cursor") == "*":
                return httpx.Response(
                    200,
                    json={
                        "meta": {"next_cursor": "p2"},
                        "results": [
                            {
                                "id": "https://openalex.org/W1",
                                "doi": None,
                                "title": "P",
                                "publication_date": "2025-01-01",
                            }
                        ],
                    },
                )
            # Second page: cursor still present but no results -> must stop.
            return httpx.Response(200, json={"meta": {"next_cursor": "p3"}, "results": []})

        with _client(handler) as c:
            papers = c.recent_citing_papers("W1", limit=100)
        assert len(papers) == 1
        assert calls["n"] == 2  # stopped at the empty page, did not continue

    def test_absent_meta_stops_pagination(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "doi": "10.1/x",
                            "title": "P",
                            "publication_date": "2025-01-01",
                        }
                    ]
                },
            )

        with _client(handler) as c:
            papers = c.recent_citing_papers("W1", limit=100)
        assert len(papers) == 1
        assert papers[0].url == "https://doi.org/10.1/x"  # url built from stripped doi

    def test_skips_titleless_works(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "meta": {"next_cursor": None},
                    "results": [
                        {"id": "https://openalex.org/W1", "title": None, "doi": None},
                        {
                            "id": "https://openalex.org/W2",
                            "title": "Has title",
                            "doi": None,
                            "publication_date": "2025-01-01",
                        },
                    ],
                },
            )

        with _client(handler) as c:
            papers = c.recent_citing_papers("W1", limit=10)
        assert [p.openalex_id for p in papers] == ["W2"]


class TestErrorPropagation:
    def test_http_error_raises(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "server"})

        with _client(handler) as c, pytest.raises(httpx.HTTPStatusError):
            c.counts_by_year("W1")
