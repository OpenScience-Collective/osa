"""Paper sync backed by opencite.

Fetches papers through the `opencite` multi-source search/citation client and
writes them into the local knowledge database. opencite aggregates and
deduplicates across OpenAlex, Semantic Scholar, PubMed (and more), replacing
the previous hand-rolled per-source fetchers and inverted-index handling.

Public sync functions keep their original signatures so the CLI
(`src/cli/sync.py`) and the scheduler (`src/api/scheduler.py`) call them
unchanged; only the fetch layer is swapped.

See: https://github.com/neuromechanist/opencite
"""

import asyncio
import logging
from collections.abc import Iterable

from opencite import Config, Paper
from opencite.citations import CitationExplorer
from opencite.search import SearchOrchestrator

from src.knowledge.db import get_connection, update_sync_metadata, upsert_paper

logger = logging.getLogger(__name__)

# Scholarly sources synced by default. opencite also supports arxiv, biorxiv,
# medrxiv, osf, zenodo, figshare, crossref and core; those broader sources are
# reserved for the opt-in live-search feature (issue #308) so batch sync stays
# focused on peer-reviewed literature and matches prior coverage.
DEFAULT_SOURCES: tuple[str, ...] = ("openalex", "s2", "pubmed")

# opencite source name -> OSA `papers.source` label. Kept stable so dedup and
# the existing rows in the database (openalex / semanticscholar / pubmed) line
# up with newly synced papers.
_OSA_SOURCE_BY_OPENCITE: dict[str, str] = {
    "openalex": "openalex",
    "s2": "semanticscholar",
    "pubmed": "pubmed",
}
# OSA source label -> opencite source name (used to restrict per-source syncs).
_OPENCITE_SOURCE_BY_OSA: dict[str, str] = {v: k for k, v in _OSA_SOURCE_BY_OPENCITE.items()}

# OpenAlex credentials set via configure_openalex(); merged into the per-sync
# Config as a fallback when explicit call arguments are not supplied. This
# preserves the CLI's "configure once, sync many" pattern.
_OPENALEX_API_KEY: str | None = None
_OPENALEX_EMAIL: str | None = None


def configure_openalex(api_key: str | None = None, email: str | None = None) -> None:
    """Store OpenAlex credentials for subsequent opencite-backed syncs.

    OpenAlex works anonymously; an API key grants premium limits and a contact
    email enables the faster polite pool. Values are merged into the opencite
    Config built for each sync (explicit per-call arguments still win).

    Args:
        api_key: OpenAlex API key for premium access.
        email: Contact email for OpenAlex polite pool access.
    """
    global _OPENALEX_API_KEY, _OPENALEX_EMAIL
    _OPENALEX_API_KEY = api_key.strip() if api_key and api_key.strip() else None
    _OPENALEX_EMAIL = email.strip() if email and email.strip() else None

    if _OPENALEX_API_KEY:
        logger.info("OpenAlex configured with API key")
    elif _OPENALEX_EMAIL:
        logger.info("OpenAlex configured with email: %s (polite pool)", _OPENALEX_EMAIL)
    else:
        logger.debug("OpenAlex using anonymous access (lower rate limits)")


def _build_config(
    *,
    openalex_api_key: str | None = None,
    openalex_email: str | None = None,
    semantic_scholar_api_key: str | None = None,
    pubmed_api_key: str | None = None,
) -> Config:
    """Build an opencite Config from explicit args and configure_openalex().

    Credentials come from OSA settings (passed explicitly) with a fallback to
    values set via configure_openalex(). We construct Config directly rather
    than Config.from_env() so paper sync never depends on ambient ``.env``
    files in the working directory, which are environment-specific and have
    tripped opencite's dotenv loader.
    """
    return Config(
        openalex_api_key=openalex_api_key or _OPENALEX_API_KEY or "",
        contact_email=openalex_email or _OPENALEX_EMAIL or "",
        semantic_scholar_api_key=semantic_scholar_api_key or "",
        pubmed_api_key=pubmed_api_key or "",
    )


def _native_id(paper: Paper, osa_source: str) -> str:
    """Return the identifier matching a specific OSA source label, or ''."""
    ids = paper.ids
    if osa_source == "openalex":
        return ids.openalex_id.removeprefix("https://openalex.org/") if ids.openalex_id else ""
    if osa_source == "semanticscholar":
        return ids.s2_id or ""
    if osa_source == "pubmed":
        return ids.pmid or ""
    return ""


