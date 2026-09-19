# 0007. In-memory state, direct document fetching, SQLite+FTS5 for knowledge - no PostgreSQL/Redis/vector DB

Date: 2026-09-19 (decision predates this ADR; sourced from
`.context/plan.md`'s "Architecture Decisions" and `.context/research.md`'s
"Knowledge Source Storage Comparison", backfilled)

## Status

Accepted

## Context

OSA is built for accuracy over scale, deployed on lab servers for small
research communities (see `AGENTS.md`'s "Design Principles": "Simple
infrastructure: Lab server deployment, no complex scaling"). It needs to (a)
hold conversation state for a single running instance and (b) store and
search accumulated knowledge (GitHub issues, PRs, forum posts, papers - 10K
to 1M records, growing via nightly periodic retrievers).

`.context/research.md` evaluated JSON files, TinyDB, MongoDB, DuckDB,
SQLite+FTS5, and PostgreSQL for the knowledge-storage problem specifically,
comparing search speed, dependencies, scale ceiling, and persistence model.

## Decision

- **No PostgreSQL, no Redis, no vector database.** In-memory state is
  sufficient for a single-server deployment; horizontal scaling is out of
  scope by design. Document retrieval is direct (preloaded core docs in the
  system prompt, on-demand tool-call fetches, GitHub raw URLs as a free CDN,
  simple local file caching) rather than a RAG/vector-search pipeline, which
  is unnecessary complexity for this project's document corpus.
- **SQLite with the FTS5 extension** for the knowledge databases (one
  `.db` file per community, e.g. `hed.db`, `eeglab.db` - see AGENTS.md
  "Inspecting Knowledge Databases"). Chosen over the alternatives because it
  needs no external server (a single file per community, nothing to run or
  manage), FTS5 gives indexed full-text search with BM25 ranking and
  boolean/phrase queries built in, it is 100-1000x faster than a JSON
  linear scan at this record count, it needs no extra dependency (`sqlite3`
  is Python stdlib), backup is just copying the file, and it supports
  concurrent reads with transactional writes. MongoDB and PostgreSQL were
  rejected as overkill for a single-instance deployment; JSON/TinyDB were
  rejected for their O(n) scan cost at 10K+ records.

## Consequences

- "Add complexity only when actually needed" is the explicit governing
  principle here - this decision should be revisited if the deployment
  model changes (e.g. multi-instance/horizontally-scaled deployment), not
  worked around in place.
- Knowledge databases live inside the deployed Docker containers, not in
  the repository or in source control; there is deliberately no local `.db`
  file to find or diff.
- Because there's no vector DB, retrieval quality depends on FTS5's
  lexical/BM25 search plus the preloaded-docs and on-demand-tool-call
  strategy, not on semantic embedding search - a different tradeoff than a
  RAG pipeline would make, chosen deliberately for this project's doc
  corpus rather than by default.
