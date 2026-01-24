# YAML Community Registry

This document describes the declarative YAML-based registry system for configuring research community assistants.

## Overview

Communities can be configured in `registries/communities.yaml` without writing Python code. The YAML config provides:

- Community metadata (id, name, description)
- Documentation sources to index
- GitHub repositories for issue/PR sync
- Paper/citation search queries
- Extension points for specialized tools

## Quick Start

Add a new community to `registries/communities.yaml`:

```yaml
communities:
  - id: my-community
    name: My Community
    description: Description of the community
    status: available  # or 'beta', 'coming_soon'

    documentation:
      - url: https://docs.example.com/
        type: sphinx  # or 'mkdocs', 'html'

    github:
      repos:
        - org/repo-name

    citations:
      queries:
        - "search query for papers"
```

## Configuration Schema

### Community Config

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | string | Yes | Unique identifier (kebab-case) |
| name | string | Yes | Display name |
| description | string | Yes | Short description |
| status | string | No | 'available', 'beta', or 'coming_soon' (default: 'available') |
| documentation | list | No | Documentation sources |
| github | object | No | GitHub configuration |
| citations | object | No | Paper search configuration |
| discourse | list | No | Forum search configuration (Phase 2) |
| extensions | object | No | Extension points |

### Documentation Source

```yaml
documentation:
  - url: https://docs.example.com/
    type: sphinx  # sphinx, mkdocs, or html
    source_repo: org/repo  # GitHub repo for raw markdown
    description: Optional description
```

### GitHub Config

```yaml
github:
  repos:
    - org/repo1
    - org/repo2
```

### Citation Config

```yaml
citations:
  queries:
    - "search query 1"
    - "search query 2"
  dois:
    - "10.1234/example"  # Core papers to track citations
```

### Extensions (Phase 2)

```yaml
extensions:
  python_plugins:
    - module: src.path.to.module
      tools:  # Optional: specific tools to import
        - tool_name_1
        - tool_name_2

  mcp_servers:  # Future
    - name: server-name
      command: ["uvx", "server-package"]
```

## How It Works

1. **Startup**: `discover_assistants()` loads `registries/communities.yaml`
2. **Registration**: Each community becomes an `AssistantInfo` in the registry
3. **Merging**: If Python code also registers the same ID, configs merge:
   - Python factory function is used for assistant creation
   - YAML provides sync_config (repos, queries, DOIs)
   - YAML community_config is stored for reference

## Accessing Config in Code

```python
from src.assistants import registry

# Get community config
config = registry.get_community_config("hed")
if config:
    print(config.github.repos)
    print(config.citations.queries)

# Get sync config (for CLI sync commands)
info = registry.get("hed")
repos = info.sync_config.get("github_repos", [])
```

## File Locations

- Registry config: `registries/communities.yaml`
- Pydantic models: `src/core/config/community.py`
- Registry loader: `src/assistants/registry.py`
- Discovery: `src/assistants/__init__.py`

## Implementation Status

- [x] Phase 1: YAML schema and registry loader
- [ ] Phase 2: Generic CommunityAssistant class
- [ ] Phase 3: CLI sync integration
- [ ] Phase 4: API router generalization
- [ ] Phase 5: Frontend widget modularization

See issue #42 for full implementation plan.