def _paper_source_and_id(paper: Paper) -> tuple[str | None, str | None]:
    """Pick a stable (source, external_id) for the papers table.

    Prefers identifiers in the order OpenAlex > Semantic Scholar > PubMed > DOI
    > arXiv so a paper maps to the same row across syncs and aligns with rows
    already stored from the previous per-source fetchers. Returns (None, None)
    when no usable identifier is present (such papers are skipped).
    """
    ids = paper.ids
    openalex = ids.openalex_id.removeprefix("https://openalex.org/") if ids.openalex_id else ""
    if openalex:
        return "openalex", openalex
    if ids.s2_id:
        return "semanticscholar", ids.s2_id
    if ids.pmid:
        return "pubmed", ids.pmid
    if ids.doi:
        return "doi", ids.doi.lower()
    if ids.arxiv_id:
        return "arxiv", ids.arxiv_id
    return None, None


def _paper_url(paper: Paper) -> str:
    """Best link for a paper, preferring a stable DOI landing page."""
    if paper.doi:
        return f"https://doi.org/{paper.doi}"
    if paper.url:
        return paper.url
    if paper.best_pdf_url:
        return paper.best_pdf_url
    return ""


def _store_papers(
    papers: Iterable[Paper],
    project: str,
    *,
    force_source: str | None = None,
) -> dict[str, int]:
    """Upsert opencite papers into the knowledge DB, returning counts by source.

    Args:
        papers: opencite Paper objects to store.
        project: Community/project ID for database isolation.
        force_source: When set (a single-source sync), record this OSA source
            label using its native identifier; falls back to the priority
            mapping if that identifier is missing.
    """
    counts: dict[str, int] = {}
    with get_connection(project) as conn:
        for paper in papers:
            if not paper.title:
                continue

            if force_source:
                external_id = _native_id(paper, force_source)
                source: str | None = force_source if external_id else None
                if not source:
                    source, external_id = _paper_source_and_id(paper)
            else:
                source, external_id = _paper_source_and_id(paper)

            if not source or not external_id:
                continue

            upsert_paper(
                conn,
                source=source,
                external_id=external_id,
                title=paper.title,
                first_message=paper.abstract or None,
                url=_paper_url(paper),
                created_at=paper.publication_date or (str(paper.year) if paper.year else None),
            )
            counts[source] = counts.get(source, 0) + 1
        conn.commit()
    return counts


async def _search_papers(
    config: Config,
    query: str,
    max_results: int,
    sources: tuple[str, ...] | None,
) -> list[Paper]:
    """Run an opencite multi-source search and return the deduplicated papers."""
    async with SearchOrchestrator(config) as searcher:
        result = await searcher.search(query, max_results=max_results, sources=sources)
        return result.papers


async def _citing_papers(config: Config, identifier: str, max_results: int) -> list[Paper]:
    """Return papers citing the given identifier via opencite."""
    async with CitationExplorer(config) as explorer:
        result = await explorer.citing_papers(identifier, max_results=max_results)
        return result.papers


def _sync_single_source(
    query: str,
    max_results: int,
    project: str,
    osa_source: str,
    config: Config,
) -> int:
    """Sync papers for one source (restricted opencite search) into the DB."""
    opencite_source = _OPENCITE_SOURCE_BY_OSA[osa_source]
    try:
        papers = asyncio.run(_search_papers(config, query, max_results, (opencite_source,)))
    except Exception as e:
        logger.warning("opencite %s search error for '%s': %s", osa_source, query, e)
        return 0

    counts = _store_papers(papers, project, force_source=osa_source)
    count = sum(counts.values())
    logger.info("Synced %d papers from %s for '%s'", count, osa_source, query)
    update_sync_metadata("papers", f"{osa_source}:{query}", count, project)
    return count


def sync_openalex_papers(query: str, max_results: int = 100, project: str = "hed") -> int:
    """Sync papers from OpenAlex matching query (via opencite)."""
    logger.info("Syncing OpenAlex papers for query: %s", query)
    return _sync_single_source(query, max_results, project, "openalex", _build_config())


