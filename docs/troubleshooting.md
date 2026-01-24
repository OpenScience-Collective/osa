# Troubleshooting Guide

Common issues and solutions for OSA community onboarding and operations.

---

## Quick Diagnostic Checklist

Before diving into specific errors:

1. **Validate your config:**
   ```bash
   uv run osa validate src/assistants/your-community/config.yaml
   ```

2. **Check environment variables:**
   ```bash
   echo $OPENROUTER_API_KEY_YOUR_COMMUNITY
   # Should print your API key, not empty
   ```

3. **Test API key:**
   ```bash
   uv run osa validate src/assistants/your-community/config.yaml --test-api-key
   ```

4. **Check server status:**
   ```bash
   uv run osa health
   ```

---

## Configuration Validation Errors

### Error: YAML Syntax Error

**Symptom:**
```
YAML syntax error at line 15, column 3: mapping values are not allowed here
```

**Causes:**
- Incorrect indentation (must use spaces, not tabs)
- Missing colon after key
- Improper list formatting
- Special characters not quoted

**Solutions:**

**Check indentation:**
```yaml
# Wrong - using tabs
documentation:
→   - title: My Doc

# Correct - using spaces
documentation:
  - title: My Doc
```

**Quote special characters:**
```yaml
# Wrong - colon in unquoted string
description: HED: Event annotation

# Correct - quoted
description: "HED: Event annotation"
```

**List formatting:**
```yaml
# Wrong - missing dash
cors_origins:
  https://example.com

# Correct - with dash
cors_origins:
  - https://example.com
```

**Validation:**
```bash
# Use a YAML validator
python -c "import yaml; yaml.safe_load(open('config.yaml'))"
```

---

### Error: Community ID must be kebab-case

**Symptom:**
```
Community ID must be kebab-case (lowercase, hyphens): MyProject
```

**Cause:**
Community ID contains uppercase letters, underscores, or invalid characters.

**Solution:**
```yaml
# Wrong
id: MyProject
id: my_project
id: my.project
id: -myproject   # Leading hyphen

# Correct
id: myproject
id: my-project
id: my-project-2024
```

**Rules:**
- Lowercase letters only
- Numbers allowed
- Hyphens allowed (not leading/trailing)
- No underscores, dots, or spaces

---

### Error: Invalid CORS origin

**Symptom:**
```
Invalid CORS origin 'example.com'. Must be a valid origin (e.g., 'https://example.org')
```

**Cause:**
CORS origin missing scheme or improperly formatted.

**Solution:**
```yaml
# Wrong - missing scheme
cors_origins:
  - example.com
  - www.example.com

# Wrong - includes path
cors_origins:
  - https://example.com/docs

# Correct
cors_origins:
  - https://example.com
  - https://www.example.com
  - https://*.pages.dev
```

**Common Mistakes:**
- Forgetting `https://` prefix
- Including path (`/docs`)
- Including query string (`?foo=bar`)
- Using `*` alone (not allowed)

---

### Error: Preload requires source_url

**Symptom:**
```
DocSource 'My Doc' has preload=True but no source_url
```

**Cause:**
Document configured with `preload: true` but missing `source_url` field.

**Solution:**
```yaml
# Wrong - preload without source_url
documentation:
  - title: Core Docs
    url: https://example.com/docs
    preload: true

# Correct - source_url provided
documentation:
  - title: Core Docs
    url: https://example.com/docs
    source_url: https://raw.githubusercontent.com/org/repo/main/docs.md
    preload: true
```

**Why:**
Preloaded documents are fetched and embedded in the system prompt. The `source_url` must point to the raw markdown or text content.

---

### Error: Repository must be in 'org/repo' format

**Symptom:**
```
Repository must be in 'org/repo' format, got: hed-specification
```

**Cause:**
GitHub repository missing organization prefix.

**Solution:**
```yaml
# Wrong - missing org
github:
  repos:
    - hed-specification

# Correct - org/repo format
github:
  repos:
    - hed-standard/hed-specification
```

---

### Error: Invalid DOI format

**Symptom:**
```
Invalid DOI format (expected '10.xxxx/yyyy'): doi.org/10.1234/example
```

**Cause:**
DOI includes URL prefix or doesn't match expected format.

**Solution:**
```yaml
# Wrong - includes prefix
citations:
  dois:
    - https://doi.org/10.1234/example
    - doi.org/10.1234/example

# Correct - DOI only
citations:
  dois:
    - 10.1234/example
```

**Valid DOI Format:**
- Must start with `10.`
- Format: `10.xxxx/yyyy`
- No URL prefixes

---

## API Key Issues

### Warning: API key env var not set

**Symptom:**
```
⚠ OPENROUTER_API_KEY_MYPROJECT not set
Validation passed with warnings
```

**Impact:**
- Assistant will fall back to platform API key
- Costs billed to platform, not your community
- Shared rate limits apply

