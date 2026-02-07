# Open Science Assistant (OSA)

A precise, reliable AI assistant platform for researchers working with open science tools. Built for accuracy over scale; serving small research communities from lab servers.

## Development Workflow

**All development follows: Issue -> Feature Branch (from develop) -> PR to develop -> Review -> Merge**

**Branch Strategy:**
- `main` - Production releases only, auto-deploys to prod
  - Always has stable versions (no `.dev` suffix)
  - CI automatically strips `.dev` suffix if merged accidentally
  - Releases tagged with `--latest` flag
- `develop` - Integration branch, auto-deploys to dev
  - Has `.dev` suffix on versions (e.g., `0.5.1.dev0`)
- `feature/*` - Feature branches, created from and merged to `develop`

**Version Management (Automated):**
- `develop` branch: Versions end with `.dev0` suffix (e.g., `0.5.1.dev0`)
- `main` branch: Versions are stable, no suffix (e.g., `0.5.1`)
- When `src/version.py` changes on `main`:
  1. CI automatically strips `.dev` suffix if present
  2. Creates git tag (e.g., `v0.5.1`)
  3. Creates GitHub release marked as "latest"
- Manual version bumps use `scripts/bump_version.py`

1. **Pick an issue** from GitHub Issues
2. **Create feature branch from develop**: `git checkout develop && git pull && git checkout -b feature/issue-N-short-description`
3. **Implement** with atomic commits
4. **Review** using `/pr-review-toolkit:review-pr` before creating PR
5. **Address ALL review findings** - fix critical AND important issues, not just critical
6. **Create PR to develop**: `gh pr create --base develop`
7. **Squash and merge**: `gh pr merge --squash --delete-branch` (always squash to keep history clean)

```bash
# Example workflow
gh issue list                                    # Find issue to work on
git checkout develop && git pull                 # Start from develop
git checkout -b feature/issue-7-interfaces       # Create branch
# ... implement ...
git add -A && git commit -m "feat: add X"       # Atomic commits
/pr-review-toolkit:review-pr                     # Review before PR
# FIX ALL ISSUES from review (critical + important)
gh pr create --base develop --title "feat: add X" --body "Closes #7"
git push -u origin feature/issue-7-interfaces
gh pr merge --squash --delete-branch             # SQUASH MERGE to keep history clean
```

## GitHub Labels

Available labels for issues and PRs (check with `gh label list` before creating new ones):

**Priority:**
- `P0` - Blocker, must fix before release
- `P1` - Critical, fix as soon as possible
- `P2` - Important, fix when possible

**Type:**
- `bug` - Something isn't working
- `feature` - New feature or enhancement
- `enhancement` - New feature or request
- `documentation` - Improvements or additions to documentation
- `security` - Security vulnerability or hardening

**Category:**
- `testing` - Testing and quality assurance
- `operations` - Operations, monitoring, and observability
- `observability` - Logging, monitoring, and debugging
- `developer-experience` - Improves developer experience
- `widget` - Related to frontend widget
- `cost-management` - Cost tracking and optimization

**Status:**
- `good first issue` - Good for newcomers
- `help wanted` - Extra attention is needed
- `duplicate` - This issue or pull request already exists
- `invalid` - This doesn't seem right
- `question` - Further information is requested
- `wontfix` - This will not be worked on

**Adding new labels:**
1. Create the label: `gh label create "label-name" --description "Description" --color "hexcolor"`
2. Update this list in CLAUDE.md

## Design Principles

- **Precision over features**: Researchers need accurate, citation-backed answers
- **Simple infrastructure**: Lab server deployment, no complex scaling
- **Extensible tools**: General tool system that communities can adapt for their needs
- **Domain expertise**: Deep knowledge of specific tools, not broad generalist

**Target:** Multiple small research communities (HED, BIDS, EEGLAB, etc.), each with specific tool needs. The platform provides robust infrastructure; communities customize tools and prompts.

## Quick Start