def sync_semanticscholar_papers(
    query: str,
    max_results: int = 100,
    api_key: str | None = None,
    project: str = "hed",
) -> int:
    """Sync papers from Semantic Scholar matching query (via opencite)."""
    logger.info("Syncing Semantic Scholar papers for query: %s", query)
    config = _build_config(semantic_scholar_api_key=api_key)
    return _sync_single_source(query, max_results, project, "semanticscholar", config)


def sync_pubmed_papers(
    query: str,
    max_results: int = 100,
    api_key: str | None = None,
    project: str = "hed",
) -> int:
    """Sync papers from PubMed matching query (via opencite)."""
    logger.info("Syncing PubMed papers for query: %s", query)
    config = _build_config(pubmed_api_key=api_key)
    return _sync_single_source(query, max_results, project, "pubmed", config)


def sync_all_papers(
    queries: list[str] | None = None,
    max_results: int = 100,
    semantic_scholar_api_key: str | None = None,
    pubmed_api_key: str | None = None,
    openalex_api_key: str | None = None,
    openalex_email: str | None = None,
    project: str = "hed",
) -> dict[str, int]:
    """Sync papers from all default sources for given queries via opencite.

    A single deduplicated opencite search runs per query across
    ``DEFAULT_SOURCES``, replacing the previous three sequential per-source
    fetches.

    Args:
        queries: List of search queries (required - no default queries).
        max_results: Max deduplicated results per query.
        semantic_scholar_api_key: Optional Semantic Scholar API key.
        pubmed_api_key: Optional PubMed/NCBI API key.
        openalex_api_key: Optional OpenAlex API key for premium access.
        openalex_email: Optional email for OpenAlex polite pool.
        project: Project/community ID for database isolation.

    Returns:
        Dict mapping OSA source label to total papers synced.
    """
    if isinstance(queries, str):
        raise TypeError(f"queries must be a list of strings, not a bare string: {queries!r}")
    if not queries:
        logger.warning("No queries provided for paper sync")
        return {"openalex": 0, "semanticscholar": 0, "pubmed": 0}

    config = _build_config(
        openalex_api_key=openalex_api_key,
        openalex_email=openalex_email,
        semantic_scholar_api_key=semantic_scholar_api_key,
        pubmed_api_key=pubmed_api_key,
    )

    results: dict[str, int] = {"openalex": 0, "semanticscholar": 0, "pubmed": 0}
    for query in queries:
        try:
            papers = asyncio.run(_search_papers(config, query, max_results, DEFAULT_SOURCES))
        except Exception as e:
            logger.warning("opencite search error for '%s': %s", query, e)
            continue

        counts = _store_papers(papers, project)
        for source, n in counts.items():
            results[source] = results.get(source, 0) + n
        update_sync_metadata("papers", f"opencite:{query}", sum(counts.values()), project)

    total = sum(results.values())
    logger.info("Total papers synced for %s: %d", project, total)
    return results


def sync_citing_papers(
    dois: list[str],
    max_results: int = 100,
    project: str = "hed",
    openalex_api_key: str | None = None,
    openalex_email: str | None = None,
) -> int:
    """Sync papers that cite the given DOIs using opencite's citation graph.

    Args:
        dois: List of DOIs to find citations for. Bare format preferred
            (e.g. "10.1016/j.neuroimage.2021.118809"); opencite auto-detects
            and resolves the identifier. Unresolved DOIs are skipped with a
            warning.
        max_results: Maximum number of citing papers per DOI.
        project: Project/community ID for database isolation.
        openalex_api_key: Optional OpenAlex API key for premium access.
        openalex_email: Optional email for OpenAlex polite pool.

    Returns:
        Total number of citing papers synced.
    """
    if isinstance(dois, str):
        raise TypeError(f"dois must be a list of strings, not a bare string: {dois!r}")

    config = _build_config(openalex_api_key=openalex_api_key, openalex_email=openalex_email)
    total = 0

    for doi in dois:
        logger.info("Syncing papers citing DOI: %s", doi)
        try:
            papers = asyncio.run(_citing_papers(config, doi, max_results))
        except Exception as e:
            logger.warning("opencite citation error for DOI %s: %s", doi, e)
            continue

        counts = _store_papers(papers, project)
        count = sum(counts.values())
        update_sync_metadata("papers", f"citing_{doi}", count, project)
        logger.info("Synced %d papers citing %s", count, doi)
        total += count

    return total
