"""Direct OpenAlex client for citation analysis.

opencite returns citing papers from a single page (<=200), ordered for its own
ranking, with no pagination and no aggregation exposed. For a citations
dashboard that silently truncates recent citations (the first page skews to
older, highly-cited works). We therefore query OpenAlex directly:

- ``counts_by_year`` uses ``group_by=publication_year`` for the *exact,
  complete* per-year histogram with no cap.
- ``recent_citing_papers`` cursor-paginates ``sort=publication_date:desc`` to
  collect the latest N citing papers for the search corpus.

The client takes an optional injected ``httpx.Client`` so tests can supply an
``httpx.MockTransport`` instead of hitting the network.
"""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

OPENALEX_BASE = "https://api.openalex.org"
_TIMEOUT = 30.0
_PER_PAGE = 200  # OpenAlex maximum page size


@dataclass
class CitingPaper:
    """A minimal citing-paper record for the search corpus."""

    openalex_id: str
    doi: str | None
    title: str
    publication_date: str | None
    url: str


def _strip_id(value: str | None) -> str:
    """Reduce an OpenAlex IRI (https://openalex.org/W123) to its bare id."""
    if not value:
        return ""
    return value.rstrip("/").rsplit("/", 1)[-1]


def _strip_doi(value: str | None) -> str | None:
    """Reduce a DOI URL to the bare ``10.xxxx/yyyy`` form."""
    if not value:
        return None
    cleaned = value.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned or None


class OpenAlexCitationClient:
    """Queries OpenAlex for citation counts and recent citing papers."""

    def __init__(
        self,
        *,
        email: str = "",
        api_key: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self._email = email
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=_TIMEOUT)

    def __enter__(self) -> "OpenAlexCitationClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _params(self, **extra: object) -> dict[str, object]:
        params: dict[str, object] = dict(extra)
        # mailto routes to the polite pool; api_key unlocks premium throughput.
        if self._email:
            params["mailto"] = self._email
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    def resolve_work_id(self, doi: str) -> str | None:
        """Resolve a DOI to its OpenAlex work id (e.g. ``W2128495200``)."""
        resp = self._client.get(
            f"{OPENALEX_BASE}/works/doi:{doi}",
            params=self._params(select="id"),
        )
        if resp.status_code == 404:
            logger.warning("OpenAlex has no work for DOI %s", doi)
            return None
        resp.raise_for_status()
        work_id = _strip_id(resp.json().get("id"))
        return work_id or None

    def counts_by_year(self, work_id: str) -> dict[int, int]:
        """Return the complete per-year count of works citing ``work_id``.

        Uses OpenAlex ``group_by`` so the counts are exact and uncapped,
        independent of how many citing papers are stored.
        """
        resp = self._client.get(
            f"{OPENALEX_BASE}/works",
            params=self._params(filter=f"cites:{work_id}", group_by="publication_year"),
        )
        resp.raise_for_status()
        counts: dict[int, int] = {}
        for group in resp.json().get("group_by", []):
            try:
                year = int(group["key"])
            except (KeyError, TypeError, ValueError):
                continue  # non-year buckets (e.g. "unknown") are skipped
            counts[year] = int(group.get("count", 0))
        return counts

    def recent_citing_papers(self, work_id: str, limit: int = 2000) -> list[CitingPaper]:
        """Collect up to ``limit`` most-recent works citing ``work_id``.

        Cursor-paginates ``sort=publication_date:desc`` so the stored sample is
        the newest citations rather than an arbitrary first page.
        """
        papers: list[CitingPaper] = []
        cursor: str | None = "*"
        while cursor and len(papers) < limit:
            page_size = min(_PER_PAGE, limit - len(papers))
            resp = self._client.get(
                f"{OPENALEX_BASE}/works",
                params=self._params(
                    filter=f"cites:{work_id}",
                    sort="publication_date:desc",
                    select="id,doi,title,publication_date",
                    cursor=cursor,
                    **{"per-page": page_size},
                ),
            )
            resp.raise_for_status()
            data = resp.json()
            for work in data.get("results", []):
                title = work.get("title")
                if not title:
                    continue
                papers.append(
                    CitingPaper(
                        openalex_id=_strip_id(work.get("id")),
                        doi=_strip_doi(work.get("doi")),
                        title=title,
                        publication_date=work.get("publication_date"),
                        url=work.get("doi") or work.get("id") or "",
                    )
                )
                if len(papers) >= limit:
                    break
            cursor = data.get("meta", {}).get("next_cursor")
        return papers
