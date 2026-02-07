"""NEMAR-specific tools for dataset discovery and exploration.

These tools query the NEMAR public API to help researchers find and
explore BIDS-formatted EEG/MEG/iEEG datasets from OpenNeuro.

- search_nemar_datasets: Search/filter datasets by text, modality, task, etc.
- get_nemar_dataset_details: Get full metadata for a specific dataset by ID

The NEMAR API has no server-side search, so search_nemar_datasets fetches
all ~485 datasets and filters client-side. This is fast enough given the
small dataset count (<2s for full fetch).
"""

import logging
from typing import Any

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

NEMAR_API_BASE = "https://nemar.org/api/dataexplorer/datapipeline"
TABLE_NAME = "dataexplorer_dataset"
NEMAR_SEP = "===NEMAR-SEP==="


def _fetch_all_datasets() -> list[dict[str, Any]]:
    """Fetch all datasets from NEMAR API.

    Returns:
        List of dataset dicts sorted by dataset ID.

    Raises:
        httpx.HTTPError: If the API request fails.
    """
    url = f"{NEMAR_API_BASE}/records"
    payload = {"table_name": TABLE_NAME, "start": 0, "limit": 1000}

    # NEMAR API uses GET with JSON body (unusual but required)
    response = httpx.request("GET", url, json=payload, timeout=30.0)
    response.raise_for_status()
    data = response.json()

    entries = data.get("entries", {})
    # entries is a dict with string indices: {"0": {...}, "1": {...}, ...}
    datasets = [entries[k] for k in sorted(entries.keys(), key=int)]
    return datasets


def _parse_sep_field(value: str) -> list[str]:
    """Split a NEMAR multi-value field using the ===NEMAR-SEP=== delimiter."""
    if not value:
        return []
    parts = value.split(NEMAR_SEP)
    return [p.strip() for p in parts if p.strip()]


def _matches(
    dataset: dict[str, Any],
    query: str | None,
    modality_filter: str | None,
    task_filter: str | None,
    has_hed: bool | None,
    min_participants: int | None,
) -> bool:
    """Check if a dataset matches all provided filters."""
    if query:
        q = query.lower()
        searchable = " ".join(
            [
                str(dataset.get("name", "")),
                str(dataset.get("tasks", "")),
                str(dataset.get("readme", "")),
                str(dataset.get("Authors", "")),
            ]
        ).lower()
        if q not in searchable:
            return False

    if modality_filter:
        modalities = str(dataset.get("modalities", "")).lower()
        if modality_filter.lower() not in modalities:
            return False

    if task_filter:
        tasks = str(dataset.get("tasks", "")).lower()
        if task_filter.lower() not in tasks:
            return False

    if has_hed is True and dataset.get("hedAnnotation") != 1:
        return False

    if min_participants is not None:
        participants = dataset.get("participants", 0) or 0
        if participants < min_participants:
            return False

    return True


def _format_summary(dataset: dict[str, Any]) -> str:
    """Format a compact one-line summary for search results."""
    ds_id = dataset.get("id", "unknown")
    name = dataset.get("name", ds_id)
    modalities = dataset.get("modalities", "N/A") or "N/A"
    tasks = dataset.get("tasks", "N/A") or "N/A"
    participants = dataset.get("participants", 0) or 0
    size = dataset.get("byte_size_format", "unknown") or "unknown"

    # Truncate long names
    if len(name) > 80:
        name = name[:77] + "..."

    return (
        f"- **{ds_id}** - {name}\n"
        f"  Modalities: {modalities} | Tasks: {tasks} | "
        f"Participants: {participants} | Size: {size}"
    )


@tool
def search_nemar_datasets(
    query: str | None = None,
    modality_filter: str | None = None,
    task_filter: str | None = None,
    has_hed: bool | None = None,
    min_participants: int | None = None,
    limit: int = 20,
) -> str:
    """Search NEMAR datasets with flexible text search and filtering.

    Fetches all datasets from NEMAR and filters client-side. Returns compact
    summaries suitable for browsing. Use get_nemar_dataset_details for full info.

    Args:
        query: Text search across dataset names, tasks, README, and authors
            (case-insensitive substring match). Example: "attention", "face", "motor".
        modality_filter: Filter by recording modality. Use one of: "EEG", "MEG",
            "iEEG", "MRI" (partial match, case-insensitive).
        task_filter: Filter by experimental task name (partial match,
            case-insensitive). Example: "rest", "gonogo", "memory".
        has_hed: If True, only return datasets with HED annotations.
        min_participants: Minimum number of participants required.
        limit: Maximum results to return (default: 20, max: 50).

    Returns:
        Formatted markdown string with matching dataset summaries.
    """
    limit = min(limit, 50)

    try:
        datasets = _fetch_all_datasets()
    except httpx.HTTPError as e:
        logger.warning("NEMAR API error: %s", e)
        return f"Failed to fetch datasets from NEMAR: {e}"
    except Exception as e:
        logger.exception("Unexpected error fetching NEMAR datasets")
        return f"Failed to fetch datasets: {e}"

    # Apply filters
    matched = [
        ds
        for ds in datasets
        if _matches(ds, query, modality_filter, task_filter, has_hed, min_participants)
    ]

    total_matched = len(matched)
    if total_matched == 0:
        filters_desc = []
        if query:
            filters_desc.append(f'query="{query}"')
        if modality_filter:
            filters_desc.append(f"modality={modality_filter}")
        if task_filter:
            filters_desc.append(f"task={task_filter}")
        if has_hed:
            filters_desc.append("has_hed=True")
        if min_participants:
            filters_desc.append(f"min_participants={min_participants}")
        return f"No datasets found matching: {', '.join(filters_desc)}. Total datasets in NEMAR: {len(datasets)}."

    # Cap results
    shown = matched[:limit]

    lines = [f"Found **{total_matched}** matching datasets (showing {len(shown)}):\n"]
    for ds in shown:
        lines.append(_format_summary(ds))

    if total_matched > limit:
        lines.append(
            f"\n*{total_matched - limit} more results not shown. Narrow your search or increase limit.*"
        )

    return "\n".join(lines)


