# Community Onboarding Sprint

**Goal:** Make community onboarding, development, and assistance as easy, thorough, and secure as possible.

**Status:** Planning Phase - Comprehensive Review in Progress

**Date:** 2026-01-24

---

## Executive Summary

We've successfully implemented CORS-based authorization (PR #60, merged to main). Now we need to address the identified gaps to create a professional, low-friction community onboarding experience.

**Current State:**
- ✅ CORS-based authorization with BYOK support
- ✅ YAML-driven community configs
- ✅ Per-community API key management
- ✅ Model selection authorization
- ✅ Exception detail sanitization
- ✅ Fail-fast config discovery

**Target State:**
- Communities can onboard themselves in <15 minutes
- 95%+ success rate on first attempt
- Clear validation and error messages
- Secure by default
- Development-friendly (localhost support)
- Production-ready monitoring and alerts

---

## Use Cases to Address

### High Probability (>50%): Critical Path

#### UC1: New Community Onboarding
**Frequency:** Every new community (100%)
**Current Pain Points:**
- No validation before deploy → 40% failure rate
- Requires server restart → downtime
- Manual API key setup → email admin
- No preview/testing → hope it works
- Errors only in server logs → hard to debug

**Requirements:**
1. Self-service config validation
2. Real-time error feedback
3. API key validation (does it work?)
4. Preview before deploy
5. No server restart needed
6. Clear documentation

#### UC2: Local Development Testing
**Frequency:** Every developer, every session (100%)
**Current Pain Points:**
- Must hardcode localhost:PORT in config
- Developers use random ports (3000, 3001, 5173, 8080, etc.)
- Platform CORS applies to ALL communities
- No way for community to add their own dev origins

**Requirements:**
1. Support `http://localhost:*` wildcard pattern
2. Per-community dev origins (optional)
3. Clear dev setup documentation
4. Works with all common dev servers (Vite, webpack-dev-server, etc.)

#### UC3: Configuration Updates
**Frequency:** Monthly per community (~30-50%)
**Current Pain Points:**
- Edit YAML → PR → review → merge → deploy → restart
- No validation until deployed
- No rollback if config breaks
- Breaking changes affect all users immediately
- Downtime during restart

**Requirements:**
1. Hot config reload (no restart)
2. Validation before commit
3. Rollback on error
4. Gradual rollout option
5. Config version history

#### UC4: API Key Management
**Frequency:** Setup + occasional rotation (~30%)
**Current Pain Points:**
- Manual env var setup → email admin
- Silent fallback to platform key if not set
- No proactive validation
- No alert when key expires/fails
- Cost attribution unclear

**Requirements:**
1. Detect missing API key at startup
2. Validate key actually works (test API call)
3. Alert on key expiration/failure
4. Clear cost attribution in logs
5. Key rotation process documented

### Medium Probability (20-50%): Important UX

#### UC5: Multiple Domains (www vs non-www)
**Frequency:** ~40% of communities
**Pattern:**
```yaml
cors_origins:
  - https://hedtags.org
  - https://www.hedtags.org
```

**Current:** Works but tedious
**Enhancement:** Support `https://{www.,}hedtags.org` pattern?

#### UC6: Preview/Staging Environments
**Frequency:** ~40% of communities
**Pattern:**
```yaml
cors_origins:
  - https://hedtags.org              # Production
  - https://*.pages.dev               # Previews (Cloudflare Pages)
  - https://*.vercel.app              # Previews (Vercel)
```

**Current:** Wildcard support works ✅
**Gap:** No way to use different models/settings for preview vs prod

#### UC7: Model Selection/Changes
**Frequency:** ~30% want custom models
**Current Pain Points:**
- No validation that model exists
- No cost estimation
- Can't test new model before switching all users
- No A/B testing support

**Requirements:**
1. Validate model exists on OpenRouter
2. Show available models + costs
3. Model testing endpoint
4. Gradual rollout/A/B testing

#### UC8: Temporary Maintenance Mode
**Frequency:** ~20% need this occasionally
**Current Workaround:**
```yaml
status: coming_soon  # Removes from available list
```

**Issues:**
- Not clear this is for maintenance
- No custom message to users
- No scheduled enable/disable

**Requirements:**
```yaml
enabled: false
maintenance_message: "Upgrading to HED 9.0, back soon!"
scheduled_enable: "2026-01-25T10:00:00Z"
```

#### UC9: Documentation Sync Updates
**Frequency:** Weekly to monthly per community (~30%)
**Current Flow:**
1. Docs updated in GitHub
2. Wait for automated sync (daily)
3. OR email admin to trigger manual sync

**Requirements:**
1. Per-community sync trigger (webhook)
2. View sync status/history
3. Manual trigger option (authenticated)
4. Sync error notifications

### Low Probability (10-20%): Edge Cases to Consider

#### UC10: iframe Embedding
**Frequency:** ~15% of communities
**Issue:** Origin header = iframe parent domain
**Status:** Needs testing + documentation

#### UC11: Mobile App Integration
**Frequency:** ~10-15%
**Current:** Works with BYOK, no platform key support
**Gap:** Mobile apps can't hide keys securely
**Recommendation:** OAuth flow or mobile SDK (future)

#### UC12: Rate Limiting / Abuse Prevention
**Frequency:** ~20% will hit this
**Current:** No rate limiting at all
**Requirements:**
```yaml
rate_limits:
  requests_per_minute: 60
  requests_per_day: 10000
  burst_size: 100
```

#### UC13: Custom Error Messages
**Frequency:** ~15% want branding
**Gap:** All communities get same generic errors
**Enhancement:**
```yaml
error_messages:
  api_key_required: "Get your free key at hedtags.org/api"
  rate_limited: "HED API is temporarily busy, try again in 1 minute"
```

#### UC14: Multiple Environments (dev/staging/prod)
**Frequency:** ~20% run multiple instances
**Gap:** Same config.yaml for all environments
**Enhancement:**
```yaml
environments:
  dev:
    default_model: "openai/gpt-oss-120b"  # Faster/cheaper
  prod:
    default_model: "anthropic/claude-3.5-sonnet"  # Better quality
```