**Solution:**

**For local testing:**
```bash
# Add to shell profile (~/.zshrc or ~/.bashrc)
echo 'export OPENROUTER_API_KEY_MYPROJECT="sk-or-v1-..."' >> ~/.zshrc
source ~/.zshrc

# Verify
echo $OPENROUTER_API_KEY_MYPROJECT
```

**For production (server):**
```bash
# Add to .env file
echo 'OPENROUTER_API_KEY_MYPROJECT="sk-or-v1-..."' >> .env

# Or add to environment
export OPENROUTER_API_KEY_MYPROJECT="sk-or-v1-..."
```

**Verify:**
```bash
uv run osa validate src/assistants/myproject/config.yaml
# Should show: "✓ OPENROUTER_API_KEY_MYPROJECT is set"
```

---

### Error: API key test failed (401 Unauthorized)

**Symptom:**
```
✗ Invalid API key (401 Unauthorized)
```

**Causes:**
- API key is invalid or expired
- Wrong key format
- Key not activated on OpenRouter

**Solution:**

1. **Verify key format:**
   ```bash
   echo $OPENROUTER_API_KEY_MYPROJECT
   # Should start with: sk-or-v1-
   ```

2. **Check key on OpenRouter:**
   - Visit https://openrouter.ai/keys
   - Verify key exists and is active
   - Check usage limits not exceeded

3. **Generate new key if needed:**
   - Go to https://openrouter.ai/keys
   - Create new API key
   - Update environment variable

4. **Test directly:**
   ```bash
   curl https://openrouter.ai/api/v1/models \
     -H "Authorization: Bearer $OPENROUTER_API_KEY_MYPROJECT"
   # Should return 200 OK with model list
   ```

---

### Error: API key test failed (403 Forbidden)

**Symptom:**
```
✗ API key lacks permissions (403 Forbidden)
```

**Cause:**
API key doesn't have necessary permissions or credits exhausted.

**Solution:**

1. **Check credits:**
   - Visit https://openrouter.ai/credits
   - Ensure account has credits available

2. **Check key permissions:**
   - Some keys may be restricted to certain models
   - Verify key has access to models you want to use

3. **Add credits:**
   - Add credits to your OpenRouter account
   - Test again after credits added

---

## Runtime Errors

### Error: CORS policy blocked

**Browser Console:**
```
Access to fetch at 'https://api.osc.earth/osa/...' from origin 'https://mysite.com'
has been blocked by CORS policy
```

**Cause:**
Your website origin not listed in `cors_origins` config.

**Solution:**

1. **Add your origin to config:**
   ```yaml
   cors_origins:
     - https://mysite.com
     - https://www.mysite.com  # Don't forget www variant
   ```

2. **Redeploy assistant** (config changes require restart)

3. **Verify origin exactly matches:**
   ```javascript
   // In browser console
   console.log(window.location.origin)
   // Must match exactly (including https://)
   ```

**Common Issues:**
- Forgot `www` subdomain variant
- `http://` vs `https://` mismatch
- Port number missing (e.g., `:3000`)
- Trailing slash in config (remove it)

---

### Error: Widget not loading

**Symptom:**
Widget icon doesn't appear or widget doesn't open.

**Causes:**
1. Script not loaded
2. Wrong community ID
3. API endpoint unreachable
4. JavaScript errors

**Diagnosis:**

1. **Check browser console** (F12 → Console):
   ```
   Look for errors like:
   - Failed to load widget.js
   - OSAWidget is not defined
   - Community 'xxx' not found
   ```

2. **Verify script loads:**
   ```html
   <!-- Check this in your HTML -->
   <script src="https://api.osc.earth/osa/widget.js"></script>
   ```

3. **Check network tab:**
   - Widget.js should load (200 OK)
   - API requests should succeed

**Solutions:**

**Wrong community ID:**
```html
<!-- Wrong - ID doesn't match config -->
<script>
    OSAWidget.init({
        communityId: 'wrong-id'  // Check this matches config.yaml
    });
</script>

<!-- Correct -->
<script>
    OSAWidget.init({
        communityId: 'hed'  // Must match config.yaml id field
    });
</script>
```

**Script placement:**
```html
<!-- Wrong - script in <head> before widget init -->
<head>
    <script>
        OSAWidget.init({ communityId: 'hed' });
    </script>
    <script src="https://api.osc.earth/osa/widget.js"></script>
</head>

<!-- Correct - load script first -->
<body>
    <script src="https://api.osc.earth/osa/widget.js"></script>
    <script>
        OSAWidget.init({ communityId: 'hed' });
    </script>
</body>
```

**API endpoint:**
```javascript
// Check if API is reachable
fetch('https://api.osc.earth/osa/health')
    .then(r => r.json())
    .then(console.log);
// Should show: {status: "healthy"}
```

