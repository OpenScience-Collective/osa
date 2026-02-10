"""Knowledge sources module for community content discovery.

This module provides:
- SQLite + FTS5 database for storing community knowledge
- GitHub sync for issues/PRs
- Paper sync from OpenALEX, Semantic Scholar, and PubMed Central
- Code docstring sync
- Mailing list FAQ sync
- BIDS Extension Proposal (BEP) sync
- Full-text search for discovery (not as knowledge sources)

Design principle: These are for DISCOVERY, not authoritative answers.
The agent should link users to relevant discussions, not answer from them.
"""

from src.knowledge.db import get_connection, get_db_path, init_db
from src.knowledge.search import (
    BEPResult,
    SearchResult,
    search_beps,
    search_github_items,
    search_papers,
)

__all__ = [
    "BEPResult",
    "get_connection",
    "get_db_path",
    "init_db",
    "search_beps",
    "search_github_items",
    "search_papers",
    "SearchResult",
]