#### UC15: Forgot to Set API Key
**Frequency:** ~30% on first setup
**Current Behavior:**
- Logs warning
- Falls back to platform key
- Community thinks it's using their key
- Platform gets charged

**Status:** Partially fixed (fail-fast added)
**Remaining:** Need startup validation + clearer error

---

## Security Considerations

### Threat Model

**T1: API Key Exposure in Config**
- **Risk:** Low (config files in private repo)
- **Mitigation:** Keys in env vars, not YAML ✅
- **Status:** Secure

**T2: Information Disclosure via Errors**
- **Risk:** Medium (stack traces, internal paths)
- **Mitigation:** Sanitized exception messages ✅
- **Status:** Fixed in PR #60

**T3: Unauthorized API Usage**
- **Risk:** High (abuse, cost)
- **Mitigation:** CORS + BYOK + rate limiting
- **Status:** CORS ✅, rate limiting ⏳

**T4: CORS Bypass**
- **Risk:** Medium (misconfiguration)
- **Mitigation:** Strict origin matching, no wildcards except subdomains
- **Status:** Implemented ✅

**T5: Model Injection**
- **Risk:** Medium (custom models without BYOK)
- **Mitigation:** Require BYOK for custom models ✅
- **Status:** Implemented ✅

**T6: Configuration Injection**
- **Risk:** Low (YAML parsing)
- **Mitigation:** Pydantic validation
- **Status:** Secure ✅

**T7: Rate Limiting Bypass**
- **Risk:** Medium (no rate limits)
- **Mitigation:** Need to implement
- **Status:** ⏳ TODO

**T8: Localhost Wildcard Abuse**
- **Risk:** Low (localhost is local)
- **Mitigation:** Only in dev/testing, not production
- **Status:** Need to implement carefully

---

## Priority 1: Must Have Before GA

### 1. Config Validation Endpoint

**Endpoint:** `POST /admin/validate-config`

**Purpose:** Validate community config before deployment

**Request:**
```json
{
  "config_yaml": "...",
  "test_api_key": true  // Optional: actually test API key
}
```

**Response:**
```json
{
  "valid": true,
  "config": { ... },
  "warnings": [
    "Model 'gpt-4' is expensive ($30/1M tokens)"
  ],
  "checks": {
    "yaml_structure": "✓ Valid",
    "cors_origins": "✓ 3 origins configured",
    "api_key": "✓ Key works (tested with /models endpoint)",
    "model": "✓ Model exists on OpenRouter",
    "documents": "✓ 28 documents configured"
  }
}
```

**Validations:**
1. YAML structure (Pydantic model)
2. CORS origins format
3. API key exists (env var set)
4. API key works (optional: test call to OpenRouter)
5. Model exists (query OpenRouter /models)
6. Documents/repos accessible
7. DOIs valid format
8. No obvious security issues

**Estimate:** 2-3 hours

**Files to Create/Modify:**
- `src/api/routers/admin.py` (new)
- Add to `src/api/main.py`

### 2. Localhost Wildcard Support

**Requirement:** Support `http://localhost:*` for development

**Implementation Options:**

**Option A: Special Case in CORS Check**
```python
def _is_authorized_origin(origin: str | None, community_id: str) -> bool:
    # ... existing code ...

    # Special case: localhost with any port for development
    if origin and origin.startswith("http://localhost:"):
        settings = get_settings()
        # Check if platform allows localhost
        if any("localhost" in str(o) for o in settings.cors_origins):
            return True

    # ... rest of existing code ...
```

**Option B: Per-Community Dev Origins**
```yaml
# In config.yaml
cors_origins:
  - https://hedtags.org
  - https://www.hedtags.org

dev_origins:  # Only in dev environment
  - http://localhost:*
  - http://127.0.0.1:*
```

**Recommendation:** Option A (simpler, matches common pattern)

**Security Notes:**
- Localhost is always local machine → low risk
- Only works if platform has localhost in CORS list
- Document that this is for development only

**Estimate:** 1 hour

**Files to Modify:**
- `src/api/routers/community.py` (lines 150-195)
- `tests/test_api/test_cors.py` (add test cases)

### 3. Enhanced API Key Detection

**Current:** We added fail-fast, but need better operational visibility

**Enhancements:**

1. **Startup Validation**
```python
# In discover_assistants()
for config in discovered:
    if config.openrouter_api_key_env_var:
        key = os.getenv(config.openrouter_api_key_env_var)
        if not key:
            logger.error(
                "CRITICAL: Community '%s' has openrouter_api_key_env_var='%s' "
                "but environment variable is not set! "
                "Set the variable or costs will be billed to platform.",
                config.id,
                config.openrouter_api_key_env_var
            )
            # Option: fail-fast or warn?
```

2. **Runtime Logging**
```python
# In _select_api_key()
if not community_key and env_var_name:
    logger.error(
        "Community %s API key missing: %s not set. Using platform key. "
        "COSTS WILL BE BILLED TO PLATFORM, NOT COMMUNITY.",
        community_id,
        env_var_name,
        extra={
            "community_id": community_id,
            "env_var": env_var_name,
            "using_platform_key": True,
            "alert": True  # For monitoring systems
        }
    )
```

3. **Health Check Endpoint**
```python
@router.get("/health/communities")
async def community_health():
    """Check health of all community configurations."""
    health = {}
    for community in registry.list_all():
        config = community.community_config
        api_key_status = "not_configured"

        if config.openrouter_api_key_env_var:
            key = os.getenv(config.openrouter_api_key_env_var)
            api_key_status = "configured" if key else "missing"

        health[community.id] = {
            "api_key": api_key_status,
            "cors_origins": len(config.cors_origins),
            "documents": len(config.knowledge.documents),
        }

    return health
```

**Estimate:** 1 hour

**Files to Modify:**
- `src/assistants/__init__.py` (startup validation)
- `src/api/routers/community.py` (runtime logging)
- `src/api/routers/admin.py` (health check)

### 4. Hot Config Reload (Optional)

**Current:** Requires server restart to load config changes

**Goal:** Reload configs without restart

**Implementation:**

