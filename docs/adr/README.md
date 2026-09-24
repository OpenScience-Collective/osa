# Architecture Decision Records

ADRs for significant, hard-to-reverse decisions in OSA, in
[MADR](https://adr.github.io/madr/)-style format (`template.md`). Numbered
sequentially; never renumber or delete a record - mark it Superseded and
link forward instead.

Several of these were backfilled from decisions already made and documented
informally elsewhere in the repo (PR descriptions, issue writeups,
`.context/plan.md`, `.context/research.md`, and workflow-file comments) -
each record cites its actual source rather than restating it from memory.
If you find another decision like that, add an ADR for it rather than
letting the reasoning stay buried in a PR nobody will reread.

| # | Title |
|---|---|
| [0001](0001-develop-main-branch-strategy-with-automated-versioning.md) | `develop`/`main` branch strategy with fully automated version bumping |
| [0002](0002-sync-develop-to-main-via-pull-request.md) | Sync `develop` from `main` through a pull request, not a direct push |
| [0003](0003-release-image-build-and-latest-tag-race.md) | Serialize release image builds; `:latest` follows releases, not `main` |
| [0004](0004-anthropic-claude-platform-migration.md) | Serve platform-funded requests from the Claude Platform on AWS, not OpenRouter |
| [0005](0005-enforce-pr-approval-requirement.md) | Require an approving review before merge, with a scoped admin bypass and unconditional deletion protection |
| [0006](0006-langgraph-for-agent-orchestration.md) | LangGraph for agent orchestration |
| [0007](0007-simple-storage-no-external-database.md) | In-memory state, direct document fetching, SQLite+FTS5 - no PostgreSQL/Redis/vector DB |
| [0008](0008-byok-bring-your-own-key.md) | Support BYOK (bring your own key) alongside platform-funded requests |
| [0009](0009-astral-tooling-uv-ruff.md) | Astral tooling (uv, ruff) for Python dependency management and linting |
| [0010](0010-the-notebook-surface.md) | Skip marimo as the notebook surface; adopt JupyterLite pinned to Pyodide 0.29.5, deferred to a follow-up (#453) |
| [0011](0011-the-notebook-site.md) | Host the JupyterLite notebook surface at notebook.osc.earth/osa, built from this repository, amending 0010 |

## Adding a new ADR

1. Copy `template.md` to `NNNN-short-title.md` (next sequential number).
2. Cite real sources (issue/PR numbers, workflow comments, `.context/`
   notes) - don't fabricate rationale you can't point to.
3. Add a row to the table above.
