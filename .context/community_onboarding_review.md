# Community Onboarding Review & Gap Analysis

## Executive Summary

This document analyzes the current community onboarding experience, identifies common use cases (>20% probability), edge cases, and gaps in the implementation.

**Key Finding:** While the YAML-driven architecture is solid, we're missing several critical onboarding features that will affect >50% of communities.

## Common Use Cases (>20% Probability)

### 1. New Community Onboarding ⚠️ **GAPS IDENTIFIED**

**Current Flow:**
```yaml
1. Create src/assistants/{community-id}/config.yaml
2. Set environment variable OPENROUTER_API_KEY_{COMMUNITY}
3. Restart server
4. Test with curl/browser
```

**Issues:**
- ❌ No validation that API key works before going live
- ❌ No way to test configuration without restart
- ❌ No onboarding checklist or wizard
- ❌ No validation feedback during YAML editing
- ❌ Errors only appear in server logs (not user-facing)

**Recommendation:** Add `/admin/validate-config` endpoint

### 2. Multiple Domain Support (www vs non-www) ⚠️ **COMMON PATTERN**

**Scenario:** Community has both `hedtags.org` and `www.hedtags.org`

**Current Solution:**
```yaml
cors_origins:
  - https://hedtags.org
  - https://www.hedtags.org
```

**Issue:** Tedious for communities with many subdomains

**Recommendation:** Support domain wildcards `https://{www.,}hedtags.org` or document pattern clearly

### 3. Preview/Staging Environments ⚠️ **VERY COMMON**

**Scenario:** Community uses Cloudflare Pages, Vercel, or similar with preview deploys

**Current Solution:**
```yaml
cors_origins:
  - https://hedtags.org              # Production
  - https://*.pages.dev               # Previews
```

**Status:** ✓ Already supported via wildcard

**Gap:** No way to use different models/settings for preview vs production

**Recommendation:** Consider `environment_overrides` in config?

### 4. Local Development ⚠️ **AFFECTS ALL DEVELOPERS**

**Scenario:** Community developers testing widget locally

**Current Solution:**
```yaml
# Platform-level cors_origins in Settings
cors_origins:
  - http://localhost:3000
  - http://localhost:8080
  - http://localhost:8888
```

**Issues:**
- ❌ Developers use random ports (3001, 5173, etc.)
- ❌ Platform CORS applies to ALL communities
- ❌ No way for community to add their own localhost origins

**Recommendation:** Support `http://localhost:*` or per-community dev origins

### 5. Cost Tracking & Attribution ✓ **WELL SUPPORTED**

**Current Solution:**
```yaml
openrouter_api_key_env_var: "OPENROUTER_API_KEY_HED"
```

**Status:** ✓ Well supported
**Gap:** No usage dashboard or cost reports

### 6. Model Selection/Changes ⚠️ **NEEDS IMPROVEMENT**

**Scenario:** Community wants to use a better model

**Current Solution:**
```yaml
default_model: "anthropic/claude-3.5-sonnet"
default_model_provider: null
```

**Issues:**
- ❌ No validation that model exists/is available
- ❌ No cost estimation for model change
- ❌ No A/B testing support (50% old model, 50% new model)
- ❌ Can't test new model before switching for all users

**Recommendation:** Add model validation + gradual rollout support

### 7. Configuration Updates ⚠️ **CRITICAL GAP**

**Scenario:** Community wants to add a new CORS origin

**Current Flow:**
```
1. Edit config.yaml in git
2. Create PR
3. Review + merge
4. Deploy to server
5. Restart server
```

**Issues:**
- ❌ Requires server restart (downtime)
- ❌ No rollback mechanism if config is bad
- ❌ No preview of changes before deploy
- ❌ Breaking changes affect all users immediately

**Recommendation:** Hot-reload config + config validation endpoint

### 8. Forgot to Set API Key ⚠️ **VERY COMMON ERROR**

**Scenario:** Community sets `openrouter_api_key_env_var` but forgets to set env var

**Current Behavior:**
```
- Logs warning: "env var not set, falling back to platform key"
- Widget users: Works (uses platform key)
- CLI users: Works if they provide BYOK
```

**Issues:**
- ⚠️ Silent fallback - community thinks it's using their key but isn't
- ❌ No alert/notification about missing key
- ❌ Costs go to platform instead of community

**Recommendation:** Fail fast with clear error, or send alert

### 9. Invalid/Expired API Key ⚠️ **COMMON**

**Scenario:** Community's OpenRouter API key expires or is invalid

**Current Behavior:**
- First user request fails
- Error logged to server
- User sees generic error

**Issues:**
- ❌ No proactive validation of API keys
- ❌ No alert when key stops working
- ❌ Community doesn't know until users complain

**Recommendation:** Periodic API key validation + alerts