---

### Error: Messages not getting responses

**Symptom:**
Widget accepts input but shows loading spinner indefinitely.

**Causes:**
1. API key not configured
2. Network errors
3. Model timeout
4. Backend server down

**Diagnosis:**

1. **Check browser network tab:**
   - Look for failed API requests
   - Check response status codes
   - Look for timeout errors

2. **Check API health:**
   ```bash
   curl https://api.osc.earth/osa/health
   ```

3. **Check backend logs** (if you have access):
   ```bash
   docker logs osa-prod
   ```

**Solutions:**

**API key missing:**
- Verify `openrouter_api_key_env_var` is set on server
- Check env var exists: `echo $OPENROUTER_API_KEY_XXX`
- Restart server after adding env var

**Network errors:**
- Check firewall rules
- Verify API endpoint accessible
- Check DNS resolution

**Timeouts:**
- May indicate model is slow or overloaded
- Try different model (faster)
- Check OpenRouter status

---

## Deployment Issues

### Error: Config file not found

**Symptom:**
```
Error: Config file not found: src/assistants/myproject/config.yaml
```

**Cause:**
Config file not in expected location or path incorrect.

**Solution:**

1. **Check file exists:**
   ```bash
   ls src/assistants/myproject/config.yaml
   ```

2. **Verify directory structure:**
   ```
   src/assistants/
   └── myproject/
       └── config.yaml
   ```

3. **Check file name:**
   - Must be exactly `config.yaml`
   - Lowercase, not `Config.yaml`

---

### Error: Assistant not discovered

**Symptom:**
After deployment, `osa myproject ask "test"` returns:
```
Error: Unknown command 'myproject'
```

**Cause:**
Assistant not registered in registry.

**Diagnosis:**

1. **Check discovery:**
   ```bash
   uv run python -c "from src.assistants import registry; print([a.id for a in registry.list_all()])"
   ```

2. **Check directory structure:**
   ```bash
   ls src/assistants/
   # Should show: myproject/
   ```

**Solution:**

1. **Ensure config.yaml exists:**
   ```bash
   ls src/assistants/myproject/config.yaml
   ```

2. **Restart server:**
   ```bash
   # Discovery happens at startup
   uv run uvicorn src.api.main:app --reload
   ```

3. **Check for validation errors:**
   ```bash
   uv run osa validate src/assistants/myproject/config.yaml
   ```

---

## Testing Issues

### Error: Tests fail with import errors

**Symptom:**
```
ImportError: cannot import name 'validate' from 'src.cli.validate'
```

**Cause:**
Module not in Python path or not installed.

**Solution:**

1. **Sync dependencies:**
   ```bash
   uv sync
   ```

2. **Run tests from repo root:**
   ```bash
   cd /path/to/osa
   uv run pytest tests/
   ```

3. **Check Python path:**
   ```bash
   uv run python -c "import sys; print(sys.path)"
   # Should include current directory
   ```

---

### Error: Tests fail with fixture errors

**Symptom:**
```
fixture 'tmp_path' not found
```

**Cause:**
Using old pytest version or fixture not available.

**Solution:**

1. **Update pytest:**
   ```bash
   uv sync --upgrade
   ```

2. **Verify pytest version:**
   ```bash
   uv run pytest --version
   # Should be >= 7.0
   ```

---

## Performance Issues

### Issue: Widget loads slowly

**Symptoms:**
- Widget takes 3-5+ seconds to appear
- First message slow to respond

**Causes:**
1. Large system prompt (too many preloaded docs)
2. Slow model
3. Network latency
4. Cold start (server sleeping)

**Solutions:**

**Reduce preloaded docs:**
```yaml
# Before - 5 preloaded docs = slow
documentation:
  - title: Doc 1
    preload: true
  - title: Doc 2
    preload: true
  # ... 3 more preloaded

# After - 1-2 critical docs only
documentation:
  - title: Core Spec
    preload: true
  - title: API Ref
    preload: false  # Fetch on-demand
```

**Use faster model:**
```yaml
# Before - Opus is slow but capable
default_model: anthropic/claude-opus-4.5

# After - Haiku is fast
default_model: anthropic/claude-haiku-4.5
default_model_provider: Cerebras  # Route to fast provider
```

**Check network:**
```bash
# Test API latency
time curl https://api.osc.earth/osa/health
# Should be < 1 second
```

---

### Issue: High API costs

**Symptoms:**
- OpenRouter bill higher than expected
- Usage exceeded budget

**Causes:**
1. Expensive model (Opus)
2. Long conversations (context accumulation)
3. Many users
4. Preloaded docs increasing prompt size

**Solutions:**

**Use cheaper model:**
```yaml
# Opus: $15/1M tokens
default_model: anthropic/claude-opus-4.5

# Haiku: $0.25/1M tokens (60x cheaper!)
default_model: anthropic/claude-haiku-4.5
```

