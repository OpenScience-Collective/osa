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

Set the `HED_LSP_PATH` environment variable to point to your hed-lsp installation:

```bash
export HED_LSP_PATH=/path/to/hed-lsp
```

Or install globally:

```bash
cd hed-lsp/server
npm link  # Makes hed-suggest available globally
```

### Usage

The `suggest_hed_tags` tool will automatically find the CLI and convert natural language to valid HED tags:

```python
from src.tools.hed_validation import suggest_hed_tags

result = suggest_hed_tags.invoke({
    'search_terms': ['button press', 'visual flash'],
    'top_n': 5
})
# {'button press': ['Button', 'Response-button', 'Mouse-button', 'Press', 'Push'],
#  'visual flash': ['Flash', 'Flickering', 'Visual-presentation']}
```

The CLI can also be used directly:

```bash
hed-suggest "button press"
# Button, Response-button, Mouse-button, Press, Push

hed-suggest --json "button" "stimulus"
# {"button": [...], "stimulus": [...]}
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
