# Codebase Structure

## Top-Level Layout
```
/Users/yahya/Documents/git/osa/
├── .context/              # Planning and research docs
│   ├── plan.md           # Implementation roadmap
│   └── research.md       # Technical notes
├── .github/              # GitHub Actions (future)
├── .rules/               # Development standards
│   ├── python.md         # Python conventions
│   ├── testing.md        # Testing guidelines
│   └── git.md            # Git workflow
├── .serena/              # Serena MCP data
├── docs/                 # Additional documentation
├── scripts/              # Utility scripts
│   └── bump_version.py   # Version management
├── src/                  # Main source code
├── tests/                # Test suite
├── .env.example          # Environment template
├── .gitignore
├── .pre-commit-config.yaml
├── CLAUDE.md             # Project instructions for Claude
├── LICENSE
├── pyproject.toml        # Project configuration
└── README.md
```

## Source Code Structure (src/)
```
src/
├── __init__.py
├── version.py            # Version info (dynamic)
├── api/                  # FastAPI backend
│   ├── __init__.py
│   ├── main.py          # App entry, health check
│   ├── config.py        # Settings (pydantic-settings)
│   ├── security.py      # API key auth, BYOK
│   └── routers/
│       ├── __init__.py
│       └── chat.py      # Chat endpoints (streaming SSE)
├── cli/                  # Typer CLI
│   ├── __init__.py
│   ├── main.py          # CLI commands (chat, ask, serve)
│   ├── client.py        # HTTP client for API
│   └── config.py        # User config (~/.config/osa)
├── agents/               # LangGraph agents
│   ├── __init__.py
│   ├── state.py         # State definitions
│   ├── base.py          # BaseAgent, SimpleAgent, ToolAgent
│   └── hed.py           # HED assistant agent
├── core/                 # Business logic
│   ├── __init__.py
│   ├── domain/          # Domain models
│   │   └── __init__.py
│   └── services/        # Core services
│       ├── __init__.py
│       ├── llm.py       # LLM provider abstraction
│       └── litellm_llm.py  # LiteLLM implementation
├── tools/                # LangChain tools
│   ├── __init__.py
│   ├── base.py          # Tool base classes
│   ├── fetcher.py       # Document fetching
│   └── hed.py           # HED-specific tools
└── utils/                # Utilities
    └── __init__.py
```

## Test Structure (tests/)
```
tests/
├── __init__.py
├── conftest.py           # Shared fixtures
├── test_api/             # API tests
├── test_agents/          # Agent tests
│   └── test_hed.py      # HED agent tests
├── test_cli/             # CLI tests
├── test_core/            # Core service tests
├── test_tools/           # Tool tests
└── test_integration/     # Integration tests
```

## Key Files and Their Purposes

### Configuration
- **pyproject.toml**: All project config (dependencies, tools, build)
- **.pre-commit-config.yaml**: Pre-commit hooks (ruff, basic checks)
- **.env**: Environment variables (not in git)
- **.env.example**: Template for .env

### Documentation
- **README.md**: Installation, quick start
- **CLAUDE.md**: Project instructions for AI assistant
- **.context/plan.md**: Detailed implementation roadmap
- **.rules/*.md**: Development standards

### Entry Points
- **src/api/main.py**: FastAPI application
- **src/cli/main.py**: CLI entry point (registered as `osa` command)

## Important Notes
- Entry point: `osa` CLI command → `src.cli.main:cli`
- API server: `uvicorn src.api.main:app`
- Version: Dynamically loaded from `src/version.py`
- Config: User config in `~/.config/osa/` (via platformdirs)