```bash
# Setup environment (uses uv for dependency management)
uv sync

# Development server
uv run uvicorn src.api.main:app --reload --port 38528

# Run tests
uv run pytest tests/ -v

# Linting
uv run ruff check .
uv run ruff format .

# CLI usage
uv run osa --help
```

## Project Structure

```
src/
├── api/                    # FastAPI backend
│   ├── main.py            # App entry point, health check
│   ├── config.py          # Settings (pydantic-settings)
│   └── security.py        # API key auth, BYOK
├── cli/                    # Typer CLI
│   ├── main.py            # CLI commands
│   ├── client.py          # HTTP client
│   └── config.py          # User config (~/.config/osa)
├── agents/                 # LangGraph agents
│   ├── state.py           # State definitions
│   └── base.py            # BaseAgent, SimpleAgent, ToolAgent
├── core/services/          # Business logic
│   └── llm.py             # LLM provider abstraction
└── tools/                  # Document retrieval tools
```

## Key Documentation

**When working on different parts of the system, start with these documents:**

### API Development
- **.context/api-structure.md** - **START HERE for API work**
  - Community-based routing (`/{community_id}/ask`, `/chat`, etc.)
  - Model selection logic and provider routing
  - Common implementation mistakes and how to avoid them
  - How to add new communities

### Security & Authorization
- **.context/api_key_authorization_design.md** - API key auth and CORS
- **.context/security-architecture.md** - Security patterns