**Reduce preloaded docs:**
- Each preloaded doc adds to every request
- Move to on-demand retrieval
- Keep preloaded docs minimal

**Monitor usage:**
- Check OpenRouter dashboard regularly
- Set up budget alerts
- Track usage by community ID

**Cost Comparison:**
| Model | Cost (per 1M tokens) | Use Case |
|-------|---------------------|----------|
| Haiku | $0.25 | General Q&A |
| Sonnet | $3.00 | Complex tasks |
| Opus | $15.00 | Critical accuracy |

---

## Widget Integration Issues

### Issue: Widget appears but can't read page content

**Symptom:**
Assistant says "I cannot access the current page content" when asked about page.

**Cause:**
`enable_page_context` disabled or tool not available.

**Solution:**

```yaml
# Enable page context tool
enable_page_context: true
```

**Verify:**
```bash
# Check config
grep enable_page_context src/assistants/myproject/config.yaml
# Should show: enable_page_context: true
```

---

### Issue: Widget positioning problems

**Symptom:**
Widget icon overlaps with page content or appears in wrong location.

**Cause:**
CSS conflicts with your site's styles.

**Solution:**

**Add custom CSS:**
```html
<style>
  /* Adjust widget position */
  #osa-widget-container {
    bottom: 20px !important;
    right: 20px !important;
    z-index: 9999 !important;
  }
</style>
```

**Check for conflicts:**
```javascript
// In browser console
console.log(getComputedStyle(document.getElementById('osa-widget-container')));
```

---

## Documentation Sync Issues

### Error: Documentation fetch fails

**Symptom:**
Assistant says "I couldn't retrieve that documentation."

**Causes:**
1. `source_url` unreachable
2. GitHub rate limit
3. URL changed/moved

**Diagnosis:**

```bash
# Test URL directly
curl -I https://raw.githubusercontent.com/org/repo/main/docs.md
# Should return 200 OK
```

**Solutions:**

**URL moved:**
```yaml
# Update to new URL
documentation:
  - title: My Doc
    url: https://newsite.org/docs
    source_url: https://raw.githubusercontent.com/org/repo/main/docs/newpath.md
```

**GitHub rate limit:**
- Wait an hour for limit reset
- Use GitHub token for higher limits
- Move docs to CDN

**HTTPS required:**
```yaml
# Wrong - HTTP not secure
source_url: http://example.com/docs.md

# Correct - HTTPS
source_url: https://example.com/docs.md
```

---

## Getting Help

If you're still stuck after trying these solutions:

1. **Check logs:**
   ```bash
   # Local development
   uv run uvicorn src.api.main:app --reload
   # Watch for errors in output

   # Production
   docker logs osa-prod
   ```

2. **Run health check:**
   ```bash
   uv run osa health
   ```

3. **Validate config again:**
   ```bash
   uv run osa validate src/assistants/your-community/config.yaml --test-api-key
   ```

4. **File an issue:**
   - GitHub: https://github.com/OpenScience-Collective/osa/issues
   - Include:
     - Error message (full text)
     - Config file (sanitized - remove API keys!)
     - Steps to reproduce
     - Environment (OS, Python version)

5. **Ask in discussions:**
   - https://github.com/OpenScience-Collective/osa/discussions

---

## Prevention Checklist

Before deploying to production:

- [ ] Config validated locally (`osa validate`)
- [ ] API key tested (`--test-api-key`)
- [ ] CORS origins match production domains
- [ ] Documentation URLs verified (returns 200 OK)
- [ ] Widget tested on actual website
- [ ] Costs estimated based on expected usage
- [ ] Monitoring/alerts configured
- [ ] Team trained on troubleshooting basics

---

## Common Error Messages Reference

**Quick lookup table:**

| Error | Section | Quick Fix |
|-------|---------|-----------|
| YAML syntax error | [Config Validation](#yaml-syntax-error) | Check indentation, quotes |
| kebab-case | [Config Validation](#community-id-must-be-kebab-case) | Use lowercase with hyphens |
| Invalid CORS origin | [Config Validation](#invalid-cors-origin) | Add `https://` prefix |
| preload requires source_url | [Config Validation](#preload-requires-source_url) | Add `source_url` field |
| API key not set | [API Keys](#api-key-env-var-not-set) | Export env var |
| 401 Unauthorized | [API Keys](#api-key-test-failed-401) | Check API key validity |
| CORS blocked | [Runtime](#cors-policy-blocked) | Add origin to config |
| Widget not loading | [Runtime](#widget-not-loading) | Check script, community ID |
| Config not found | [Deployment](#config-file-not-found) | Check file path |
| Assistant not discovered | [Deployment](#assistant-not-discovered) | Restart server |

---

**Last Updated:** January 2026
