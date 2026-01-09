# Open Science Assistant (OSA)

A precise, reliable AI assistant platform for researchers working with open science tools. Built for accuracy over scale; serving small research communities from lab servers.

## Development Workflow

**All development follows: Issue -> Feature Branch (from develop) -> PR to develop -> Review -> Merge**

**Branch Strategy:**
- `main` - Production releases only, auto-deploys to prod
- `develop` - Integration branch, auto-deploys to dev
- `feature/*` - Feature branches, created from and merged to `develop`

1. **Pick an issue** from GitHub Issues
2. **Create feature branch from develop**: `git checkout develop && git pull && git checkout -b feature/issue-N-short-description`
3. **Implement** with atomic commits
4. **Review** using `/pr-review-toolkit:review-pr` before creating PR
5. **Address ALL review findings** - fix critical AND important issues, not just critical
6. **Create PR to develop**: `gh pr create --base develop`
7. **Merge with merge commit**: `gh pr merge --merge --delete-branch` (NEVER squash)

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
gh pr merge --merge --delete-branch              # MERGE COMMIT, never squash
```

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

- **.context/plan.md**: Implementation roadmap
- **.context/research.md**: Technical notes, target project resources
- **.rules/**: Development standards

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
- **NEVER squash merge** - always use merge commits to preserve history
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

## References

- Plan: `.context/plan.md`
- Research notes: `.context/research.md`
- HED tools analysis: `.context/hed_tools_analysis.md`
- HEDit (deployment patterns): `/Users/yahya/Documents/git/annot-garden/hedit`
- QP (doc retrieval patterns): `/Users/yahya/Documents/git/HED/qp`

## HED Development Resources

All HED-related repositories: `/Users/yahya/Documents/git/HED/`

- **hed-python**: Python validator library
- **hed-web**: Flask REST API (hedtools.org)
- **hed-javascript**: Browser-based validator
- **hed-resources**: User documentation (markdown source)
- **hed-specification**: Technical specification (markdown source)
- **hed-schemas**: Schema definitions (JSON/XML)
- **hed-standard.github.io**: Website source (hedtags.org)
