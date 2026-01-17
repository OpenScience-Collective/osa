"""HED knowledge sync configuration.

Defines the repositories and queries to sync for the HED assistant.
This configuration is used by the CLI sync commands.
"""

# HED-specific GitHub repositories to sync
HED_REPOS = [
    "hed-standard/hed-specification",
    "hed-standard/hed-javascript",
    "hed-standard/hed-schemas",
]

# Paper search queries for HED
HED_PAPER_QUERIES = [
    "HED annotation",
    "Hierarchical Event Descriptors",
    "HED neuroimaging",
]

# Core HED papers to track citations for
# These are foundational HED papers; we sync papers that cite them
HED_PAPER_DOIS = [
    "10.1016/j.neuroimage.2021.118766",  # HED: An online system for creating and searching
    "10.1007/s12021-023-09628-4",  # The HED annotation and event framework
    "10.3389/fninf.2024.1292667",  # HED SCORE 1.0: A library schema for scoring events
    "10.1038/s41597-024-04282-0",  # HED-based annotation for BIDS datasets
    "10.1038/s41597-025-05791-2",  # HED validation framework
]

# Sync configuration dictionary (used by registry)
SYNC_CONFIG = {
    "github_repos": HED_REPOS,
    "paper_queries": HED_PAPER_QUERIES,
    "paper_dois": HED_PAPER_DOIS,
}
