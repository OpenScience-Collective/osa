# Community Configuration Reference

Complete reference for all fields in community `config.yaml` files.

---

## Required Fields

### `id`
**Type:** string
**Required:** Yes
**Format:** kebab-case (lowercase letters, numbers, hyphens)

Unique identifier for your community. Must be kebab-case (lowercase with hyphens).

**Examples:**
```yaml
id: hed                    # Valid
id: brain-imaging          # Valid
id: eeglab                 # Valid
id: my-community-2024      # Valid

id: HED                    # Invalid - uppercase
id: my_community           # Invalid - underscore
id: -myproject             # Invalid - leading hyphen
```

**Validation:**
- Must match pattern: `^[a-z0-9]+(-[a-z0-9]+)*$`
- No leading or trailing hyphens
- No consecutive hyphens
- Cannot be empty

---

### `name`
**Type:** string
**Required:** Yes

Display name shown to users. Can include spaces, capitalization, and special characters.

**Examples:**
```yaml
name: HED (Hierarchical Event Descriptors)
name: Brain Imaging Data Structure
name: EEGLAB
name: My Research Community
```

---

### `description`
**Type:** string
**Required:** Yes

Brief description of your community or tool. Used in listings and introductions.

**Examples:**
```yaml
description: Event annotation standard for neuroimaging
description: Data organization standard for brain imaging
description: MATLAB toolbox for EEG analysis
```

**Recommendations:**
- Keep under 100 characters
- Focus on what the tool/community does
- Avoid marketing language

---

## Optional Core Fields

### `status`
**Type:** string (enum)
**Default:** `available`
**Options:** `available`, `beta`, `coming_soon`

Assistant availability status.

**Examples:**
```yaml
status: available      # Fully operational
status: beta          # In testing, may have issues
status: coming_soon   # Announced but not ready
```

---

### `system_prompt`
**Type:** string (multiline)
**Default:** Uses platform default prompt

Custom system prompt for the assistant. Supports template placeholders.

**Placeholders:**
- `{name}`: Community display name
- `{description}`: Community description
- `{repo_list}`: Formatted list of GitHub repos (if configured)
- `{paper_dois}`: Formatted list of paper DOIs (if configured)
- `{additional_instructions}`: Extra instructions passed at creation time

**Example:**
```yaml
system_prompt: |
  You are an expert assistant for {name}.

  {description}

  You help researchers with:
  - Understanding the specification
  - Validating their data
  - Troubleshooting errors

  Available repositories:
  {repo_list}

  When answering questions, be precise and cite specific
  documentation sections when possible.
```

**When to use:**
- Need domain-specific expertise (e.g., medical terminology)
- Want to emphasize certain capabilities
- Have specific tone or style requirements

**When not to use:**
- Default prompt works well for most communities
- Adds complexity to maintain
- Can cause unexpected behavior if poorly written

---

## CORS Configuration

### `cors_origins`
**Type:** list of strings
**Default:** `[]`

Allowed CORS origins for widget embedding. Required if you plan to embed the widget on your website.

**Format:**
- Must include scheme (`https://` or `http://`)
- Supports wildcard subdomains (`*.example.org`)
- Port numbers allowed (`:8080`)
- Max 255 characters per origin

**Examples:**
```yaml
cors_origins:
  # Production website
  - https://hedtags.org
  - https://www.hedtags.org

  # Development/preview environments
  - https://*.pages.dev           # Cloudflare Pages previews
  - https://*.vercel.app          # Vercel previews
  - http://localhost:3000         # Local development

  # Subdomain wildcard
  - https://*.myproject.org       # Matches docs.myproject.org, wiki.myproject.org, etc.
```

**Validation:**
- Must start with `http://` or `https://`
- Must be valid origin format
- Max 255 characters per origin
- No path, query, or fragment allowed
- Automatically deduplicated