### 10. Documentation Sync ⚠️ **ONGOING MAINTENANCE**

**Scenario:** Community updates their documentation, wants it re-synced

**Current Flow:**
```
1. Wait for automated sync (GitHub: daily, Papers: weekly)
2. OR manually trigger: POST /sync/trigger
```

**Issues:**
- ❌ Manual trigger requires API key auth (community can't trigger)
- ❌ No way to sync just one community
- ❌ No way to see sync status per community
- ❌ No webhook to trigger sync on doc update

**Recommendation:** Per-community sync trigger + webhooks

## Edge Cases

### 1. iframe Embedding

**Scenario:** Community embeds widget in an iframe

**Current Behavior:**
- Origin header = iframe parent domain
- Might not match community's cors_origins

**Status:** ⚠️ Might break depending on setup

**Recommendation:** Test + document iframe requirements

### 2. Mobile App Integration

**Scenario:** Community wants widget in React Native/Flutter app

**Current Behavior:**
- No Origin header → requires BYOK
- Mobile app users must provide API keys

**Status:** ✓ Works but requires BYOK

**Gap:** No mobile-specific auth method (app bundles can't hide keys)

**Recommendation:** Consider mobile SDK with OAuth

### 3. API Documentation Testing (Swagger, Postman)

**Scenario:** Developer testing API via Swagger UI or Postman

**Current Behavior:**
- No Origin header → requires BYOK

**Status:** ✓ Correct behavior

**Gap:** No way to generate temporary test keys

**Recommendation:** Add "Generate Test Key" in admin panel

### 4. Rate Limiting

**Scenario:** Single community dominates platform usage

**Current Status:**
- ❌ No rate limiting per community
- ❌ No usage quotas
- ❌ No throttling for abusive users

**Recommendation:** Add per-community rate limits

### 5. Temporary Disable

**Scenario:** Community wants to temporarily disable their assistant

**Current Solution:**
```yaml
status: coming_soon  # Hack: removes from available list
```

**Issues:**
- ❌ Not clear this is for temporary disable
- ❌ No "maintenance mode" with custom message
- ❌ No scheduled disable/enable

**Recommendation:** Add `enabled: false` field + maintenance_message

### 6. Multiple Environments (Dev/Staging/Prod)

**Scenario:** OSA runs in dev, staging, and production environments

**Current Status:**
- ❌ Same config.yaml for all environments
- ❌ No way to use different API keys per environment
- ❌ No way to use different models per environment

**Recommendation:** Environment-specific overrides in config

### 7. Widget Customization

**Scenario:** Community wants to customize widget appearance

**Current Status:**
- ❌ No widget customization options in config
- ❌ No branding/logo support
- ❌ No color theme customization

**Recommendation:** Add `widget` section to config.yaml

### 8. Custom Error Messages

**Scenario:** Community wants custom error messages for their users

**Current Status:**
- ❌ All communities get same generic errors
- ❌ No way to customize "API key required" message
- ❌ No way to add help links

**Recommendation:** Add `error_messages` to config

### 9. Analytics & Metrics

**Scenario:** Community wants to see usage metrics

**Current Status:**
- ❌ No per-community analytics
- ❌ No usage dashboard
- ❌ No cost tracking dashboard
- ❌ No user engagement metrics

**Recommendation:** Add analytics dashboard

### 10. Custom Tools Beyond Standard Set

**Scenario:** Community has specialized validation tools

**Current Status:**
- ✓ Extensions system with python_plugins
- ⚠️ No documentation for building custom tools
- ❌ No tool marketplace or examples

**Recommendation:** Document extension system better

## Critical Gaps

### Priority 1: Must Have Before General Availability

1. **Config Validation Endpoint**
   - POST /admin/validate-config
   - Returns errors before deploying
   - Validates API keys, models, CORS origins

2. **Hot Config Reload**
   - Watch config files for changes
   - Reload without server restart
   - Rollback on error

3. **Missing API Key Alert**
   - Detect when openrouter_api_key_env_var not set
   - Send alert to admin
   - Don't silently fall back to platform key

4. **Local Development Support**
   - Support http://localhost:* wildcard
   - OR per-community dev origins
   - Clear documentation

### Priority 2: Important for Good UX

5. **Per-Community Sync Trigger**
   - Allow communities to trigger their own sync
   - No API key auth required (maybe JWT from config)
   - View sync status

6. **Temporary Disable**
   ```yaml
   enabled: false
   maintenance_message: "Upgrading to HED 9.0, back soon!"
   ```

7. **Model Validation**
   - Validate default_model exists on OpenRouter
   - Show available models
   - Estimate costs

8. **Rate Limiting**
   - Per-community request limits
   - Configurable in config.yaml
   - Graceful degradation

### Priority 3: Nice to Have

9. **Analytics Dashboard**
   - Usage metrics per community
   - Cost tracking
   - User engagement

10. **Widget Customization**
    ```yaml
    widget:
      theme_color: "#1E3A8A"
      logo_url: "https://hedtags.org/logo.png"
      position: "bottom-right"
    ```

11. **Environment Overrides**
    ```yaml
    environments:
      dev:
        default_model: "openai/gpt-oss-120b"  # Faster for dev
      prod:
        default_model: "anthropic/claude-3.5-sonnet"  # Better for prod
    ```

12. **A/B Testing**
    ```yaml
    experiments:
      - name: "test-claude-opus"
        percentage: 10  # 10% of users
        model: "anthropic/claude-opus-4"
    ```

## Onboarding Experience Comparison

### Current Experience (No Tooling)

```
Community: "We want to add our assistant"

Steps:
1. Read README
2. Create config.yaml (might have errors)
3. Email server admin to set API key env var
4. Wait for next deploy
5. Hope it works
6. If broken, check server logs
7. Email admin to fix
8. Wait for next deploy

Time: 2-3 days
Friction: High
Success Rate: ~60% (40% have config errors)
```

### Ideal Experience (With Tooling)

```
Community: "We want to add our assistant"

Steps:
1. Visit /admin/onboard
2. Fill form (validates in real-time)
3. System generates config.yaml
4. System validates API key works
5. Preview widget with test data
6. Click "Deploy"
7. Widget live in <1 minute

Time: 15 minutes
Friction: Low
Success Rate: ~95%
```

## Recommendations

### Option A: Ship Current PR + Iterate

**Pros:**
- Get CORS-based auth live quickly
- Start learning from real usage
- Fix gaps based on actual community feedback

**Cons:**
- Poor onboarding experience
- Will require manual support for each community
- Might get negative first impressions

**Recommendation:** Only if we have ≤2 communities onboarding

### Option B: Add Priority 1 Features First

**Additions Needed:**
1. Config validation endpoint (2-3 hours)
2. Hot config reload (3-4 hours)
3. Missing API key detection (1 hour)
4. Local development support (1 hour)

**Timeline:** +1 day
**Benefit:** Significantly better experience, less manual support

**Recommendation:** **This is the balanced choice**

### Option C: Build Full Onboarding System

**Additions Needed:**
- All Priority 1 features
- All Priority 2 features
- Admin UI for config management
- Analytics dashboard

**Timeline:** +1-2 weeks
**Benefit:** Professional onboarding experience

**Recommendation:** Only if planning to onboard 5+ communities soon

## Proposed Additions for This PR

Based on >20% probability use cases, I recommend adding to current PR:

### 1. Config Validation Helper (30 min)

```python
# src/api/routers/admin.py (new file)
@router.post("/validate-config")
async def validate_config(config_yaml: str):
    """Validate a community config without deploying."""
    try:
        config = CommunityConfig.model_validate(yaml.safe_load(config_yaml))

        # Validate API key works (if provided)
        if config.openrouter_api_key_env_var:
            api_key = os.getenv(config.openrouter_api_key_env_var)
            if not api_key:
                return {"valid": False, "errors": ["API key env var not set"]}
            # TODO: Test API key actually works

        # Validate model exists (if provided)
        if config.default_model:
            # TODO: Check model exists on OpenRouter
            pass

        return {"valid": True, "config": config.model_dump()}
    except Exception as e:
        return {"valid": False, "errors": [str(e)]}
```

### 2. Missing API Key Detection (15 min)

```python
# In _select_api_key()
if env_var_name:
    community_key = os.getenv(env_var_name)
    if not community_key:
        # CHANGE: Don't just warn, actually alert
        logger.error(  # Changed from warning
            "CRITICAL: Community %s configured API key %s but env var not set! "
            "Using platform key. Set env var or costs will be billed to platform.",
            community_id,
            env_var_name,
        )
        # TODO: Send alert to admin
```

### 3. localhost Wildcard Support (15 min)

```python
# In _is_authorized_origin()
# Add special case for localhost with any port
if origin and origin.startswith("http://localhost:"):
    # Check if platform allows localhost
    settings = get_settings()
    if "http://localhost" in str(settings.cors_origins):
        return True
```

### 4. Better Documentation (30 min)

- Update README with common pitfalls
- Add troubleshooting guide
- Add example configs for different scenarios

**Total Time:** ~2 hours
**Impact:** Addresses 60% of common issues

## Conclusion

**Current implementation is solid** for the authorization logic, but **onboarding experience needs work** before general availability.

**Recommendation: Option B** - Add Priority 1 features to this PR:
- Config validation
- Hot reload (or clear docs on restart required)
- Missing API key alerts
- localhost support
- Better documentation

This gives us a **professional onboarding experience** without adding weeks of work.
