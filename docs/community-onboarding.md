# Community Onboarding Guide

This guide walks you through onboarding your research community to the Open Science Assistant (OSA) platform.

**Goal:** Create a working AI assistant for your community in under 15 minutes.

**Prerequisites:**
- GitHub access to the OSA repository
- OpenRouter API key (get one at [openrouter.ai](https://openrouter.ai))
- Basic familiarity with YAML
- (Optional) Your community's documentation URLs

---

## Overview

Onboarding involves four main steps:
1. Create your community configuration file
2. Validate the configuration locally
3. Test the assistant
4. Deploy to production

---

## Step 1: Create Community Configuration

### 1.1 Create Config Directory

Create a directory for your community under `src/assistants/`:

```bash
mkdir -p src/assistants/your-community-id
cd src/assistants/your-community-id
```

**Naming:** Use kebab-case (lowercase with hyphens): `hed`, `bids`, `eeglab`, etc.

### 1.2 Create config.yaml

Create `config.yaml` with your community's information:

```yaml
id: your-community-id  # Must match directory name
name: Your Community Name  # Display name (e.g., "HED (Hierarchical Event Descriptors)")
description: Brief description of your community or tool

# Optional: Community status (default: available)
status: available  # Options: available, beta, coming_soon

# Required: CORS origins for widget embedding
cors_origins:
  - https://your-website.org
  - https://www.your-website.org
  # For development/preview environments:
  - https://*.pages.dev  # Cloudflare Pages previews
  - https://*.vercel.app  # Vercel previews

# Optional: Documentation sources
documentation:
  - title: Getting Started Guide
    url: https://your-website.org/docs/getting-started
    source_url: https://raw.githubusercontent.com/org/repo/main/docs/getting-started.md
    preload: true  # Include in system prompt (max 2 recommended)
    category: core

  - title: API Reference
    url: https://your-website.org/docs/api
    # source_url optional for on-demand retrieval
    category: reference

# Optional: GitHub repositories for issue/PR search
github:
  repos:
    - your-org/main-repo
    - your-org/tools-repo

# Optional: Citation tracking
citations:
  queries:
    - "Your Tool Name"
  dois:
    - "10.1234/your-core-paper"

# Optional: Community-specific API key (recommended)
openrouter_api_key_env_var: OPENROUTER_API_KEY_YOUR_COMMUNITY

# Optional: Community-specific default model
default_model: anthropic/claude-3.5-sonnet
default_model_provider: Cerebras  # For performance routing
```

See [config-reference.md](config-reference.md) for all available fields.

---

## Step 2: Set Up Environment Variables

### 2.1 Create Environment Variable

Add your OpenRouter API key to the server environment:

```bash
export OPENROUTER_API_KEY_YOUR_COMMUNITY="sk-or-v1-your-api-key-here"
```

**For production:** Add to `.env` file or server environment configuration.

**For development:** Add to your shell profile (~/.zshrc or ~/.bashrc):

```bash
echo 'export OPENROUTER_API_KEY_YOUR_COMMUNITY="sk-or-v1-..."' >> ~/.zshrc
source ~/.zshrc
```

### 2.2 Verify Environment Variable

```bash
echo $OPENROUTER_API_KEY_YOUR_COMMUNITY
# Should print your API key
```

---

## Step 3: Validate Configuration

### 3.1 Install OSA CLI

```bash
# In the OSA repository root
uv sync
```

### 3.2 Run Validation

```bash
uv run osa validate src/assistants/your-community-id/config.yaml
```

**Expected output:**
```
✓ YAML Syntax: Valid
✓ Schema Validation: Valid
✓ Community ID: your-community-id
✓ Community Name: Your Community Name
✓ CORS Origins: 3 configured
✓ Documentation: 2 docs
✓ API Key Env Var: OPENROUTER_API_KEY_YOUR_COMMUNITY is set

✓ Validation passed
```

### 3.3 Test API Key (Optional)

Verify your API key works with OpenRouter:

```bash
uv run osa validate src/assistants/your-community-id/config.yaml --test-api-key
```

This makes a test request to OpenRouter to ensure the key is valid and has appropriate permissions.

---

## Step 4: Test Locally

### 4.1 Start Development Server

```bash
uv run uvicorn src.api.main:app --reload --port 38528
```

### 4.2 Test with CLI

In a new terminal:

```bash
uv run osa your-community-id chat --standalone
```

Try asking a question:
```
You: What is [your tool/community]?
```

### 4.3 Test Widget Integration

Create a test HTML file:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Test Widget</title>
</head>
<body>
    <h1>Widget Test</h1>
    <script src="https://osa.yourdomain.com/widget.js"></script>
    <script>
        OSAWidget.init({
            communityId: 'your-community-id',
            apiEndpoint: 'http://localhost:38528'
        });
    </script>
</body>
</html>
```

Open in browser and test the widget.

---

## Step 5: Deploy to Production

### 5.1 Create Pull Request

```bash
git checkout develop
git pull
git checkout -b feature/add-your-community-id
git add src/assistants/your-community-id/
git commit -m "feat: add [Your Community] assistant"
git push -u origin feature/add-your-community-id
```

Create PR:
```bash
gh pr create --base develop --title "feat: add [Your Community] assistant"
```

### 5.2 PR Review Process

The platform maintainers will:
1. Review your configuration
2. Validate CORS origins
3. Check API key setup
4. Test the assistant
5. Merge to `develop` branch

### 5.3 Deployment

Once merged to `develop`:
- Development environment deploys automatically
- Test at: `https://api.osc.earth/osa-dev`

For production release:
- Maintainers merge `develop` → `main`
- Production deploys automatically
- Live at: `https://api.osc.earth/osa`

---

## Step 6: Configure Widget on Your Website

### 6.1 Add Widget Script

Add to your website's HTML:

```html
<script src="https://api.osc.earth/osa/widget.js"></script>
<script>
    OSAWidget.init({
        communityId: 'your-community-id'
    });
</script>
```

### 6.2 Widget Customization (Coming Soon)

Future features:
- Theme color customization
- Position settings
- Custom triggers

---

## Troubleshooting

See [troubleshooting.md](troubleshooting.md) for common issues and solutions.

**Common issues:**
- **"API key not set" warning:** Ensure environment variable name matches config
- **CORS errors:** Check origins are correctly formatted with scheme (`https://`)
- **Widget not loading:** Verify `communityId` matches your config `id` field
- **Validation errors:** Check YAML syntax and required fields

---

## Next Steps

After successful onboarding:

1. **Sync Knowledge Base:**
   ```bash
   uv run osa sync github --community your-community-id
   uv run osa sync papers --community your-community-id
   ```

2. **Monitor Usage:**
   - Check logs for errors
   - Monitor API key usage on OpenRouter dashboard
   - Track user feedback

3. **Iterate:**
   - Add more documentation sources
   - Refine system prompt
   - Add specialized tools (Python plugins)

---

## Support

- **Issues:** [GitHub Issues](https://github.com/OpenScience-Collective/osa/issues)
- **Discussions:** [GitHub Discussions](https://github.com/OpenScience-Collective/osa/discussions)
- **Documentation:** [Full docs](https://github.com/OpenScience-Collective/osa/tree/main/docs)

---

## Appendix: Minimal Config Example

Simplest possible configuration:

```yaml
id: my-community
name: My Community
description: My research community assistant
cors_origins:
  - https://my-community.org
```

This uses:
- Platform API key (costs billed to platform)
- Platform default model
- No documentation (can add later)
- Basic CORS setup

**Recommended for:** Quick testing and proof-of-concept.
**For production:** Add `openrouter_api_key_env_var` for cost control.