**Common Patterns:**
```yaml
# Public open source project (typical)
cors_origins:
  - https://myproject.org
  - https://www.myproject.org
  - https://*.pages.dev

# Organization with multiple sites
cors_origins:
  - https://*.myorg.edu
  - https://docs.myorg.edu
  - https://wiki.myorg.edu

# Local development only (testing)
cors_origins:
  - http://localhost:3000
  - http://127.0.0.1:3000
```

---

## API Key Configuration

### `openrouter_api_key_env_var`
**Type:** string
**Default:** Uses platform API key

Environment variable name containing your community's OpenRouter API key.

**Purpose:**
- Cost attribution to your community (not platform)
- Control over API usage limits
- Ability to use different models independently

**Format:**
- Recommended pattern: `OPENROUTER_API_KEY_<COMMUNITY>`
- Uppercase with underscores
- Must be set as environment variable on server

**Example:**
```yaml
openrouter_api_key_env_var: OPENROUTER_API_KEY_HED
```

**Setup:**
```bash
# On server or in .env file
export OPENROUTER_API_KEY_HED="sk-or-v1-your-api-key-here"
```

**Validation:**
- Validates that env var is set (warns if missing)
- Optional: Test key with `--test-api-key` flag

**Cost Implications:**
- **Without BYOK:** Costs billed to platform (shared limits)
- **With BYOK:** Costs billed to your account (dedicated limits)

---

## Model Configuration

### `default_model`
**Type:** string
**Default:** Platform default model (check Settings configuration)

Default LLM model for your community (OpenRouter format).

**Format:** `creator/model-name`

**Common Models:**
```yaml
# Anthropic
default_model: anthropic/claude-haiku-4.5         # Fast, cheap
default_model: anthropic/claude-sonnet-4.5        # Balanced
default_model: anthropic/claude-opus-4.5          # Most capable

# OpenAI
default_model: openai/gpt-5.2-chat                # Latest GPT
default_model: openai/gpt-5-mini                  # Fast, cheap

# Google
default_model: google/gemini-3-flash-preview      # Fast
default_model: google/gemini-3-pro-preview        # Capable

# Other
default_model: moonshotai/kimi-k2-0905           # Long context
default_model: qwen/qwen3-235b-a22b-2507         # Open source
```

**Considerations:**
- **Cost:** Opus ($15/1M tokens) vs Haiku ($0.25/1M tokens)
- **Speed:** Flash models faster, Opus slower
- **Capability:** More expensive = more capable
- **Context:** Some models support longer contexts

**Recommendations:**
- Start with Haiku for testing
- Upgrade to Sonnet for production
- Use Opus only if needed (cost)

---

### `default_model_provider`
**Type:** string
**Default:** OpenRouter's default routing

Provider routing preference for the model (e.g., `Cerebras`, `Together`).

**Purpose:**
- Route to specific provider for better performance
- Use provider-specific optimizations
- Control latency vs cost tradeoffs

**Examples:**
```yaml
default_model: anthropic/claude-haiku-4.5
default_model_provider: Cerebras    # Route to Cerebras for speed
```

**Common Providers:**
- `Cerebras`: Ultra-fast inference
- `Together`: Good balance
- `OpenRouter`: Default routing
- (Provider availability varies by model)

---

## Documentation Configuration

### `documentation`
**Type:** list of DocSource objects
**Default:** `[]`

Documentation pages to index and make available to the assistant.

**Fields:**

#### `title`
**Required:** Yes
Human-readable document title.

#### `url`
**Required:** Yes
HTML page URL for user reference (shown in responses).

#### `source_url`
**Optional**
Raw markdown/content URL for fetching. Required if `preload: true`.

#### `preload`
**Default:** `false`
If true, content is fetched and embedded in system prompt.

#### `category`
**Default:** `general`
Category for organizing documents.

#### `type`
**Default:** `html`
**Options:** `sphinx`, `mkdocs`, `html`, `markdown`, `json`
Documentation format type.

#### `source_repo`
**Optional**
GitHub repo for raw markdown sources (format: `org/repo`).

#### `description`
**Optional**
Short description of what this documentation covers.

