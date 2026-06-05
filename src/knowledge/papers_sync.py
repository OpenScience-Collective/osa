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
import os
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from opencite import Config, Paper
from opencite.citations import CitationExplorer
from opencite.search import SearchOrchestrator

from src.knowledge.db import get_connection, update_sync_metadata, upsert_paper
from src.knowledge.search import SearchResult

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


def _run(coro):
    """Execute an async coroutine from synchronous code.

    OSA's sync callers (CLI command, scheduler thread) have no running event
    loop, so asyncio.run is used directly. If a loop is already running in the
    calling thread, the coroutine runs in a dedicated worker thread so these
    public sync functions stay safe to call from any context.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _search_queries(
    config: Config,
    queries: list[str],
    max_results: int,
    sources: tuple[str, ...] | None,
) -> list[tuple[str, list[Paper]]]:
    """Search every query through one shared opencite orchestrator.

    A single orchestrator (and its HTTP client pool) is opened for the whole
    batch. A failure for an individual query is logged and yields an empty
    result for that query rather than aborting the batch.
    """
    out: list[tuple[str, list[Paper]]] = []
    async with SearchOrchestrator(config) as searcher:
        for query in queries:
            try:
                result = await searcher.search(query, max_results=max_results, sources=sources)
                out.append((query, result.papers))
            except Exception as e:
                logger.warning("opencite search error for '%s': %s", query, e)
                out.append((query, []))
    return out


async def _citing_for_dois(
    config: Config,
    dois: list[str],
    max_results: int,
) -> list[tuple[str, list[Paper]]]:
    """Fetch citing papers for every DOI through one shared CitationExplorer."""
    out: list[tuple[str, list[Paper]]] = []
    async with CitationExplorer(config) as explorer:
        for doi in dois:
            try:
                result = await explorer.citing_papers(doi, max_results=max_results)
                out.append((doi, result.papers))
            except Exception as e:
                logger.warning("opencite citation error for DOI %s: %s", doi, e)
                out.append((doi, []))
    return out


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
        searched = _run(_search_queries(config, [query], max_results, (opencite_source,)))
    except Exception as e:
        logger.warning("opencite %s search failed for '%s': %s", osa_source, query, e)
        return 0

    _, papers = searched[0]
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
    try:
        searched = _run(_search_queries(config, queries, max_results, DEFAULT_SOURCES))
    except Exception as e:
        logger.warning("opencite search failed for %s: %s", project, e)
        return results

    for query, papers in searched:
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
    try:
        cited = _run(_citing_for_dois(config, dois, max_results))
    except Exception as e:
        logger.warning("opencite citation lookup failed for %s: %s", project, e)
        return 0

    total = 0
    for doi, papers in cited:
        counts = _store_papers(papers, project)
        count = sum(counts.values())
        update_sync_metadata("papers", f"citing_{doi}", count, project)
        logger.info("Synced %d papers citing %s", count, doi)
        total += count

    return total


def _config_from_env() -> Config:
    """Build an opencite Config from the server's configured API-key env vars.

    Reads the same variables OSA settings use. Missing keys fall back to
    anonymous access (fine for a single on-demand query). Specific env vars
    are read by name rather than via Config.from_env() to avoid the ambient
    ``.env`` parsing path.
    """
    return _build_config(
        openalex_api_key=os.environ.get("OPENALEX_API_KEY"),
        openalex_email=os.environ.get("OPENALEX_EMAIL"),
        semantic_scholar_api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
        pubmed_api_key=os.environ.get("PUBMED_API_KEY"),
    )


def _paper_to_result(paper: Paper) -> SearchResult:
    """Convert an opencite Paper to the shared SearchResult shape."""
    source, _ = _paper_source_and_id(paper)
    return SearchResult(
        title=paper.title,
        url=_paper_url(paper),
        snippet=paper.abstract or "",
        source=source or "opencite",
        item_type=None,
        status="published",
        created_at=str(paper.year) if paper.year else "",
    )


async def _search_recent(
    config: Config,
    query: str,
    limit: int,
    timeout: float,
) -> list[Paper]:
    """Live opencite search for the most recent papers, bounded by a timeout."""
    async with SearchOrchestrator(config) as searcher:
        result = await asyncio.wait_for(
            searcher.search(query, max_results=limit, sources=DEFAULT_SOURCES, sort="year"),
            timeout=timeout,
        )
        return result.papers


def search_papers_live(
    query: str,
    project: str = "hed",
    limit: int = 5,
    cache: bool = True,
    timeout: float = 20.0,
) -> list[SearchResult]:
    """Search the live literature via opencite for the most recent papers.

    Unlike :func:`src.knowledge.search.search_papers` (local FTS over already
    synced rows), this hits opencite's multi-source APIs for fresh results,
    newest first. This is for on-demand discovery of papers the batch sync has
    not picked up yet.

    Args:
        query: Topic to search for.
        project: Community/project ID (for caching into the right DB).
        limit: Maximum number of papers to return.
        cache: When True (default), best-effort upsert the results into the
            community knowledge DB so future local searches find them.
        timeout: Hard cap (seconds) on the opencite call to keep chat snappy.

    Returns:
        List of SearchResult, newest first. Empty on timeout/error.
    """
    config = _config_from_env()
    try:
        papers = _run(_search_recent(config, query, limit, timeout))
    except TimeoutError:
        logger.warning("opencite live search timed out for '%s' after %.0fs", query, timeout)
        return []
    except Exception as e:
        logger.warning("opencite live search failed for '%s': %s", query, e)
        return []

    if cache and papers:
        try:
            _store_papers(papers, project)
        except Exception as e:
            logger.warning("Failed to cache live search papers for %s: %s", project, e)

    return [_paper_to_result(p) for p in papers[:limit]]
