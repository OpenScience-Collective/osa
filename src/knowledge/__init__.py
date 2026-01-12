"""Knowledge sources module for discovery of HED discussions and papers.

This module provides:
- SQLite + FTS5 database for storing discussion metadata
- GitHub sync for issues/PRs from HED repositories
- Paper sync from OpenALEX, Semantic Scholar, and PubMed Central
- Full-text search for discovery (not as knowledge sources)

Design principle: These are for DISCOVERY, not authoritative answers.
The agent should link users to relevant discussions, not answer from them.
"""

from src.knowledge.db import get_connection, get_db_path, init_db
from src.knowledge.search import SearchResult, search_github_items, search_papers

__all__ = [
    "get_connection",
    "get_db_path",
    "init_db",
    "search_github_items",
    "search_papers",
    "SearchResult",
]