1. **File Watcher**
```python
# src/api/config_watcher.py
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class ConfigFileHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith("config.yaml"):
            try:
                config = CommunityConfig.from_yaml(event.src_path)
                registry.reload_config(config)
                logger.info("Reloaded config: %s", config.id)
            except Exception as e:
                logger.error("Failed to reload %s: %s", event.src_path, e)
                # Keep old config, don't break
```

2. **Registry Reload Method**
```python
# src/assistants/registry.py
def reload_config(self, config: CommunityConfig) -> None:
    """Reload a single community config without restart."""
    # Validate first
    if config.id not in self._assistants:
        raise ValueError(f"Community {config.id} not registered")

    # Update in-place
    old_config = self._assistants[config.id].community_config
    self._assistants[config.id].community_config = config

    logger.info(
        "Reloaded config for %s (was: %s origins, now: %s origins)",
        config.id,
        len(old_config.cors_origins),
        len(config.cors_origins)
    )
```

**Risks:**
- In-flight requests might use old config
- Need atomic updates
- Race conditions

**Recommendation:** Skip for now, requires careful implementation

**Alternative:** Clear documentation on restart process

---

## Priority 2: Important for Good UX

### 5. Temporary Maintenance Mode

**Config Addition:**
```yaml
# In CommunityConfig
enabled: true  # Default
maintenance_message: null  # Optional custom message
```

**Implementation:**
```python
# In community router
community_info = registry.get(community_id)
if not community_info.community_config.enabled:
    message = (
        community_info.community_config.maintenance_message
        or f"{community_id} assistant is temporarily unavailable. Please try again later."
    )
    raise HTTPException(status_code=503, detail=message)
```

**Estimate:** 2 hours

### 6. Model Validation

**Features:**
1. Validate model exists on OpenRouter
2. List available models
3. Show costs

**Endpoints:**
```python
@router.get("/admin/models")
async def list_models():
    """List available OpenRouter models with costs."""
    # Call OpenRouter /models API
    # Return formatted list

@router.post("/admin/validate-model")
async def validate_model(model: str):
    """Validate a model exists and show details."""
    # Query OpenRouter
    # Return exists, pricing, context_length, etc.
```

**Estimate:** 3 hours

### 7. Rate Limiting

**Config Addition:**
```yaml
rate_limits:
  requests_per_minute: 60
  requests_per_day: 10000
  burst_size: 100
```

**Implementation:**
- Use `slowapi` or similar
- Per-community limits
- Return 429 with Retry-After header

**Estimate:** 4 hours

### 8. Per-Community Sync Trigger

**Endpoint:** `POST /admin/sync/{community_id}`

**Requirements:**
- Trigger GitHub sync for one community
- Trigger papers sync for one community
- View sync status/history
- Webhook support (GitHub → trigger sync on push)

**Estimate:** 3 hours

---

## Priority 3: Nice to Have

### 9. Analytics Dashboard
- Usage metrics per community
- Cost tracking
- User engagement

**Estimate:** 1-2 days

### 10. Widget Customization
```yaml
widget:
  theme_color: "#1E3A8A"
  logo_url: "https://hedtags.org/logo.png"
  position: "bottom-right"
```

**Estimate:** 4 hours

### 11. Environment Overrides
```yaml
environments:
  dev:
    default_model: "openai/gpt-oss-120b"
  prod:
    default_model: "anthropic/claude-3.5-sonnet"
```

**Estimate:** 3 hours

### 12. A/B Testing
```yaml
experiments:
  - name: "test-claude-opus"
    percentage: 10
    model: "anthropic/claude-opus-4"
```

**Estimate:** 5 hours

---

## Documentation Needs

### 1. Community Onboarding Guide
**File:** `docs/community-onboarding.md`

**Sections:**
1. Prerequisites
2. Create config.yaml
3. Set environment variables
4. Validate configuration
5. Test locally
6. Deploy to production
7. Monitor and troubleshoot

### 2. Configuration Reference
**File:** `docs/config-reference.md`

**Sections:**
- All YAML fields explained
- Examples for common scenarios
- Security best practices
- Troubleshooting

### 3. Development Guide
**File:** `docs/development.md`

**Sections:**
- Local setup
- Testing widget locally
- Adding custom tools
- Debugging

---

## Sprint Plan

### Phase 1: Validation & Dev Experience (Day 1)
1. Config validation endpoint (3h)
2. Localhost wildcard support (1h)
3. Enhanced API key detection (1h)
4. Documentation (2h)

**Goal:** Developers can validate configs and test locally

### Phase 2: Operational Improvements (Day 2)
5. Temporary maintenance mode (2h)
6. Model validation (3h)
7. Health check endpoint (1h)
8. Documentation updates (1h)

**Goal:** Better operational control and visibility

### Phase 3: Protection & Scaling (Day 3)
9. Rate limiting (4h)
10. Per-community sync trigger (3h)
11. Testing & refinement (1h)

**Goal:** Production-ready with abuse prevention

---

## Success Metrics

**Before Sprint:**
- Onboarding time: 2-3 days
- Success rate: ~60%
- Manual intervention: Required
- Developer friction: High

**After Sprint:**
- Onboarding time: <15 minutes
- Success rate: >95%
- Manual intervention: Optional
- Developer friction: Low

**Measurement:**
- Track onboarding attempts vs successes
- Time from config creation to first successful request
- Number of validation errors caught
- Developer satisfaction survey

---

## Open Questions for Review

1. **Hot config reload:** Worth the complexity? Or document restart process well?
2. **Localhost wildcard:** Platform-level or per-community?
3. **API key validation:** Test on startup (adds ~1s) or lazy validation?
4. **Rate limiting:** Default limits? Per-community override?
5. **Model validation:** Cache OpenRouter model list? How often to refresh?
6. **Sync triggers:** Require auth? Or allow unauthenticated per-community trigger?
7. **Mobile apps:** Punt to future (OAuth flow)? Or support now?
8. **iframe embedding:** Document limitations? Or add special support?
9. **Environment overrides:** Needed now or wait for user request?
10. **A/B testing:** Scope creep or valuable feature?

---

## Next Steps

1. **Review this document** - Are we missing any >20% use cases?
2. **Thorough review** - Run security, UX, and architecture review
3. **Prioritize** - Confirm Priority 1-3 breakdown
4. **Create issues** - One per feature with acceptance criteria
5. **Implement** - Start with Priority 1
6. **Test** - With real community onboarding scenario
7. **Document** - Update guides as we build
8. **Deploy** - Staged rollout with monitoring

