# Community Onboarding - Phased Implementation Plan

**Sprint Goal:** Self-service community onboarding with professional developer experience

**Total Effort:** ~22 hours (P0 + P1)
**Timeline:** 3-5 days depending on review cycles
**Branch Strategy:** Feature branches from `develop`, merge back to `develop`

---

## Phase 1: Validation & Documentation (P0)

**Goal:** Enable communities to validate configs and onboard themselves
**Effort:** 7 hours
**Branch:** `feature/issue-XX-validation-and-docs`

### Issues to Create

**Issue #X1: Add config validation CLI command**
- **Type:** Feature
- **Priority:** P0 (Blocker for self-service onboarding)
- **Effort:** 3 hours
- **Description:**
  Communities need to validate their config.yaml before deploying to catch errors early.

  **Acceptance Criteria:**
  - [ ] `uv run osa validate <config-path>` command exists
  - [ ] Validates YAML syntax
  - [ ] Validates Pydantic schema
  - [ ] Checks if API key env var is set
  - [ ] Optionally tests API key works (with `--test-api-key` flag)
  - [ ] Returns clear, actionable error messages
  - [ ] Shows line numbers for YAML errors
  - [ ] Exit code 0 on success, 1 on failure

  **Implementation Details:**
  ```python
  # src/cli/validate.py
  @app.command()
  def validate(
      config_path: Path,
      test_api_key: bool = typer.Option(False, "--test-api-key"),
  ):
      """Validate a community config file."""
      # Load and validate
      # Return success/failure with details
  ```

**Issue #X2: Create community onboarding documentation**
- **Type:** Documentation
- **Priority:** P0 (Blocker for self-service onboarding)
- **Effort:** 4 hours
- **Description:**
  New communities need clear, step-by-step instructions for onboarding.

  **Acceptance Criteria:**
  - [ ] `docs/community-onboarding.md` created with:
    - Prerequisites
    - Step-by-step config creation
    - API key setup
    - Local validation
    - Deployment process
    - Troubleshooting
  - [ ] `docs/config-reference.md` created with:
    - All YAML fields explained
    - Examples for each field
    - Validation rules
    - Security best practices
  - [ ] `docs/troubleshooting.md` created with:
    - Common errors and solutions
    - Config validation failures
    - API key issues
    - CORS problems
  - [ ] All docs linked from main README

### Testing Plan
- [ ] Test validation CLI with valid config (should pass)
- [ ] Test validation CLI with invalid YAML (should fail with line number)
- [ ] Test validation CLI with missing env var (should warn)
- [ ] Test validation CLI with `--test-api-key` (should test OpenRouter)
- [ ] Review all documentation for clarity and completeness

### PR Review Focus
- Documentation clarity (no jargon, assumes beginner)
- Validation error messages are actionable
- CLI help text is clear

---

## Phase 2: Widget Settings & BYOK (P1)

**Goal:** Enable widget users to bring their own key and select models
**Effort:** 4 hours
**Branch:** `feature/issue-XX-widget-settings`

### Issue to Create

