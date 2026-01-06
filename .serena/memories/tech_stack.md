# Tech Stack

## Python Version
- Python 3.11+ minimum (currently 3.12)

## Core Frameworks
- **FastAPI** (>= 0.125.0): API backend with streaming support
- **Uvicorn** (>= 0.38.0): ASGI server with standard extras
- **Pydantic** (>= 2.12.0): Data validation and settings
- **Typer** (>= 0.20.0): CLI framework
- **Rich** (>= 14.0.0): Terminal output formatting

## AI/LLM Stack
- **LangChain** (>= 1.2.0): LLM framework
- **LangGraph** (>= 1.0.0): Agent state management
- **langchain-openai** (>= 1.1.0): OpenAI integration
- **langchain-anthropic** (>= 1.3.0): Anthropic integration
- **LiteLLM** (>= 1.50.0): Prompt caching and multi-provider support
- **LangFuse** (>= 3.11.0): Observability and tracing

## External APIs
- **httpx** (>= 0.28.0): HTTP client
- **PyGithub** (>= 2.8.0): GitHub API
- **pyalex** (>= 0.19): OpenALEX API for papers

## Database
- **psycopg[binary]** (>= 3.3.0): PostgreSQL driver (for future use)
- Currently: In-memory state management

## Utilities
- **PyYAML** (>= 6.0.3): YAML parsing
- **beautifulsoup4** (>= 4.14.0): HTML parsing
- **lxml** (>= 6.0.0): XML/HTML processing
- **python-dotenv** (>= 1.2.0): Environment management
- **platformdirs** (>= 4.5.0): Cross-platform config directories

## Development Tools
- **pytest** (>= 9.0.0): Testing framework
- **pytest-cov** (>= 7.0.0): Coverage reporting
- **pytest-asyncio** (>= 1.3.0): Async test support
- **ruff** (>= 0.14.0): Linting and formatting (replaces Black, Flake8, isort)
- **pre-commit** (>= 4.5.0): Git hooks
- **mypy** (>= 1.19.0): Type checking
- **uv** (>= 0.5.0): Fast package installer (10-100x faster than pip)

## Build System
- **hatchling**: Build backend
- **pyproject.toml**: Project configuration

## Future Tooling Considerations
- SQLite with FTS5 for knowledge sources (when Phase 5 starts)
- Docker for deployment (when Phase 4 starts)