**Examples:**

**Basic documentation:**
```yaml
documentation:
  - title: Getting Started
    url: https://hedtags.org/hed-resources/getting-started.html
```

**Preloaded core documentation:**
```yaml
documentation:
  - title: Core Specification
    url: https://hedtags.org/hed-spec/
    source_url: https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/index.md
    preload: true
    category: core
    type: markdown
```

**Categorized documentation:**
```yaml
documentation:
  # Core docs (preloaded)
  - title: HED Specification
    url: https://hedtags.org/hed-spec/
    source_url: https://raw.githubusercontent.com/.../spec.md
    preload: true
    category: core

  # Tool docs (on-demand)
  - title: Validator API
    url: https://hedtags.org/tools/validator
    category: tools
    type: sphinx

  # Tutorial docs (on-demand)
  - title: Annotation Tutorial
    url: https://hedtags.org/tutorials/annotation
    category: tutorials
```

**Recommendations:**
- Preload 1-2 critical documents max (affects prompt size)
- Use categories to organize large doc sets
- Provide source_url for markdown docs when possible
- On-demand docs retrieved when needed

**Validation:**
- If `preload: true`, must have `source_url`
- URLs must be valid HTTP/HTTPS
- Duplicates automatically removed

---

## GitHub Configuration

### `github`
**Type:** GitHubConfig object
**Default:** None

GitHub repository configuration for issue/PR search and sync.

**Fields:**

#### `repos`
**Type:** list of strings
**Format:** `org/repo`

**Example:**
```yaml
github:
  repos:
    - hed-standard/hed-specification
    - hed-standard/hed-python
    - hed-standard/hed-javascript
```

**Validation:**
- Must match pattern: `^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$`
- Automatically deduplicated
- Repository names cannot be empty

**Use Cases:**
- Search issues for common problems
- Reference PRs in answers
- Track development activity
- Sync issue history for context

---

## Citation Configuration

### `citations`
**Type:** CitationConfig object
**Default:** None

Paper and citation search configuration.

**Fields:**

#### `queries`
**Type:** list of strings
Search queries for finding related papers.

**Example:**
```yaml
citations:
  queries:
    - "Hierarchical Event Descriptors"
    - "HED neuroimaging"
    - "event annotation fMRI"
```

**Validation:**
- Empty strings removed
- Automatically deduplicated

#### `dois`
**Type:** list of strings
**Format:** `10.xxxx/yyyy`

Core paper DOIs to track citations for.

**Example:**
```yaml
citations:
  dois:
    - "10.3389/fninf.2016.00043"
    - "10.1016/j.neuroimage.2021.118152"
```

**Validation:**
- Must match DOI format: `10.xxxx/yyyy`
- Strips `https://doi.org/` prefixes
- Automatically deduplicated

**Complete Example:**
```yaml
citations:
  queries:
    - "Hierarchical Event Descriptors"
    - "HED annotation"
  dois:
    - "10.3389/fninf.2016.00043"    # Original HED paper
    - "10.1016/j.neuroimage.2021.118152"  # HED-3G paper
```

---

## Discourse Configuration (Phase 2)

### `discourse`
**Type:** list of DiscourseConfig objects
**Default:** `[]`
**Status:** Phase 2 feature (not yet implemented)

Forum search configuration for Discourse-based communities.

**Fields:**

#### `url`
**Required:** Yes
Base URL of the Discourse instance.

#### `tags`
**Optional**
Tags to filter forum topics by.

**Example:**
```yaml
discourse:
  - url: https://neurostars.org
    tags:
      - hed
      - bids
```

---

## Extensions Configuration

### `extensions`
**Type:** ExtensionsConfig object
**Default:** None

Extension points for specialized tools (Python plugins and MCP servers).

**Fields:**

### `python_plugins`
**Type:** list of PythonPlugin objects
**Default:** `[]`

Python modules providing additional tools.

**Fields:**

#### `module`
**Required:** Yes
Python module path (e.g., `src.assistants.hed.tools`).