**Issue #X3: Add settings menu to widget with BYOK and model selection**
- **Type:** Feature
- **Priority:** P1 (Critical for local testing and power users)
- **Effort:** 4 hours
- **Description:**
  Widget users need ability to:
  1. Provide their own OpenRouter API key (BYOK) for local testing
  2. Select different models beyond the community default
  3. See what the current default model is

  **Acceptance Criteria:**
  - [ ] Settings icon/button in widget UI
  - [ ] Settings modal with:
    - API key input field (optional, saved to localStorage)
    - Model selection dropdown with options:
      - Default (shows community's default model)
      - openai/gpt-5.2-chat
      - openai/gpt-5-mini
      - anthropic/claude-haiku-4.5
      - anthropic/claude-sonnet-4.5
      - google/gemini-3-flash-preview
      - google/gemini-3-pro-preview
      - moonshotai/kimi-k2-0905
      - qwen/qwen3-235b-a22b-2507
      - Custom (shows text input)
    - Save/Cancel buttons
  - [ ] Settings persisted to localStorage per community
  - [ ] API key passed in `X-OpenRouter-Key` header when set
  - [ ] Model passed in request body when set
  - [ ] Fetch community default model from API
  - [ ] Clear indication of what's currently selected

  **Implementation Details:**
  ```javascript
  // Add to widget config
  const settings = {
    apiKey: localStorage.getItem('osa-api-key-hed') || null,
    model: localStorage.getItem('osa-model-hed') || null,
  };

  // Update fetch call
  fetch(`${apiEndpoint}/${communityId}/ask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(settings.apiKey && { 'X-OpenRouter-Key': settings.apiKey }),
    },
    body: JSON.stringify({
      message,
      model: settings.model,
      // ...
    }),
  });
  ```

### Testing Plan
- [ ] Settings icon appears in widget
- [ ] Settings modal opens/closes correctly
- [ ] API key saves to localStorage
- [ ] API key is included in request headers
- [ ] Model selection saves to localStorage
- [ ] Model selection is included in request
- [ ] Default model is fetched and displayed correctly
- [ ] Custom model input works
- [ ] Local testing works with BYOK (no CORS issues)

### PR Review Focus
- UI/UX: Settings are discoverable and intuitive
- Security: API key is stored safely in localStorage (not cookies)
- Privacy: API key is not logged or exposed
- Backward compatibility: Works without settings (uses defaults)

---

## Phase 3: Security Hardening (P1)

**Goal:** Close critical security gaps
**Effort:** 5 hours
**Branch:** `feature/issue-XX-security-hardening`

### Issues to Create

**Issue #X4: Add environment variable name validation**
- **Type:** Security
- **Priority:** P1 (High severity - prevents secret exposure)
- **Effort:** 30 minutes
- **Labels:** security, P1
- **Description:**
  Communities could reference arbitrary environment variables (e.g., AWS credentials) via `openrouter_api_key_env_var`. Need to restrict to safe pattern.

  **Acceptance Criteria:**
  - [ ] Pydantic validator on `openrouter_api_key_env_var` field
  - [ ] Only allows pattern: `OPENROUTER_API_KEY_*`
  - [ ] Rejects other env var names with clear error
  - [ ] Tests for valid and invalid patterns

  **Security Impact:** HIGH - Prevents access to platform secrets

**Issue #X5: Add API key log redaction**
- **Type:** Security
- **Priority:** P1 (High severity - prevents credential leakage)
- **Effort:** 1 hour
- **Labels:** security, P1
- **Description:**
  API keys might accidentally be logged in debug output or error messages. Need automatic redaction.

  **Acceptance Criteria:**
  - [ ] Custom log formatter that redacts API keys
  - [ ] Pattern: `sk-or-v1-[0-9a-f]{64}` → `sk-or-v1-***[redacted]`
  - [ ] Applied to all loggers
  - [ ] Test that API keys are redacted in logs

  **Security Impact:** HIGH - Prevents credential exposure in logs

**Issue #X6: Add SSRF protection for documentation URLs**
- **Type:** Security
- **Priority:** P1 (High severity - prevents internal network access)
- **Effort:** 1 hour
- **Labels:** security, P1
- **Description:**
  `source_url` in documentation config could be exploited to probe internal network (e.g., AWS metadata service).

  **Acceptance Criteria:**
  - [ ] Validator on `source_url` field
  - [ ] Only allows HTTP/HTTPS schemes
  - [ ] Blocks private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16)
  - [ ] Blocks localhost (127.0.0.1, ::1)
  - [ ] Tests for valid and blocked URLs

  **Security Impact:** HIGH - Prevents SSRF attacks

**Issue #X7: Add cost manipulation protection**
- **Type:** Security / Cost Management
- **Priority:** P1 (Medium severity - prevents surprise bills)
- **Effort:** 2 hours
- **Labels:** security, cost-management, P1
- **Description:**
  Communities could set expensive models without BYOK, causing unexpected platform costs.

  **Acceptance Criteria:**
  - [ ] Validate model costs during config validation
  - [ ] Warn if expensive model (>$5/1M tokens) without BYOK
  - [ ] Require BYOK for ultra-expensive models (>$15/1M tokens)
  - [ ] Add model cost lookup (query OpenRouter API or hardcoded table)
  - [ ] Clear error messages explaining cost concerns

  **Financial Impact:** MEDIUM - Prevents surprise billing

**Issue #X8: Add model name format validation**
- **Type:** Security
- **Priority:** P2 (Low severity - defense in depth)
- **Effort:** 15 minutes
- **Labels:** security, P2
- **Description:**
  Model names should follow expected format to prevent injection or confusion.

  **Acceptance Criteria:**
  - [ ] Validate model name format: `^[a-z0-9\-]+/[a-z0-9\-\.]+$`
  - [ ] Max length 100 characters
  - [ ] Reject suspicious characters
  - [ ] Tests for valid and invalid model names

### Testing Plan
- [ ] Test env var validation (valid pattern passes, invalid fails)
- [ ] Test log redaction (API keys are masked in logs)
- [ ] Test SSRF protection (private IPs blocked, public IPs allowed)
- [ ] Test cost protection (expensive models trigger warnings/errors)
- [ ] Test model name validation (valid formats pass, invalid fail)

### PR Review Focus
- Security: All validation bypasses closed
- Errors: Clear, actionable error messages
- Tests: Comprehensive coverage of edge cases

---

## Phase 4: Operational Basics (P1)

**Goal:** Enable debugging and monitoring for production
**Effort:** 3 hours
**Branch:** `feature/issue-XX-operational-basics`

### Issues to Create

**Issue #X9: Add structured logging with context**
- **Type:** Operations
- **Priority:** P1 (Critical for debugging production issues)
- **Effort:** 2 hours
- **Labels:** operations, observability, P1
- **Description:**
  Need structured logging with request context to debug issues quickly.

  **Acceptance Criteria:**
  - [ ] JSON-structured logging format
  - [ ] Include in all log entries:
    - community_id
    - session_id (if applicable)
    - origin (if applicable)
    - model (if applicable)
    - timestamp
    - level
    - message
  - [ ] Use `extra` parameter for context
  - [ ] Update key log statements in community router

  **Implementation:**
  ```python
  logger.info(
      "Chat request processed",
      extra={
          "community_id": community_id,
          "session_id": session_id,
          "origin": origin,
          "model": model_name,
          "duration_ms": duration,
      }
  )
  ```

**Issue #X10: Add per-community health check endpoint**
- **Type:** Operations
- **Priority:** P1 (Critical for operational visibility)
- **Effort:** 1 hour
- **Labels:** operations, monitoring, P1
- **Description:**
  Need visibility into each community's health status.

  **Acceptance Criteria:**
  - [ ] `GET /health/communities` endpoint
  - [ ] Returns status for each community:
    - api_key (configured, missing, using platform)
    - cors_origins (count)
    - documents (count)
    - sync_age (hours since last sync)
    - status (healthy, degraded, error)
  - [ ] JSON response format
  - [ ] Tests for endpoint

  **Example Response:**
  ```json
  {
    "hed": {
      "status": "healthy",
      "api_key": "configured",
      "cors_origins": 3,
      "documents": 28,
      "sync_age_hours": 12.5
    },
    "bids": {
      "status": "degraded",
      "api_key": "missing",
      "cors_origins": 1,
      "documents": 15,
      "sync_age_hours": 52.3
    }
  }
  ```

### Testing Plan
- [ ] Test structured logging (logs are JSON, include context)
- [ ] Test health endpoint (returns all communities)
- [ ] Test health endpoint (shows correct status)
- [ ] Test health endpoint (shows missing API keys)

### PR Review Focus
- Logging: All important events are logged with context
- Health checks: Accurate status reporting
- Performance: Health checks don't slow down app

---

## Phase 5: Widget Integration & Testing (P1)

**Goal:** Enable easy widget integration and testing
**Effort:** 2 hours
**Branch:** `feature/issue-XX-widget-integration-test`

### Issue to Create

**Issue #X11: Add widget integration test endpoint**
- **Type:** Testing / Developer Experience
- **Priority:** P1 (Important for reducing integration errors)
- **Effort:** 2 hours
- **Labels:** testing, developer-experience, P1
- **Description:**
  Developers integrating the widget need a way to test their setup before going live.

  **Acceptance Criteria:**
  - [ ] `GET /communities/{id}/widget-test` endpoint
  - [ ] Returns HTML page that:
    - Loads the widget
    - Tests API connectivity
    - Shows CORS status
    - Shows community config
    - Provides diagnostic information
  - [ ] Copy-paste code snippet for integration
  - [ ] Test with different community IDs

  **Example:**
  ```html
  <!DOCTYPE html>
  <html>
  <head>
    <title>OSA Widget Test - HED</title>
  </head>
  <body>
    <h1>Widget Integration Test: HED</h1>

    <h2>Configuration</h2>
    <pre>
    Community ID: hed
    Default Model: anthropic/claude-sonnet-4.5
    CORS Origins: 3 configured
    API Status: ✅ Online
    </pre>

    <h2>Live Widget</h2>
    <!-- Widget loads here -->

    <h2>Integration Code</h2>
    <pre>
    &lt;script src="https://..."&gt;&lt;/script&gt;
    </pre>
  </body>
  </html>
  ```

### Testing Plan
- [ ] Test endpoint with valid community ID
- [ ] Test endpoint with invalid community ID
- [ ] Test that widget loads on test page
- [ ] Test diagnostic information is accurate

### PR Review Focus
- User experience: Test page is helpful and informative
- Diagnostics: Accurate status reporting
- Documentation: Integration code is correct

---

## Phase 6: API Key Enhancement (P1)

**Goal:** Better API key validation and feedback
**Effort:** 1.5 hours
**Branch:** `feature/issue-XX-api-key-enhancement`

### Issue to Create

**Issue #X12: Enhance API key validation and detection**
- **Type:** Operations / Developer Experience
- **Priority:** P1 (Important for cost attribution)
- **Effort:** 1.5 hours
- **Labels:** operations, cost-management, P1
- **Description:**
  Need better detection and validation of API keys to prevent cost attribution errors.

  **Acceptance Criteria:**
  - [ ] Startup validation: Check if env vars are set for communities with `openrouter_api_key_env_var`
  - [ ] Optional: Test API key with simple request (health check style)
  - [ ] Log ERROR (not warning) when community uses platform key unintentionally
  - [ ] Include in structured logs:
    - Which key source used (byok, community, platform)
    - If using platform key when BYOK configured
  - [ ] Update health endpoint to show API key status

  **Implementation:**
  ```python
  # At startup in discover_assistants()
  for config in discovered:
      if config.openrouter_api_key_env_var:
          key = os.getenv(config.openrouter_api_key_env_var)
          if not key:
              logger.error(
                  "CRITICAL: Community %s configured %s but env var not set. "
                  "Using platform key - costs will be billed to platform!",
                  config.id,
                  config.openrouter_api_key_env_var,
              )
  ```

### Testing Plan
- [ ] Test startup with missing env var (should log ERROR)
- [ ] Test startup with set env var (should be quiet)
- [ ] Test that fallback is logged prominently
- [ ] Test health endpoint shows API key status

### PR Review Focus
- Logging: Clear distinction between warning and error
- Operations: Easy to spot cost attribution issues
- Documentation: How to fix missing API key

---

## Implementation Workflow

For each phase:

1. **Create GitHub Issues**
   - Use template with acceptance criteria
   - Link to sprint document for context
   - Add labels (priority, type, effort estimate)

2. **Create Feature Branch**
   ```bash
   git checkout develop
   git pull
   git checkout -b feature/issue-XX-description
   ```

3. **Implement**
   - Follow acceptance criteria
   - Write tests as you go
   - Commit atomically with clear messages

4. **Test Locally**
   ```bash
   uv run pytest tests/ -v
   uv run ruff check .
   uv run ruff format .
   ```

5. **Create Pull Request**
   ```bash
   git push -u origin feature/issue-XX-description
   gh pr create --base develop --title "feat: description" --body "Closes #XX"
   ```

6. **Run PR Review Toolkit**
   ```bash
   /review-pr
   ```

7. **Address Feedback**
   - Fix CRITICAL issues (must fix)
   - Fix IMPORTANT issues (should fix)
   - Consider SUGGESTIONS (nice to have)
   - Add review findings as PR comment
   - Push fixes

8. **Verify CI**
   ```bash
   gh pr checks
   ```

9. **Merge**
   ```bash
   # When CI passes and reviews addressed
   gh pr merge --merge --delete-branch
   ```

10. **Move to Next Phase**

---

## Success Criteria

After all phases complete:

- [ ] Communities can validate configs locally before deploy
- [ ] Step-by-step documentation exists for onboarding
- [ ] Widget supports BYOK for local testing
- [ ] Widget allows model selection with 8 default options
- [ ] All critical security gaps closed
- [ ] Structured logging for debugging
- [ ] Health checks for operational visibility
- [ ] Widget integration test page available
- [ ] API key validation and cost attribution clear

**Result:** Self-service community onboarding with professional experience

---

## Timeline Estimate

| Phase | Effort | Dependencies | Can Parallelize? |
|-------|--------|--------------|------------------|
| 1: Validation & Docs | 7h | None | No (foundation) |
| 2: Widget Settings | 4h | None | Yes (independent) |
| 3: Security | 5h | None | Yes (independent) |
| 4: Operations | 3h | None | Yes (independent) |
| 5: Widget Test | 2h | Phase 2 | No (needs widget BYOK) |
| 6: API Key | 1.5h | None | Yes (independent) |

**Sequential:** ~23 hours (5 days with reviews/testing)
**With 2 parallel tracks:** ~15 hours (3 days with reviews/testing)

**Recommended Approach:**
- **Day 1:** Phase 1 (validation + docs) - Foundation
- **Day 2:** Phases 2 + 3 in parallel (widget + security)
- **Day 3:** Phases 4 + 6 in parallel (ops + API key)
- **Day 4:** Phase 5 (widget test) + Integration testing
- **Day 5:** Buffer for reviews, fixes, final testing

---

## Risk Mitigation

**Risk:** PR reviews take too long
**Mitigation:** Start with Phase 1, get feedback on review depth, adjust

**Risk:** Widget changes break existing functionality
**Mitigation:** Thorough backward compatibility testing, feature flags if needed

**Risk:** Security changes introduce new issues
**Mitigation:** Comprehensive test coverage, security-focused PR reviews

**Risk:** Documentation is unclear to new users
**Mitigation:** Have non-technical reviewer test documentation, iterate

---

## Post-Implementation

After all phases merge to `develop`:

1. **Integration Testing**
   - Test full onboarding flow end-to-end
   - Test widget with all model options
   - Test local development with BYOK
   - Test security validations

2. **Create Release PR**
   ```bash
   gh pr create --base main --head develop --title "Release: Community Onboarding Sprint"
   ```

3. **Final Review**
   - Run `/review-pr` on release PR
   - Address any issues
   - Get sign-off

4. **Merge to Main**
   ```bash
   gh pr merge --merge  # NO --delete-branch (develop is permanent)
   ```

5. **Deploy**
   ```bash
   ./deploy/deploy.sh prod
   ```

6. **Monitor**
   - Check health endpoints
   - Watch logs for errors
   - Monitor first community onboarding

7. **Iterate**
   - Gather feedback from first communities
   - Create follow-up issues for P2 items
   - Plan next sprint

---

## Notes

- All feature branches merge to `develop`, NOT `main`
- Only merge `develop` → `main` for releases
- Use merge commits (NOT squash) to preserve history
- Run PR review toolkit on EVERY PR
- Address critical + important issues before merging
- Keep PRs focused (one phase per PR)
- Link PRs to GitHub issues
- Update this plan as we learn