### Community Development (Adding/Modifying Communities)
- **Full docs site**: https://docs.osc.earth/osa/registry/ (canonical reference)
  - [Adding a Community](https://docs.osc.earth/osa/registry/quick-start/) - Step-by-step guide
  - [Local Testing](https://docs.osc.earth/osa/registry/local-testing/) - Testing a new community end-to-end
  - [Schema Reference](https://docs.osc.earth/osa/registry/schema-reference/) - Full YAML config schema
  - [Extensions](https://docs.osc.earth/osa/registry/extensions/) - Python plugins and MCP servers
- **.context/yaml_registry.md** - YAML-based community config (internal notes)
- **.context/community_onboarding_review.md** - Onboarding gap analysis
- **.context/local-testing-guide.md** - Quick local testing reference
- **Existing configs to reference**: `src/assistants/hed/config.yaml`, `src/assistants/eeglab/config.yaml`

### Tool System
- **.context/tool-system-guide.md** - How tools work and are registered

### Architecture & Planning
- **docs/architecture.md** - High-level system diagrams (for papers/docs)
- **.context/plan.md** - Implementation roadmap and current tasks
- **.context/research.md** - Technical notes, target project resources

### Development Standards
- **.rules/** - Code style, testing, conventions

## Development Guidelines

### Testing
- **NO MOCKS**: Real tests with real data only
- **Dynamic tests**: Query registries/configs, don't hardcode values (see `.rules/testing_guidelines.md`)
- **Coverage**: >70% minimum
- **LLM testing**: Use exemplar scenarios from real cases
- Run `uv run pytest --cov` before committing

### Code Style
- ruff for formatting/linting (pre-commit hooks)
- Type hints required
- Docstrings for public APIs

### Git
- **Follow the Development Workflow** (see top of file)
- Atomic commits, concise messages, no emojis
- Feature branches from `develop`, PRs target `develop`
- `main` is production only; merge `develop` -> `main` for releases
- **ALWAYS squash merge** - keep develop history clean with single commit per feature
- Use PR review toolkit before creating PRs
- **Address ALL review issues** (critical + important) before merging

### Code Exploration with Serena
- **Use Serena MCP for efficient code exploration** via Language Server Protocol (LSP)
- **Prefer symbolic tools over reading full files**
- Key tools:
  - `mcp__serena__get_symbols_overview`: See file structure without reading full content
  - `mcp__serena__find_symbol`: Locate specific classes/functions/methods
  - `mcp__serena__find_referencing_symbols`: Find where symbols are used
  - `mcp__serena__search_for_pattern`: Search for text patterns when symbol name unclear
- **Workflow**: Overview → Locate → Read only what's needed
- See `.serena/` memories for detailed usage patterns

## Target Projects

- **HED**: Hierarchical Event Descriptors (annotation standard)
- **BIDS**: Brain Imaging Data Structure (data organization)
- **EEGLAB**: EEG analysis MATLAB toolbox

## Architecture

Simple, single-instance deployment:
- In-memory state (no PostgreSQL needed)
- Direct document fetching (no vector DB needed)
- LangFuse for observability (optional)
- Deployment patterns from HEDit when ready

## Backend Server Access

To access and test the backend server:

```bash
# SSH into the backend server (via jump host)
ssh -J hallu hedtools

# Backend repo location on server
cd ~/osa

# Deploy to dev
deploy/deploy.sh dev

# Check service status
docker ps
docker logs osa-dev

# Manual sync trigger
docker exec osa-dev python -m src.cli.main sync github --full
```

**API Endpoints:**
- Dev: https://api.osc.earth/osa-dev
- Prod: https://api.osc.earth/osa

**Frontend:**
- Dev: https://develop.demo.osc.earth
- Prod: https://demo.osc.earth

### Inspecting Knowledge Databases

Knowledge databases (SQLite) live **inside the Docker containers**, not locally.
Do NOT look for `.db` files in the local repo; they won't be there.

```bash
# List databases in a container
ssh -o "RequestTTY=no" -J hallu hedtools \
  "docker exec osa find /app/data/knowledge -name '*.db'"

# Containers: osa (prod), osa-dev (dev)
# Database paths: /app/data/knowledge/{community_id}.db
#   e.g., /app/data/knowledge/eeglab.db, /app/data/knowledge/hed.db

# List tables (no sqlite3 binary; use python)
ssh -o "RequestTTY=no" -J hallu hedtools \
  "docker exec osa python3 -c 'import sqlite3; conn = sqlite3.connect(\"/app/data/knowledge/eeglab.db\"); print([r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\"\")]); conn.close()'"

# Query example: count docstrings
ssh -o "RequestTTY=no" -J hallu hedtools \
  "docker exec osa python3 -c 'import sqlite3; conn = sqlite3.connect(\"/app/data/knowledge/eeglab.db\"); print(conn.execute(\"SELECT COUNT(*) FROM docstrings\").fetchone()[0]); conn.close()'"

# Query example: search for a symbol
ssh -o "RequestTTY=no" -J hallu hedtools \
  "docker exec osa python3 -c 'import sqlite3; conn = sqlite3.connect(\"/app/data/knowledge/eeglab.db\"); [print(r) for r in conn.execute(\"SELECT symbol_name, file_path FROM docstrings WHERE symbol_name LIKE \\\"%erpimage%\\\"\").fetchall()]; conn.close()'"
```

**Important notes:**
- `sqlite3` CLI is not installed in containers; use `python3 -c` with the `sqlite3` module
- Use `ssh -o "RequestTTY=no"` to avoid interactive shell banners
- Dev and prod databases may differ; always check the right container

## References

- **API structure**: `.context/api-structure.md` (read first for API work)
- **Architecture**: `docs/architecture.md` (high-level diagrams)
- **Plan**: `.context/plan.md` (current roadmap)
- **Research notes**: `.context/research.md` (technical deep-dives)
- **HED tools analysis**: `.context/hed_tools_analysis.md`
- **HEDit** (deployment patterns): `/Users/yahya/Documents/git/annot-garden/hedit`
- **QP** (doc retrieval patterns): `/Users/yahya/Documents/git/HED/qp`

## HED Development Resources

All HED-related repositories: `/Users/yahya/Documents/git/HED/`

- **hed-python**: Python validator library
- **hed-web**: Flask REST API (hedtools.org)
- **hed-javascript**: Browser-based validator
- **hed-resources**: User documentation (markdown source)
- **hed-specification**: Technical specification (markdown source)
- **hed-schemas**: Schema definitions (JSON/XML)
- **hed-standard.github.io**: Website source (hedtags.org)
