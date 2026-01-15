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

# Sync configuration dictionary (used by registry)
SYNC_CONFIG = {
    "github_repos": HED_REPOS,
    "paper_queries": HED_PAPER_QUERIES,
}
