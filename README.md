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
# Set up your API key
# Anthropic (what the platform itself runs on): https://console.anthropic.com/settings/keys
# OpenRouter (still supported for BYOK): https://openrouter.ai/keys
osa init

# Ask the HED assistant a question
osa ask -a hed "What is HED?"

# Start an interactive chat session
osa chat -a hed

# Show all commands
osa --help
```

### API Server

Requires server dependencies: `pip install 'open-science-assistant[server]'`

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
osa config set --anthropic-key sk-ant-...
osa config set --openrouter-key sk-or-v1-...

# Override API URL per-command
osa ask -a hed "What is HED?" --api-url https://api.osc.earth/osa-dev
```

### Deployment

OSA can be deployed via Docker:

```bash
# Pull and run
docker pull ghcr.io/openscience-collective/osa:latest
docker run -d --name osa -p 38528:38528 \
  -e ANTHROPIC_API_KEY=your-key \
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

# By default, every community runs on the shared Claude Platform key.
# Optional: only set these if the community funds its own usage instead.
# Set the named environment variable on your backend server.
# anthropic_api_key_env_var: "ANTHROPIC_API_KEY_MY_TOOL"
# openrouter_api_key_env_var: "OPENROUTER_API_KEY_MY_TOOL"

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

2. (Optional) If you set either key env var, export it on your backend:

```bash
export ANTHROPIC_API_KEY_MY_TOOL="sk-ant-..."
# or, for a community that funds OpenRouter usage instead
export OPENROUTER_API_KEY_MY_TOOL="sk-or-v1-..."
```

3. Validate your configuration:

```bash
uv run osa validate src/assistants/my-tool/config.yaml
```

4. Start the server - the `/{community-id}/ask` endpoint is auto-created.

For the full guide, see the [community registry documentation](https://docs.osc.earth/osa/registry/).

To let a community's model write and run Python in the reader's own browser
(`execute_code`, a client tool), see
[`docs/community-browser-runtime.md`](docs/community-browser-runtime.md):
the `extensions.client_tools` and `runtime.python` config keys, the lock
overlay for wheels Pyodide does not ship, the embedding page's
Content-Security-Policy, and the measured first-load cost.

To customize a community's widget (title, greeting, suggested questions, logo,
and its colors: the surface, the text on it, the accent used on the white
panel, and the reader's own message bubble), see
[`docs/community-widget.md`](docs/community-widget.md).

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