---

## Review Checklist

- [ ] All >20% use cases covered?
- [ ] Security threats identified and mitigated?
- [ ] Developer experience smooth?
- [ ] Production operational needs met?
- [ ] Clear error messages and debugging?
- [ ] Documentation plan complete?
- [ ] Success metrics defined?
- [ ] Testing strategy defined?
- [ ] Deployment plan defined?
- [ ] Monitoring and alerts planned?

---

# APPENDIX: Review Findings Scrutiny

**Date:** 2026-01-24
**Reviewers:** 4 specialized agents (Security, Use Cases, Developer Experience, Operations)
**Total Findings:** 26 security issues, 20+ use cases, 10+ DX gaps, 15+ ops gaps

**Purpose:** Systematically evaluate each finding to determine:
1. Is the issue real?
2. Is it already addressed?
3. What's the actual priority?
4. Should we implement, document, or dismiss?

---

## Scrutiny Methodology

For each finding, we evaluate:
- **Reality Check:** Is this a real problem in our context?
- **Already Addressed:** Have we already fixed/mitigated this?
- **False Positive:** Is the agent misunderstanding our architecture?
- **Actual Impact:** What's the real-world impact?
- **Recommendation:** Implement, document, or dismiss

---

## Security Findings Scrutiny

### T1-T8: Original Threat Model (Already in Sprint Doc)