@tool
def get_nemar_dataset_details(dataset_id: str) -> str:
    """Get comprehensive metadata for a specific NEMAR dataset.

    Retrieves full information including description, citation, licensing,
    experimental details, and README content.

    Args:
        dataset_id: Dataset identifier, e.g. "ds000248" or "ds005697".

    Returns:
        Formatted markdown string with complete dataset information,
        including OpenNeuro link, DOI, authors, license, and README.
    """
    url = f"{NEMAR_API_BASE}/datasetid"
    payload = {"table_name": TABLE_NAME, "dataset_id": dataset_id}

    try:
        # NEMAR API uses GET with JSON body (unusual but required)
        response = httpx.request("GET", url, json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as e:
        logger.warning("NEMAR API error for dataset %s: %s", dataset_id, e)
        return f"Failed to fetch dataset {dataset_id} from NEMAR: {e}"
    except Exception as e:
        logger.exception("Unexpected error fetching NEMAR dataset %s", dataset_id)
        return f"Failed to fetch dataset {dataset_id}: {e}"

    entry = data.get("entry", {})
    if not entry:
        return f"Dataset '{dataset_id}' not found on NEMAR."

    # entry is {"0": {...}} for single results
    ds = next(iter(entry.values()))

    ds_id = ds.get("id", dataset_id)
    name = ds.get("name", ds_id)
    openneuro_url = f"https://openneuro.org/datasets/{ds_id}"
    nemar_url = f"https://nemar.org/dataexplorer/detail?dataset_id={ds_id}"

    # Build formatted output
    lines = [
        f"# {name}",
        "",
        f"**Dataset ID:** {ds_id}",
        f"**NEMAR:** {nemar_url}",
        f"**OpenNeuro:** {openneuro_url}",
    ]

    doi = ds.get("DatasetDOI", "")
    if doi:
        lines.append(f"**DOI:** {doi}")

    lines.append("")

    # Authors
    authors = ds.get("Authors", "")
    if authors:
        # Authors can be ===NEMAR-SEP=== or comma-separated
        if NEMAR_SEP in authors:
            author_list = _parse_sep_field(authors)
            lines.append(f"**Authors:** {', '.join(author_list)}")
        else:
            lines.append(f"**Authors:** {authors}")

    # License
    license_val = ds.get("License", "")
    if license_val:
        lines.append(f"**License:** {license_val}")

    # BIDS version
    bids_ver = ds.get("BIDSVersion", "")
    if bids_ver:
        lines.append(f"**BIDS Version:** {bids_ver}")

    lines.append("")

    # Data characteristics
    lines.append("## Data Characteristics")
    lines.append("")
    modalities = ds.get("modalities", "N/A") or "N/A"
    tasks = ds.get("tasks", "N/A") or "N/A"
    participants = ds.get("participants", 0) or 0
    sessions = ds.get("sessionsNum", 0) or 0
    total_files = ds.get("totalFiles", 0) or 0
    size = ds.get("byte_size_format", "unknown") or "unknown"
    age_min = ds.get("age_min", 0) or 0
    age_max = ds.get("age_max", 0) or 0

    lines.append(f"- **Modalities:** {modalities}")
    lines.append(f"- **Tasks:** {tasks}")
    lines.append(f"- **Participants:** {participants}")
    lines.append(f"- **Sessions:** {sessions}")
    lines.append(f"- **Total files:** {total_files}")
    lines.append(f"- **Size:** {size}")

    if age_min or age_max:
        lines.append(f"- **Age range:** {age_min}-{age_max}")

    # HED annotation
    hed_ver = ds.get("HEDVersion", "")
    has_hed_annotation = ds.get("hedAnnotation", 0) == 1
    if has_hed_annotation and hed_ver:
        lines.append(f"- **HED annotations:** Yes (version {hed_ver})")
    elif has_hed_annotation:
        lines.append("- **HED annotations:** Yes")
    else:
        lines.append("- **HED annotations:** No")

    # Version info
    snapshot = ds.get("latestSnapshot", "")
    if snapshot:
        lines.append(f"- **Latest version:** {snapshot}")

    # References and links
    refs = ds.get("ReferencesAndLinks", "")
    if refs:
        ref_list = _parse_sep_field(refs)
        if ref_list:
            lines.append("")
            lines.append("## References")
            for ref in ref_list:
                lines.append(f"- {ref}")

    # Funding
    funding = ds.get("Funding", "")
    if funding:
        fund_list = _parse_sep_field(funding)
        if fund_list:
            lines.append("")
            lines.append("## Funding")
            for f in fund_list:
                lines.append(f"- {f}")

    # Acknowledgements
    ack = ds.get("Acknowledgements", "")
    if ack:
        lines.append("")
        lines.append(f"## Acknowledgements\n\n{ack}")

    # How to acknowledge
    how_to_ack = ds.get("HowToAcknowledge", "")
    if how_to_ack:
        lines.append("")
        lines.append(f"## How to Acknowledge\n\n{how_to_ack}")

    # README (truncated)
    readme = ds.get("readme", "")
    if readme:
        lines.append("")
        lines.append("## README")
        lines.append("")
        if len(readme) > 1500:
            lines.append(readme[:1500] + "\n\n*[README truncated; see OpenNeuro for full text]*")
        else:
            lines.append(readme)

    return "\n".join(lines)


__all__ = ["search_nemar_datasets", "get_nemar_dataset_details"]
