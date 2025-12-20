# Open Science Assistant (OSA)

An extensible AI assistant platform for open science projects, built with LangGraph/LangChain and FastAPI.

## Overview

OSA provides domain-specific AI assistants for open science tools (HED, BIDS, EEGLAB) with:
- Modular tool system for document retrieval, validation, and code execution
- Multi-source knowledge bases (GitHub, OpenALEX, Discourse forums, mailing lists)
- Extensible architecture for adding new assistants and tools
- Production-ready observability via LangFuse

## Installation

```bash
# Create conda environment
conda create -n osa python=3.12 -y
conda activate osa

# Install in development mode
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

## Quick Start

```bash
# Run development server
uvicorn src.api.main:app --reload --port 38428

# CLI usage
osa --help
```

## Development

```bash
# Run tests with coverage
pytest --cov

# Format code
ruff check --fix . && ruff format .
```

## License

MIT