#### `tools`
**Optional**
Specific tool names to import, or None for all.

**Example:**
```yaml
extensions:
  python_plugins:
    - module: src.assistants.hed.tools
      tools:
        - validate_events
        - search_schema

    - module: src.assistants.hed.validators
      # Import all tools from module
```

**Use Cases:**
- Domain-specific validation tools
- Custom data processing
- API integrations
- File format converters

**Validation:**
- Module paths must be unique
- Module must be importable

---

### `mcp_servers`
**Type:** list of McpServer objects
**Default:** `[]`
**Status:** Phase 2 feature

MCP (Model Context Protocol) server configurations.

**Fields:**

#### `name`
**Required:** Yes
Server name identifier.

#### `command`
**Optional**
Command to start local MCP server (list of strings).

#### `url`
**Optional**
URL for remote MCP server.

**Validation:**
- Must have either `command` (local) or `url` (remote), not both
- Server names must be unique
- Command cannot be empty list

**Example (Local Server):**
```yaml
extensions:
  mcp_servers:
    - name: hed-validator
      command:
        - python
        - -m
        - hed.mcp_server
```

**Example (Remote Server):**
```yaml
extensions:
  mcp_servers:
    - name: hed-validator
      url: https://hed-validator.example.org/mcp
```

---

## Widget Configuration

### `enable_page_context`
**Type:** boolean
**Default:** `true`

Enable the `fetch_current_page` tool for widget embedding.

**Purpose:**
- Allows assistant to read content from the page where widget is embedded
- Useful for context-aware help
- Can be disabled for standalone (non-widget) assistants

**Example:**
```yaml
enable_page_context: true    # Default - widget can read page content
enable_page_context: false   # Disable for CLI-only assistant
```

**When to disable:**
- Assistant will never be used in a widget
- Privacy concerns about page content access
- Widget embedded on third-party sites

---

## Complete Configuration Examples

### Minimal Configuration

Simplest possible working configuration:

```yaml
id: my-community
name: My Community
description: My research community assistant
cors_origins:
  - https://my-community.org
```

**Uses:**
- Platform API key
- Platform default model
- No documentation sources
- Basic CORS setup

**Good for:** Quick testing, proof-of-concept

---

### Production Configuration

Recommended production setup:

```yaml
id: hed
name: HED (Hierarchical Event Descriptors)
description: Event annotation standard for neuroimaging

status: available

# Community-specific API key
openrouter_api_key_env_var: OPENROUTER_API_KEY_HED

# Fast, cost-effective model
default_model: anthropic/claude-haiku-4.5
default_model_provider: Cerebras

# CORS for widget embedding
cors_origins:
  - https://hedtags.org
  - https://www.hedtags.org
  - https://*.pages.dev  # Preview deployments

# Core documentation
documentation:
  - title: HED Specification
    url: https://www.hedtags.org/hed-spec/
    source_url: https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/index.md
    preload: true
    category: core

  - title: Getting Started
    url: https://www.hedtags.org/hed-resources/getting-started.html
    category: tutorials

  - title: API Reference
    url: https://www.hedtags.org/hed-python/
    category: reference
    type: sphinx

# GitHub repositories
github:
  repos:
    - hed-standard/hed-specification
    - hed-standard/hed-python

# Citation tracking
citations:
  queries:
    - "Hierarchical Event Descriptors"
  dois:
    - "10.3389/fninf.2016.00043"
```

---

### Advanced Configuration with Extensions

Full-featured configuration with custom tools:

