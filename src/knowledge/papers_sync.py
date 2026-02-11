"""Paper sync from OpenALEX, Semantic Scholar, and PubMed Central.

Syncs papers for community-configured search queries.
Only stores title, abstract snippet, URL, and publication date.

Rate limits:
- OpenALEX: No key required, generous limits
- Semantic Scholar: ~100 requests/5 min (free), higher with API key
- PubMed: ~3 requests/sec without key, 10/sec with key
"""

import logging
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx
import pyalex
from pyalex import Works

from src.knowledge.db import get_connection, update_sync_metadata, upsert_paper

logger = logging.getLogger(__name__)

# Rate limiting settings
SEMANTIC_SCHOLAR_DELAY = 3.0  # seconds between requests (to stay under 100/5min)
PUBMED_DELAY = 0.4  # seconds between requests (to stay under 3/sec)


def configure_openalex(api_key: str | None = None, email: str | None = None) -> None:
    """Configure pyalex with API key or email for polite pool access.

    Args:
        api_key: OpenAlex API key for premium access (~2M requests).
        email: Email for polite pool access (faster than anonymous).
    """
    # Treat empty strings as None
    api_key = api_key.strip() if api_key else None
    email = email.strip() if email else None

    if api_key:
        pyalex.config.api_key = api_key
        logger.info("OpenAlex configured with API key")
    elif email:
        pyalex.config.email = email
        logger.info("OpenAlex configured with email: %s (polite pool)", email)
    else:
        logger.debug("OpenAlex using anonymous access (lower rate limits)")


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Reconstruct abstract from OpenALEX inverted index format.

    OpenALEX stores abstracts as inverted indexes: {"word": [positions]}
    This function reconstructs the original text.

    Args:
        inverted_index: Dict mapping words to their positions

    Returns:
        Reconstructed abstract text
    """
    if not inverted_index:
        return ""

    # Find max position to size the array
    max_pos = 0
    for positions in inverted_index.values():
        if positions:
            max_pos = max(max_pos, max(positions))

    # Build word array
    words = [""] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word

    return " ".join(words)


def _get_paper_url(doi: str | None, fallback_id: str) -> str:
    """Get paper URL, preferring DOI when available.

    Args:
        doi: The DOI (may be full URL or bare DOI)
        fallback_id: Fallback URL/ID if no DOI

    Returns:
        URL string
    """
    if not doi:
        return fallback_id
    return doi if doi.startswith("http") else f"https://doi.org/{doi}"


def _get_openalex_external_id(openalex_id: str) -> str:
    """Extract external ID from OpenALEX URL.

    Args:
        openalex_id: Full OpenALEX URL or bare ID

    Returns:
        Bare external ID (e.g., "W12345")
    """
    return openalex_id.removeprefix("https://openalex.org/")


def sync_openalex_papers(query: str, max_results: int = 100, project: str = "hed") -> int:
    """Sync papers from OpenALEX matching query.

    Args:
        query: Search query
        max_results: Maximum number of papers to sync
        project: Assistant/project name for database isolation. Defaults to 'hed'.

    Returns:
        Number of papers synced
    """
    logger.info("Syncing OpenALEX papers for query: %s", query)

    try:
        # Build query and fetch results
        # pyalex returns a lazy query object, need to call .get() to fetch results
        works_query = (
            Works()
            .search(query)
            .select(
                [
                    "id",
                    "title",
                    "abstract_inverted_index",
                    "publication_date",
                    "doi",
                    "primary_location",
                ]
            )
        )
        # Fetch up to max_results using pagination
        works = list(works_query.get(per_page=min(max_results, 200)))
    except Exception as e:
        logger.warning("OpenALEX error for '%s': %s", query, e)
        return 0

    count = 0
    with get_connection(project) as conn:
        for work in works:
            if count >= max_results:
                break

            # Skip if no title
            title = work.get("title")
            if not title:
                continue

            abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
            url = _get_paper_url(work.get("doi"), work.get("id", ""))
            external_id = _get_openalex_external_id(work.get("id", ""))

            upsert_paper(
                conn,
                source="openalex",
                external_id=external_id,
                title=title,
                first_message=abstract,
                url=url,
                created_at=work.get("publication_date"),
            )
            count += 1

        conn.commit()

    logger.info("Synced %d papers from OpenALEX for '%s'", count, query)
    update_sync_metadata("papers", f"openalex:{query}", count, project)
    return count


def sync_semanticscholar_papers(
    query: str,
    max_results: int = 100,
    api_key: str | None = None,
    project: str = "hed",
) -> int:
    """Sync papers from Semantic Scholar matching query.

    Args:
        query: Search query
        max_results: Maximum number of papers to sync
        api_key: Optional API key for higher rate limits
        project: Assistant/project name for database isolation. Defaults to 'hed'.

    Returns:
        Number of papers synced
    """
    logger.info("Syncing Semantic Scholar papers for query: %s", query)

    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params: dict[str, Any] = {
        "query": query,
        "limit": min(max_results, 100),  # API limit per request
        "fields": "paperId,title,abstract,year,url,openAccessPdf",
    }

    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=30.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        logger.warning("Semantic Scholar HTTP error for '%s': %s", query, e)
        return 0
    except httpx.RequestError as e:
        logger.warning("Semantic Scholar request error for '%s': %s", query, e)
        return 0

    count = 0
    with get_connection(project) as conn:
        for paper in data.get("data", []):
            if count >= max_results:
                break

            # Skip if no title
            title = paper.get("title")
            if not title:
                continue

            paper_id = paper.get("paperId", "")
            paper_url = paper.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}"

            # Prefer open access PDF URL if available
            open_access = paper.get("openAccessPdf")
            if open_access and open_access.get("url"):
                paper_url = open_access["url"]

            upsert_paper(
                conn,
                source="semanticscholar",
                external_id=paper_id,
                title=title,
                first_message=paper.get("abstract"),
                url=paper_url,
                created_at=str(paper.get("year")) if paper.get("year") else None,
            )
            count += 1

        conn.commit()

    logger.info("Synced %d papers from Semantic Scholar for '%s'", count, query)
    update_sync_metadata("papers", f"semanticscholar:{query}", count, project)

    # Rate limiting
    time.sleep(SEMANTIC_SCHOLAR_DELAY)
    return count


def sync_pubmed_papers(
    query: str,
    max_results: int = 100,
    api_key: str | None = None,
    project: str = "hed",
) -> int:
    """Sync papers from PubMed matching query.

    Uses NCBI E-utilities API (esearch + efetch).

    Args:
        query: Search query
        max_results: Maximum number of papers to sync
        api_key: Optional NCBI API key for higher rate limits
        project: Assistant/project name for database isolation. Defaults to 'hed'.

    Returns:
        Number of papers synced
    """
    logger.info("Syncing PubMed papers for query: %s", query)

    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    # Step 1: Search for paper IDs
    search_params: dict[str, Any] = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
    }
    if api_key:
        search_params["api_key"] = api_key

    try:
        search_response = httpx.get(f"{base_url}/esearch.fcgi", params=search_params, timeout=30.0)
        search_response.raise_for_status()
        search_data = search_response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.warning("PubMed search error for '%s': %s", query, e)
        return 0

    id_list = search_data.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        logger.info("No PubMed results for '%s'", query)
        return 0

    # Rate limiting between requests
    time.sleep(PUBMED_DELAY)

    # Step 2: Fetch paper details
    fetch_params: dict[str, Any] = {
        "db": "pubmed",
        "id": ",".join(id_list),
        "retmode": "xml",
    }
    if api_key:
        fetch_params["api_key"] = api_key

    try:
        fetch_response = httpx.get(f"{base_url}/efetch.fcgi", params=fetch_params, timeout=60.0)
        fetch_response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.warning("PubMed fetch error for '%s': %s", query, e)
        return 0

    # Parse XML response
    try:
        root = ET.fromstring(fetch_response.text)
    except ET.ParseError as e:
        logger.warning("PubMed XML parse error for '%s': %s", query, e)
        return 0

    count = 0
    with get_connection(project) as conn:
        for article in root.findall(".//PubmedArticle"):
            pmid_elem = article.find(".//PMID")
            title_elem = article.find(".//ArticleTitle")
            abstract_elem = article.find(".//AbstractText")
            year_elem = article.find(".//PubDate/Year")

            if pmid_elem is None or title_elem is None:
                continue

            pmid = pmid_elem.text or ""
            title = title_elem.text or ""
            abstract = abstract_elem.text if abstract_elem is not None else None
            year = year_elem.text if year_elem is not None else None

            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

            upsert_paper(
                conn,
                source="pubmed",
                external_id=pmid,
                title=title,
                first_message=abstract,
                url=url,
                created_at=year,
            )
            count += 1

        conn.commit()

    logger.info("Synced %d papers from PubMed for '%s'", count, query)
    update_sync_metadata("papers", f"pubmed:{query}", count, project)

    # Rate limiting
    time.sleep(PUBMED_DELAY)
    return count


def sync_all_papers(
    queries: list[str] | None = None,
    max_results: int = 100,
    semantic_scholar_api_key: str | None = None,
    pubmed_api_key: str | None = None,
    openalex_api_key: str | None = None,
    openalex_email: str | None = None,
    project: str = "hed",
) -> dict[str, int]:
    """Sync papers from all sources for given queries.

    Args:
        queries: List of search queries (required - no default queries)
        max_results: Max results per query per source
        semantic_scholar_api_key: Optional Semantic Scholar API key
        pubmed_api_key: Optional PubMed/NCBI API key
        openalex_api_key: Optional OpenAlex API key for premium access
        openalex_email: Optional email for OpenAlex polite pool
        project: Project/community ID for database isolation

    Returns:
        Dict mapping source to total items synced
    """
    if not queries:
        logger.warning("No queries provided for paper sync")
        return {"openalex": 0, "semanticscholar": 0, "pubmed": 0}

    # Configure OpenAlex with API key or email if provided
    configure_openalex(api_key=openalex_api_key, email=openalex_email)

    results = {
        "openalex": 0,
        "semanticscholar": 0,
        "pubmed": 0,
    }

    for query in queries:
        results["openalex"] += sync_openalex_papers(query, max_results, project=project)
        results["semanticscholar"] += sync_semanticscholar_papers(
            query, max_results, semantic_scholar_api_key, project=project
        )
        results["pubmed"] += sync_pubmed_papers(query, max_results, pubmed_api_key, project=project)

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
    """Sync papers that cite the given DOIs using OpenALEX.

    OpenALEX supports finding papers that cite a specific work via
    the `cites` filter. This is useful for tracking citations to
    foundational papers in a field.

    Args:
        dois: List of DOIs to find citations for. Should be in bare format
            (e.g., "10.1016/j.neuroimage.2021.118809") without the
            https://doi.org/ prefix. Invalid or unfound DOIs are skipped
            with a warning log.
        max_results: Maximum number of citing papers per DOI
        project: Project/community ID for database isolation
        openalex_api_key: Optional OpenAlex API key for premium access
        openalex_email: Optional email for OpenAlex polite pool

    Returns:
        Total number of citing papers synced
    """
    configure_openalex(api_key=openalex_api_key, email=openalex_email)
    total = 0

    for doi in dois:
        logger.info("Syncing papers citing DOI: %s", doi)

        try:
            # First, look up the OpenALEX work ID for this DOI
            work_lookup = Works()[f"https://doi.org/{doi}"]
            openalex_id = work_lookup.get("id")

            if not openalex_id:
                logger.warning("Could not find OpenALEX ID for DOI %s", doi)
                continue

            logger.debug("Found OpenALEX ID %s for DOI %s", openalex_id, doi)

            # Now find papers that cite this work using the OpenALEX ID
            works_query = (
                Works()
                .filter(cites=openalex_id)
                .select(
                    [
                        "id",
                        "title",
                        "abstract_inverted_index",
                        "publication_date",
                        "doi",
                        "primary_location",
                    ]
                )
            )
            works = list(works_query.get(per_page=min(max_results, 200)))
        except Exception as e:
            logger.warning("OpenALEX citation error for DOI %s: %s", doi, e)
            continue

        count = 0
        with get_connection(project) as conn:
            for work in works:
                if count >= max_results:
                    break

                title = work.get("title")
                if not title:
                    continue

                abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
                url = _get_paper_url(work.get("doi"), work.get("id", ""))
                external_id = _get_openalex_external_id(work.get("id", ""))

                upsert_paper(
                    conn,
                    source="openalex",
                    external_id=external_id,
                    title=title,
                    first_message=abstract,
                    url=url,
                    created_at=work.get("publication_date"),
                )
                count += 1

            conn.commit()

        # Update sync metadata with citing_ prefix to distinguish from query-based syncs
        update_sync_metadata("papers", f"citing_{doi}", count, project)
        logger.info("Synced %d papers citing %s", count, doi)
        total += count

    return total
