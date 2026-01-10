# Open Science Assistant (OSA)

An extensible AI assistant platform for open science projects, built with LangGraph/LangChain and FastAPI.

## Overview

OSA provides domain-specific AI assistants for open science tools with:
- **HED Assistant**: Hierarchical Event Descriptors for neuroimaging annotation
- **BIDS Assistant**: Brain Imaging Data Structure (coming soon)
- **EEGLAB Assistant**: EEG analysis toolbox (coming soon)

Features:
- Modular tool system for document retrieval, validation, and code execution
- Multi-source knowledge bases (GitHub, OpenALEX, Discourse forums, mailing lists)
- Extensible architecture for adding new assistants and tools
- Production-ready observability via LangFuse

## Installation

```bash
# From PyPI
pip install open-science-assistant

# Or with uv (recommended)
uv pip install open-science-assistant
```

### Development Setup

```bash
# Clone and install in development mode
git clone https://github.com/OpenScience-Collective/osa.git
cd osa
uv sync --extra dev

# Install pre-commit hooks
uv run pre-commit install
```

## Quick Start

### CLI Usage

```bash
# Show available assistants
osa

# Ask the HED assistant a question
osa hed ask "What is HED?"

# Start an interactive chat session
osa hed chat

# Show all commands
osa --help
osa hed --help
```

### API Server

```bash
# Start the API server
osa serve

# Or with uvicorn directly
uv run uvicorn src.api.main:app --reload --port 38528
```

### Configuration

```bash
# Show current config
osa config show

# Set API keys for BYOK (Bring Your Own Key)
osa config set --openrouter-key YOUR_KEY

# Connect to remote server (uses BYOK)
osa hed ask "What is HED?" --url https://api.osc.earth/osa-dev
```

### Deployment

OSA can be deployed via Docker:

```bash
# Pull and run
docker pull ghcr.io/openscience-collective/osa:latest
docker run -d --name osa -p 38528:38528 \
  -e OPENROUTER_API_KEY=your-key \
  ghcr.io/openscience-collective/osa:latest

# Check health
curl http://localhost:38528/health
```

See [deploy/DEPLOYMENT_ARCHITECTURE.md](deploy/DEPLOYMENT_ARCHITECTURE.md) for detailed deployment options including Apache reverse proxy and BYOK configuration.

## Optional: HED Tag Suggestions

The HED assistant can suggest valid HED tags from natural language using the [hed-lsp](https://github.com/hed-standard/hed-lsp) CLI tool.

### Installation

```bash
# Clone and build hed-lsp
git clone https://github.com/hed-standard/hed-lsp.git
cd hed-lsp/server
npm install
npm run compile
```

### Configuration

Set the `HED_LSP_PATH` environment variable:

```bash
export HED_LSP_PATH=/path/to/hed-lsp
```

Or install globally:

```bash
cd hed-lsp/server
npm link  # Makes hed-suggest available globally
```

## Development

```bash
# Run tests with coverage
uv run pytest --cov

# Format code
uv run ruff check --fix . && uv run ruff format .
```

## License

MIT