```yaml
id: hed
name: HED (Hierarchical Event Descriptors)
description: Event annotation standard for neuroimaging

openrouter_api_key_env_var: OPENROUTER_API_KEY_HED
default_model: anthropic/claude-sonnet-4.5

cors_origins:
  - https://hedtags.org
  - https://*.pages.dev

documentation:
  - title: HED Specification
    url: https://www.hedtags.org/hed-spec/
    source_url: https://raw.githubusercontent.com/.../spec.md
    preload: true
    category: core
    description: Complete HED specification and schema reference

github:
  repos:
    - hed-standard/hed-specification
    - hed-standard/hed-python

citations:
  queries:
    - "Hierarchical Event Descriptors"
  dois:
    - "10.3389/fninf.2016.00043"

# Custom tools
extensions:
  python_plugins:
    - module: src.assistants.hed.tools
      tools:
        - validate_hed_string
        - search_hed_schema
        - suggest_hed_tags

# Custom system prompt
system_prompt: |
  You are an expert HED (Hierarchical Event Descriptors) assistant.

  HED is a structured vocabulary for annotating events in neuroimaging data.
  You help researchers:
  - Understand HED concepts and syntax
  - Validate HED annotations
  - Find appropriate HED tags
  - Troubleshoot validation errors

  Available repositories:
  {repo_list}

  When helping with HED strings:
  1. Always validate syntax
  2. Suggest specific tags from the schema
  3. Explain validation errors clearly
  4. Provide working examples
```

---

## Validation

All configurations are validated using Pydantic. Use the validation CLI to check your config before deployment:

```bash
# Basic validation
uv run osa validate src/assistants/my-community/config.yaml

# Validate and test API key
uv run osa validate src/assistants/my-community/config.yaml --test-api-key
```

See [community-onboarding.md](community-onboarding.md) for complete validation workflow.

---

## Common Patterns

### Multi-Site Community
```yaml
cors_origins:
  - https://main-site.org
  - https://docs.main-site.org
  - https://wiki.main-site.org
  - https://*.main-site.org  # Catch all subdomains
```

### Development + Production
```yaml
cors_origins:
  - https://myproject.org           # Production
  - https://*.pages.dev             # Cloudflare preview
  - https://*.vercel.app            # Vercel preview
  - http://localhost:3000           # Local dev
```

### Documentation-Heavy Project
```yaml
documentation:
  # Preload critical docs
  - title: Core Concepts
    url: https://example.org/concepts
    source_url: https://raw.githubusercontent.com/.../concepts.md
    preload: true
    category: core

  # On-demand reference docs
  - title: API Reference
    url: https://example.org/api
    category: reference

  - title: CLI Reference
    url: https://example.org/cli
    category: reference

  # Tutorials
  - title: Quick Start
    url: https://example.org/quickstart
    category: tutorials
```

### Cost-Conscious Setup
```yaml
# Use your own API key
openrouter_api_key_env_var: OPENROUTER_API_KEY_MYPROJECT

# Use cheapest viable model
default_model: anthropic/claude-haiku-4.5

# Route to fast provider
default_model_provider: Cerebras
```

---

## Security Best Practices

1. **CORS Origins:**
   - Only add origins you control
   - Use specific domains when possible
   - Wildcards only for preview environments
   - Never use `*` alone (blocks all origins)

2. **API Keys:**
   - Use community-specific keys (not shared platform key)
   - Keep keys in environment variables (not in config files)
   - Rotate keys periodically
   - Monitor usage on OpenRouter dashboard

3. **Documentation URLs:**
   - Only link to trusted sources
   - Verify HTTPS for all URLs
   - Avoid user-generated content sources
   - Review source_url destinations

4. **Extensions:**
   - Only load trusted Python modules
   - Review third-party tools before adding
   - Keep extension code updated

---

## Troubleshooting

See [troubleshooting.md](troubleshooting.md) for detailed error resolution.

**Quick Checks:**

- **Validation fails:** Run `uv run osa validate <config-path>` for detailed errors
- **API key not working:** Check env var name matches `openrouter_api_key_env_var`
- **CORS errors:** Verify origins include scheme (`https://`)
- **Preload errors:** Ensure `source_url` is set for preloaded docs
- **ID validation fails:** Use kebab-case (lowercase with hyphens)

---

## Schema Version

This reference documents the schema as of **January 24, 2026**.

**Config Version:** Not explicitly versioned (uses Pydantic validation)
**Breaking Changes:** Announced in release notes
