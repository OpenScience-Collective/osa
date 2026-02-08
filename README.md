# Open Science Assistant (OSA)

An extensible AI assistant platform for open science projects, built with LangGraph/LangChain and FastAPI.

## Overview

OSA provides domain-specific AI assistants for open science tools with:
- **HED Assistant**: Hierarchical Event Descriptors for neuroimaging annotation
- **BIDS Assistant**: Brain Imaging Data Structure
- **EEGLAB Assistant**: EEG analysis toolbox
- **NEMAR Assistant**: BIDS-formatted EEG, MEG, and iEEG dataset discovery

Features:
- **YAML-driven community registry** - add a new assistant with just a config file
- Modular tool system for document retrieval, validation, and code execution
- Multi-source knowledge bases (GitHub, OpenALEX, Discourse forums, mailing lists)
- Embeddable chat widget for any website
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

## Community Registry

OSA uses a YAML-driven registry to configure community assistants. Each community has a `config.yaml` that declares its documentation, system prompt, knowledge sources, and specialized tools.

```bash
# Directory structure
src/assistants/
    hed/config.yaml      # HED assistant configuration
    bids/config.yaml     # BIDS assistant (planned)
```

### Adding a New Community

1. Create `src/assistants/my-tool/config.yaml`:

```yaml
id: my-tool
name: My Tool
description: A research tool for neuroscience
status: available

# Required: Per-community OpenRouter API key for cost attribution
# Set the environment variable on your backend server
openrouter_api_key_env_var: "OPENROUTER_API_KEY_MY_TOOL"

system_prompt: |
  You are a technical assistant for {name}.
  {preloaded_docs_section}
  {available_docs_section}

documentation:
  - title: Getting Started
    url: https://my-tool.org/docs
    source_url: https://raw.githubusercontent.com/org/my-tool/main/docs/intro.md
    preload: true

github:
  repos:
    - org/my-tool
```

2. Set the API key environment variable on your backend:

```bash
export OPENROUTER_API_KEY_MY_TOOL="your-openrouter-key"
```

3. Validate your configuration:

```bash
uv run osa validate src/assistants/my-tool/config.yaml
```

4. Start the server - the `/{community-id}/ask` endpoint is auto-created.

For the full guide, see the [community registry documentation](https://docs.osc.earth/osa/registry/).

## Documentation

Full documentation is available at **[docs.osc.earth/osa](https://docs.osc.earth/osa/)**.

## Development

```bash
# Run tests with coverage
uv run pytest --cov

# Format code
uv run ruff check --fix . && uv run ruff format .
```

## License

MIT