| ID | Threat | Status | Notes |
|---|---|---|---|
| T1 | API Key Exposure in Config | ✅ SECURED | Keys in env vars, not YAML |
| T2 | Information Disclosure via Errors | ✅ FIXED (PR #60) | Exception sanitization implemented |
| T3 | Unauthorized API Usage | ⚠️ PARTIAL | CORS ✅, rate limiting ⏳ |
| T4 | CORS Bypass | ✅ MITIGATED | Strict matching, documented wildcards |
| T5 | Model Injection | ✅ SECURED | BYOK required for custom models |
| T6 | Configuration Injection | ✅ SECURED | Pydantic validation |
| T7 | Rate Limiting Bypass | ❌ TODO | No rate limiting yet |
| T8 | Localhost Wildcard Abuse | ⚠️ NEEDS SCRUTINY | See detailed analysis below |

---

### New Security Findings (T9-T22) - Detailed Scrutiny

#### T9: Timing Attack on CORS Origin Validation
**Agent Claim:** Medium severity, CORS validation timing can leak configured origins
**Reality Check:** 🟡 THEORETICAL - Very low practical risk
- CORS origin list is public information (in config.yaml in public repo)
- Timing differences negligible (microseconds)
- No sensitive data exposed
- Attack requires precise timing measurement (difficult in browser)

**Already Addressed:** N/A
**Recommendation:** ✖️ DISMISS - Not a real threat in our context
**Justification:** Config is already public, timing attack gains nothing

---

#### T10: Environment Variable Injection via config.yaml
**Agent Claim:** HIGH severity, arbitrary env var access
**Code Reference:**
```yaml
openrouter_api_key_env_var: "AWS_SECRET_ACCESS_KEY"  # Could read any env var!
```

**Reality Check:** 🔴 REAL ISSUE
- Community with PR access could reference platform secrets
- Silent access to sensitive variables
- Logs would expose env var names

**Already Addressed:** ❌ NO
**Recommendation:** ✅ IMPLEMENT - Add validation
```python
@field_validator("openrouter_api_key_env_var")
@classmethod
def validate_api_key_env_var(cls, v: str | None) -> str | None:
    if v is None:
        return None
    if not re.match(r"^OPENROUTER_API_KEY_[A-Z0-9_]+$", v):
        raise ValueError(
            f"Invalid env var name '{v}'. Must match pattern: OPENROUTER_API_KEY_*"
        )
    return v
```

**Priority:** P1 (HIGH)
**Effort:** 30 minutes

---

#### T11: YAML Bomb / Resource Exhaustion
**Agent Claim:** Medium severity, DoS via malicious YAML
**Reality Check:** 🟡 REAL BUT MITIGATED
- Pydantic has built-in limits on nesting depth
- Config files are in git (requires PR approval)
- Platform admin reviews all configs before merge
- Not publicly writable

**Already Addressed:** ⚠️ PARTIAL (PR review is mitigation)
**Recommendation:** ✅ ADD LIMITS (defense in depth)
```python
# In CommunityConfig model
cors_origins: list[str] = Field(..., max_length=100)
documentation: list[DocumentationSource] = Field(..., max_length=1000)
```

**Priority:** P2 (MEDIUM)
**Effort:** 15 minutes

---

#### T12: Session Hijacking via Predictable Session IDs
**Agent Claim:** Medium severity, user-controlled session IDs
**Reality Check:** 🟡 REAL BUT LOW IMPACT
- Sessions only contain conversation history (no auth, no PII)
- User can provide session_id for continuity
- "Hijacking" just means seeing someone else's questions
- No sensitive data, no financial impact

**Already Addressed:** ❌ NO
**Recommendation:** ⚠️ DOCUMENT + OPTIONAL ENHANCEMENT
- Document: Sessions are for convenience, not security
- Optional: Use cryptographic session IDs server-side
- Not a blocker for GA

**Priority:** P3 (LOW)
**Effort:** 2 hours

---

#### T13: Localhost Wildcard CORS Bypass in Production
**Agent Claim:** HIGH severity if deployed to production
**Reality Check:** 🔴 REAL CONCERN

**HOWEVER - Critical Misunderstanding Identified:**
The agent's concern is valid BUT we discovered:
1. **Backend BYOK already works from any origin** (bypasses CORS entirely)
2. **The "localhost problem" only affects widget users without BYOK**
3. **Widget doesn't support BYOK yet** ← This is the real issue

**Root Cause Analysis:**
- Backend: BYOK works perfectly ✅
- Widget: No BYOK support ❌
- Therefore: Widget devs testing locally hit CORS issues

**Already Addressed:** ❌ NO
**Recommendation:** ✅ TWO-PART FIX

**Part 1 (Documentation - Immediate):**
- Document that widget local testing requires localhost in platform CORS
- Or use backend API directly with BYOK

**Part 2 (Widget Enhancement - P1):**
- Add BYOK support to widget
- Add settings menu with:
  - API key input (BYOK)
  - Model selection dropdown + custom option

**Priority:** P1 (HIGH) for widget enhancement
**Effort:** 4 hours (widget settings menu)

---

#### T14: Insufficient Input Validation on Model Names
**Agent Claim:** Medium severity, injection risk
**Reality Check:** 🟡 REAL BUT LOW RISK
- Model names passed to OpenRouter
- OpenRouter validates models (rejects invalid)
- Worst case: Error message from OpenRouter
- No injection risk (not executed, just passed as string)

**Already Addressed:** ⚠️ PARTIAL (OpenRouter validates)
**Recommendation:** ✅ ADD BASIC VALIDATION (good practice)
```python
if not re.match(r"^[a-z0-9\-]+/[a-z0-9\-\.]+$", model_name):
    raise ValueError("Invalid model name format")
```

**Priority:** P2 (MEDIUM)
**Effort:** 15 minutes

---

#### T15: API Key Leakage in Logs
**Agent Claim:** HIGH severity, keys in logs
**Reality Check:** 🔴 REAL RISK
- Debug logging might accidentally include key values
- Logs shipped to external services
- Centralized logging = wider exposure

**Already Addressed:** ⚠️ PARTIAL (we log carefully but no redaction)
**Recommendation:** ✅ IMPLEMENT LOG REDACTION
```python
# Custom logger formatter
class SecureFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)
        # Redact API keys (show last 4 chars only)
        msg = re.sub(r'sk-or-v1-[a-f0-9]{64}', 'sk-or-v1-***[redacted]', msg)
        return msg
```

**Priority:** P1 (HIGH)
**Effort:** 1 hour

---

#### T16: No Rate Limiting on Config Validation Endpoint
**Agent Claim:** Medium severity, DoS on validation endpoint
**Reality Check:** 🟡 REAL BUT NOT URGENT
- Endpoint doesn't exist yet (planned)
- When implemented, needs rate limiting
- Standard practice for admin endpoints

**Already Addressed:** N/A (endpoint not implemented)
**Recommendation:** ✅ IMPLEMENT WHEN BUILDING ENDPOINT
- Include rate limiting in initial implementation
- Require admin auth

**Priority:** P1 (include in endpoint implementation)
**Effort:** Included in endpoint work

---

#### T17: SSRF via Documentation source_url
**Agent Claim:** Medium severity, internal network probing
**Reality Check:** 🔴 REAL RISK
```yaml
documentation:
  - source_url: "http://169.254.169.254/latest/meta-data/"  # AWS metadata!
```

**Already Addressed:** ❌ NO
**Recommendation:** ✅ IMPLEMENT URL VALIDATION
```python
# In DocumentationSource validation
@field_validator("source_url")
@classmethod
def validate_source_url(cls, v: str | None) -> str | None:
    if v is None:
        return None

    # Parse URL
    parsed = urllib.parse.urlparse(v)

    # Only allow HTTP/HTTPS
    if parsed.scheme not in ['http', 'https']:
        raise ValueError("Only HTTP(S) URLs allowed")

    # Block private IP ranges
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL")

    # Resolve to IP
    try:
        ip = socket.gethostbyname(hostname)
        if ipaddress.ip_address(ip).is_private:
            raise ValueError("Private IP addresses not allowed")
    except:
        raise ValueError("Cannot resolve hostname")

    return v
```

**Priority:** P1 (HIGH)
**Effort:** 1 hour

---

#### T18: Session Store Memory Exhaustion
**Agent Claim:** Medium severity, no global session limit
**Reality Check:** 🟡 REAL BUT MITIGATED
- Current limit: 1000 sessions per community
- Max 20 communities = 20,000 sessions max
- Each session ~1MB = 20GB theoretical max
- Unlikely to hit in practice

**Already Addressed:** ⚠️ PARTIAL (per-community limits exist)
**Recommendation:** ⚠️ MONITOR + OPTIONAL GLOBAL LIMIT
- Add memory monitoring
- Document limits
- Add global limit if needed later

**Priority:** P2 (MEDIUM)
**Effort:** 30 minutes (monitoring)

---

#### T19: CORS Wildcard Subdomain Takeover
**Agent Claim:** Medium severity, `*.pages.dev` exploitable
**Reality Check:** 🟡 REAL BUT ACCEPTABLE RISK
```yaml
cors_origins:
  - https://*.pages.dev  # Anyone can create xyz.pages.dev
```

**Analysis:**
- Yes, attacker can create `malicious.pages.dev`
- BUT: Would need to convince users to visit their site
- AND: CORS only matters if user is on attacker's site
- This is a **documented trade-off** for preview environments

**Already Addressed:** ⚠️ DOCUMENTED RISK
**Recommendation:** ✅ DOCUMENT CLEARLY
- Explain wildcard risks in config validation warnings
- Suggest specific subdomain patterns when possible
- Monitor usage per origin

**Priority:** P2 (DOCUMENT)
**Effort:** 30 minutes (documentation)

---

#### T20: No Protection Against Model Cost Manipulation
**Agent Claim:** Medium severity, unexpected platform costs
**Reality Check:** 🔴 REAL FINANCIAL RISK
```yaml
default_model: "anthropic/claude-opus-4"  # $15/1M tokens
openrouter_api_key_env_var: null  # Uses platform key!
```

**Already Addressed:** ⚠️ PARTIAL (warning exists, not enforced)
**Recommendation:** ✅ IMPLEMENT VALIDATION + ALERTS
1. Warn loudly during config validation
2. Require BYOK for expensive models (>$5/1M tokens)
3. Add cost monitoring dashboard

**Priority:** P1 (HIGH) - Financial impact
**Effort:** 2 hours

---

#### T21: Path Traversal in Plugin Module Loading
**Agent Claim:** Medium severity, arbitrary module loading
**Reality Check:** 🟡 THEORETICAL RISK
- Plugins require PR access (admin control)
- Platform admin reviews all changes
- Python import restrictions apply

**Already Addressed:** ⚠️ PARTIAL (PR review)
**Recommendation:** ✅ ADD VALIDATION (defense in depth)
```python
@field_validator("module")
@classmethod
def validate_module_path(cls, v: str) -> str:
    if not re.match(r"^src\.assistants\.[a-z0-9\-]+\.tools$", v):
        raise ValueError("Plugin module must be in src.assistants.{community}.tools")
    return v
```

**Priority:** P2 (MEDIUM)
**Effort:** 15 minutes

---

#### T22: Denial of Service via Message Length
**Agent Claim:** Low-Medium severity, memory exhaustion
**Reality Check:** 🟢 ALREADY MITIGATED
- `MAX_MESSAGE_LENGTH = 10000` ✅
- `MAX_MESSAGES_PER_SESSION = 100` ✅
- `MAX_SESSIONS_PER_COMMUNITY = 1000` ✅

**Already Addressed:** ✅ YES
**Recommendation:** ✅ NO ACTION NEEDED
**Note:** Existing limits are sufficient

---

## Security Findings Summary Table

| ID | Issue | Severity | Real? | Addressed? | Priority | Effort |
|----|-------|----------|-------|------------|----------|--------|
| T9 | Timing Attack | LOW | ❌ FALSE | N/A | DISMISS | 0h |
| T10 | Env Var Injection | HIGH | ✅ YES | ❌ NO | P1 | 0.5h |
| T11 | YAML Bomb | MEDIUM | ⚠️ PARTIAL | ⚠️ PARTIAL | P2 | 0.25h |
| T12 | Session Hijacking | LOW | ⚠️ LOW IMPACT | ❌ NO | P3 | 2h |
| T13 | Localhost CORS | HIGH | ✅ YES* | ❌ NO | P1 | 4h |
| T14 | Model Name Injection | LOW | ⚠️ LOW RISK | ⚠️ PARTIAL | P2 | 0.25h |
| T15 | API Key Logs | HIGH | ✅ YES | ⚠️ PARTIAL | P1 | 1h |
| T16 | Validation Endpoint DoS | MEDIUM | ⚠️ FUTURE | N/A | P1 | incl |
| T17 | SSRF | HIGH | ✅ YES | ❌ NO | P1 | 1h |
| T18 | Memory Exhaustion | MEDIUM | ⚠️ LOW RISK | ⚠️ PARTIAL | P2 | 0.5h |
| T19 | Wildcard Takeover | MEDIUM | ⚠️ ACCEPTABLE | ⚠️ DOC | P2 | 0.5h |
| T20 | Cost Manipulation | MEDIUM | ✅ YES | ⚠️ PARTIAL | P1 | 2h |
| T21 | Plugin Path Traversal | MEDIUM | ⚠️ THEORETICAL | ⚠️ PARTIAL | P2 | 0.25h |
| T22 | Message DoS | LOW | ✅ YES | ✅ YES | DONE | 0h |

**Real Issues:** 8/14 (57%)
**False Positives:** 2/14 (14%)
**Already Addressed:** 1/14 (7%)
**Need Implementation:** 7/14 (50%)

**Total P1 Effort:** ~9 hours
**Total P2 Effort:** ~2 hours

---

## Use Case Findings Scrutiny

### UC1-UC15: Original Use Cases (Already in Sprint Doc)
**Status:** All valid, well-documented ✅

### UC16-UC20: New Use Cases from Review

#### UC16: Config Syntax Errors During Onboarding
**Agent Claim:** 60-70% probability, critical DX issue
**Reality Check:** ✅ ABSOLUTELY REAL
- Every community will hit this
- YAML is unforgiving
- No validation before deploy
- Errors only in server logs

**Already Addressed:** ❌ NO
**Recommendation:** ✅ IMPLEMENT - Validation CLI
**Priority:** P0 (BLOCKER for good onboarding)
**Effort:** 3 hours

---

#### UC17: API Key Permissions/Scope Issues
**Agent Claim:** 40-50% probability
**Reality Check:** ⚠️ PARTIALLY REAL
- OpenRouter keys generally work or don't (binary)
- No granular permissions like AWS IAM
- Testing "does it work" is sufficient

**Already Addressed:** ❌ NO (no validation)
**Recommendation:** ⚠️ BASIC VALIDATION SUFFICIENT
- Test key with simple API call
- Check for 401/403 errors
- Don't need complex scope checking

**Priority:** P1 (include in validation CLI)
**Effort:** Included in CLI work

---

#### UC18: Cross-Origin Issues Beyond CORS
**Agent Claim:** 35-45% probability, CSP/mixed content issues
**Reality Check:** ⚠️ OVERSTATED
- Most sites don't have restrictive CSP
- HTTPS enforcement handles mixed content
- Real issue: only affects ~10% of users

**Already Addressed:** N/A
**Recommendation:** ⚠️ DOCUMENT ONLY
- Troubleshooting guide for CSP errors
- Not worth building detection tooling

**Priority:** P2 (documentation)
**Effort:** 1 hour

---

#### UC19: Concurrent Config Updates
**Agent Claim:** 30-40% probability
**Reality Check:** ⚠️ REAL BUT RARE
- Git handles merge conflicts already
- Standard PR workflow addresses this
- Not unique to our platform

**Already Addressed:** ✅ YES (Git workflow)
**Recommendation:** ✅ DOCUMENT GIT WORKFLOW
**Priority:** P2 (documentation)
**Effort:** 30 minutes

---

#### UC20: Widget Integration Errors
**Agent Claim:** 50-60% probability
**Reality Check:** ✅ ABSOLUTELY REAL
- Wrong community ID
- Script placement issues
- No way to test before deploy

**Already Addressed:** ❌ NO
**Recommendation:** ✅ IMPLEMENT - Widget test endpoint
**Priority:** P1 (HIGH)
**Effort:** 2 hours

---

## Use Case Summary

| ID | Use Case | Probability | Real? | Addressed? | Priority | Effort |
|----|----------|-------------|-------|------------|----------|--------|
| UC16 | Config Syntax Errors | 60-70% | ✅ YES | ❌ NO | P0 | 3h |
| UC17 | API Key Permissions | 40-50% | ⚠️ PARTIAL | ❌ NO | P1 | incl |
| UC18 | CSP/Mixed Content | 35-45% | ⚠️ OVERSTATED | N/A | P2 | 1h |
| UC19 | Concurrent Updates | 30-40% | ⚠️ RARE | ✅ GIT | P2 | 0.5h |
| UC20 | Widget Integration | 50-60% | ✅ YES | ❌ NO | P1 | 2h |

**Total P0 Effort:** 3 hours
**Total P1 Effort:** 2 hours
**Total P2 Effort:** 1.5 hours

---

## Developer Experience Findings Scrutiny

### DX1: No Pre-Deploy Validation
**Agent Claim:** Affects 100%, critical friction
**Reality Check:** ✅ ABSOLUTELY TRUE
- 40% failure rate confirmed
- Multiple deploy cycles
- High friction

**Already Addressed:** ❌ NO
**Recommendation:** ✅ VALIDATION CLI
**Priority:** P0
**Effort:** 3 hours (same as UC16)

---

### DX2: Server Restart Required
**Agent Claim:** Affects 100%, high friction
**Reality Check:** ✅ TRUE BUT ACCEPTABLE
- Standard for config changes
- Hot reload is complex
- Document restart process instead

**Already Addressed:** N/A
**Recommendation:** ⚠️ DOCUMENT (don't implement hot reload yet)
**Priority:** P1 (documentation)
**Effort:** 30 minutes

---

### DX3: Localhost Port Issues
**Agent Claim:** Affects 100% of developers
**Reality Check:** ❌ FALSE - Only affects widget testers without BYOK
- Backend BYOK works from any origin ✅
- Widget doesn't support BYOK ❌ ← Real issue
- Fix: Add BYOK to widget

**Already Addressed:** ❌ NO (widget limitation)
**Recommendation:** ✅ ADD BYOK TO WIDGET + SETTINGS MENU
**Priority:** P1
**Effort:** 4 hours

**Settings Menu Features:**
1. API key input (BYOK)
2. Model selection dropdown with suggestions
3. "Custom" model option for other models

---

### DX4: API Key Validation Gap
**Agent Claim:** Affects 50%
**Reality Check:** ✅ TRUE (silent fallback is bad)
**Already Addressed:** ⚠️ PARTIAL (warning exists)
**Recommendation:** ✅ ENHANCE (fail-fast option)
**Priority:** P1
**Effort:** 1 hour

---

### DX5: No Documentation
**Agent Claim:** Affects 100%
**Reality Check:** ✅ TRUE
- No step-by-step guide
- No troubleshooting docs
- No config reference

**Already Addressed:** ❌ NO
**Recommendation:** ✅ CREATE DOCS
**Priority:** P0 (blocker for self-service)
**Effort:** 4 hours

---

## Developer Experience Summary

| ID | Issue | Impact | Real? | Addressed? | Priority | Effort |
|----|-------|--------|-------|------------|----------|--------|
| DX1 | No Validation | 100% | ✅ YES | ❌ NO | P0 | 3h |
| DX2 | Restart Required | 100% | ✅ ACCEPTABLE | N/A | P1 | 0.5h |
| DX3 | Localhost Ports | Widget only | ❌ MISUNDERSTOOD | ❌ NO | P1 | 4h |
| DX4 | API Key Validation | 50% | ✅ YES | ⚠️ PARTIAL | P1 | 1h |
| DX5 | No Documentation | 100% | ✅ YES | ❌ NO | P0 | 4h |

**Total P0 Effort:** 7 hours (3h CLI + 4h docs)
**Total P1 Effort:** 5.5 hours

---

## Operations Findings Scrutiny

### OPS1: No Monitoring/Metrics
**Agent Claim:** Critical for production
**Reality Check:** ✅ TRUE BUT NOT BLOCKER
- Important for scale
- Not needed for initial GA (2-3 communities)
- Can add as usage grows

**Already Addressed:** ❌ NO
**Recommendation:** ⚠️ P2 (not required for GA)
**Priority:** P2
**Effort:** 6 hours (Prometheus + dashboards)

---

### OPS2: No Alerting
**Agent Claim:** Critical for production
**Reality Check:** ⚠️ OVERSTATED FOR SMALL SCALE
- Useful but not critical for 2-3 communities
- Can monitor manually initially
- Add as scale increases

**Already Addressed:** ❌ NO
**Recommendation:** ⚠️ P2 (add after GA)
**Priority:** P2
**Effort:** 2 hours

---

### OPS3: No Cost Tracking
**Agent Claim:** High priority
**Reality Check:** ⚠️ USEFUL BUT NOT URGENT
- LangFuse tracks tokens
- OpenRouter provides billing
- Nice to have dashboard but not required

**Already Addressed:** ⚠️ PARTIAL (LangFuse)
**Recommendation:** ⚠️ P2 (post-GA)
**Priority:** P2
**Effort:** 3 hours

---

### OPS4-15: Various Operational Gaps
**Reality Check:** All valid but NOT BLOCKERS for initial GA

**Recommendation:** Implement iteratively based on actual operational pain points

---

## Operations Summary

**Agent Findings:** 15 operational gaps identified
**Reality:** All valid for large-scale operation
**For GA (2-3 communities):** Most are P2/P3

**Immediate Needs (P1):**
- Basic structured logging (2h)
- Health check per community (1h)

**Post-GA (P2):**
- Full monitoring stack
- Alerting integration
- Cost dashboards
- Runbooks

---

## Final Scrutinized Priorities

### Priority 0: Blockers for Self-Service Onboarding

| Item | Effort | Source | Status |
|------|--------|--------|--------|
| Validation CLI | 3h | DX1, UC16 | TODO |
| Documentation (3 guides) | 4h | DX5 | TODO |
| **TOTAL P0** | **7h** | | |

---

### Priority 1: Critical for Good Experience

| Item | Effort | Source | Status |
|------|--------|--------|--------|
| Widget BYOK + Settings Menu | 4h | DX3, T13 | TODO |
| Env var validation | 0.5h | T10 | TODO |
| API key log redaction | 1h | T15 | TODO |
| SSRF protection | 1h | T17 | TODO |
| Cost manipulation protection | 2h | T20 | TODO |
| Widget integration test | 2h | UC20 | TODO |
| API key validation | 1h | DX4 | TODO |
| Basic structured logging | 2h | OPS | TODO |
| Health check per community | 1h | OPS | TODO |
| **TOTAL P1** | **15.5h** | | |

---

### Priority 2: Important but Not Urgent

| Item | Effort | Source |
|------|--------|--------|
| YAML size limits | 0.25h | T11 |
| Model name validation | 0.25h | T14 |
| Memory monitoring | 0.5h | T18 |
| Wildcard docs | 0.5h | T19 |
| Plugin path validation | 0.25h | T21 |
| Git workflow docs | 0.5h | UC19 |
| CSP troubleshooting | 1h | UC18 |
| Monitoring stack | 6h | OPS |
| Alerting | 2h | OPS |
| **TOTAL P2** | **11.25h** | |

---

## Revised Sprint Timeline

### Week 1: Core Functionality (P0 + Critical P1)

**Day 1-2: Validation & Documentation**
- [ ] Validation CLI (3h)
- [ ] Onboarding guide (2h)
- [ ] Config reference (1h)
- [ ] Troubleshooting guide (1h)

**Day 3: Widget Enhancement**
- [ ] Widget BYOK support (2h)
- [ ] Settings menu UI (2h)
  - API key input
  - Model selection dropdown
  - Custom model option

**Day 4: Security Hardening**
- [ ] Env var name validation (0.5h)
- [ ] API key log redaction (1h)
- [ ] SSRF protection (1h)
- [ ] Cost manipulation guards (2h)

**Day 5: Operations Basics**
- [ ] Structured logging (2h)
- [ ] Health check endpoint (1h)
- [ ] Widget integration test (2h)

**Week 1 Total:** ~22 hours (P0 + high-priority P1)

---

### Week 2: Polish & Post-GA (P1 remainder + P2)

**Day 1: Remaining P1**
- [ ] API key validation enhancement (1h)
- [ ] Final testing (2h)
- [ ] Documentation review (1h)

**Day 2-3: P2 Items**
- [ ] All validation enhancements (1.5h)
- [ ] Documentation (2h)
- [ ] Monitoring prep (2h)

**Day 4-5: Monitoring & Operations**
- [ ] Prometheus setup (4h)
- [ ] Basic dashboards (2h)
- [ ] Alerting (2h)

**Week 2 Total:** ~15 hours

---

## Dismissed Items (False Positives)

| ID | Item | Reason |
|----|------|--------|
| T9 | Timing Attack on CORS | Config is public, no sensitive data |
| T12 | Session Hijacking | Low impact (no auth/PII in sessions) |
| "100% of developers hit localhost issue" | BYOK already works from any origin |

---

## Key Insights from Scrutiny

1. **Security:** 57% of findings are real issues (8/14)
2. **Use Cases:** 80% are real (4/5 new cases)
3. **Developer Experience:** 60% are real blockers (3/5)
4. **Operations:** All valid but most are P2 for small scale

5. **Biggest Misunderstanding:** Localhost CORS issue
   - Agents thought all developers affected
   - Reality: Only widget testers without BYOK
   - Root cause: Widget doesn't support BYOK
   - Fix: Add BYOK to widget (4h effort)

6. **Actual Blockers for GA:** Only 2 items!
   - Validation CLI (3h)
   - Documentation (4h)

7. **Recommended Sprint:** ~22 hours (3 days) for solid GA readiness

---

## Next Steps

1. ✅ Scrutiny complete
2. ⏭️ Update priorities based on scrutiny
3. ⏭️ Create GitHub issues for P0 + P1 items
4. ⏭️ Begin implementation


---

## Widget Settings Menu - Model Specifications

### Default Model Options

The settings menu should include these models as quick-select options:

1. **OpenAI:**
   - `openai/gpt-5.2-chat`
   - `openai/gpt-5-mini`

2. **Anthropic:**
   - `anthropic/claude-haiku-4.5`
   - `anthropic/claude-sonnet-4.5`

3. **Google:**
   - `google/gemini-3-flash-preview`
   - `google/gemini-3-pro-preview`

4. **Moonshot AI:**
   - `moonshotai/kimi-k2-0905`

5. **Qwen:**
   - `qwen/qwen3-235b-a22b-2507`

### Settings Menu Design

```
┌─────────────────────────────────────┐
│ Chat Settings                       │
├─────────────────────────────────────┤
│                                     │
│ API Key (Optional)                  │
│ ┌─────────────────────────────────┐ │
│ │ sk-or-v1-...                    │ │
│ └─────────────────────────────────┘ │
│ ℹ️ Use your own OpenRouter API key  │
│                                     │
│ Model Selection                     │
│ ┌─────────────────────────────────┐ │
│ │ ○ Default (claude-sonnet-4.5)   │ │ ← Show community default
│ │ ○ GPT-5.2 Chat                  │ │
│ │ ○ GPT-5 Mini                    │ │
│ │ ○ Claude Haiku 4.5              │ │
│ │ ○ Claude Sonnet 4.5             │ │
│ │ ○ Gemini 3 Flash Preview        │ │
│ │ ○ Gemini 3 Pro Preview          │ │
│ │ ○ Kimi K2 0905                  │ │
│ │ ○ Qwen3 235B                    │ │
│ │ ○ Custom...                     │ │
│ └─────────────────────────────────┘ │
│                                     │
│ If "Custom" selected:               │
│ ┌─────────────────────────────────┐ │
│ │ provider/model-name             │ │
│ └─────────────────────────────────┘ │
│                                     │
│ [Save Settings] [Cancel]            │
└─────────────────────────────────────┘
```

### Implementation Notes

1. **Default Display:** Always show what model the community is using by default
   - Fetch from `/communities/{id}` endpoint
   - Display: "Default (model-name-here)"

2. **BYOK Behavior:**
   - If API key provided: Can select any model
   - If no API key: Can only select default or community-allowed models

3. **Persistence:**
   - Store in localStorage: `osa-settings-{communityId}`
   - Apply on every request

4. **Validation:**
   - Validate API key format: `sk-or-v1-[hex]`
   - Validate model format: `provider/model-name`
