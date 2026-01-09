# OSA Security Architecture

## Overview

OSA uses a multi-layer security architecture to protect the backend API from abuse and attacks:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENTS                                      │
├─────────────────────┬───────────────────────────────────────────────┤
│   Web Frontends     │              CLI / Programmatic               │
│   (Browser-based)   │                                               │
│                     │                                               │
│  ┌───────────────┐  │  ┌─────────────────────────────────────────┐  │
│  │   Turnstile   │  │  │  X-OpenRouter-Key header (BYOK mode)    │  │
│  │   (Visible)   │  │  │  User provides their own OpenRouter key │  │
│  └───────┬───────┘  │  └────────────────┬────────────────────────┘  │
│          │          │                   │                           │
└──────────┼──────────┴───────────────────┼───────────────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    OSA CLOUDFLARE WORKER                            │
│                    (osa-worker.*.workers.dev)                       │
├─────────────────────────────────────────────────────────────────────┤
│  1. Turnstile Verification (for web clients)                        │
│  2. Rate Limiting (IP-based, per-endpoint)                          │
│  3. CORS Validation (allowed origins only)                          │
│  4. API Key Injection (backend auth)                                │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ X-API-Key header added
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       OSA BACKEND                                    │
│                   (api.osc.earth/osa/)                               │
├─────────────────────────────────────────────────────────────────────┤
│  - API Key Authentication (REQUIRE_API_AUTH=true)                   │
│  - Never exposed directly to internet                               │
│  - Only accepts requests from Worker (with valid API key)           │
└─────────────────────────────────────────────────────────────────────┘
```

## Security Layers

### Layer 1: Cloudflare Turnstile (Bot Protection)

- **Type**: Visible widget (user sees challenge)
- **Purpose**: Prevent automated abuse from bots and scripts
- **Configuration**: Multi-domain via Cloudflare dashboard hostname management
- **BYOK Bypass**: CLI users with `X-OpenRouter-Key` header skip Turnstile

Turnstile widget domains (managed in Cloudflare dashboard):
- `hedtags.org` (HED documentation site)
- `hed-examples.org` (HED examples)
- `localhost` (development)

### Layer 2: Rate Limiting

IP-based rate limiting in the Worker:
- **Per-minute limit**: 10 requests/minute per IP
- **Per-hour limit**: 100 requests/hour per IP
- **Storage**: Cloudflare KV for rate limit counters
- **Scope**: Applied per-endpoint

### Layer 3: CORS Validation

Only allowed origins can make requests:
- `https://hedtags.org`
- `https://hed-examples.org`
- `http://localhost:*` (development only)

### Layer 4: API Key Authentication

- Worker injects `X-API-Key` header before forwarding to backend
- Backend validates API key (REQUIRE_API_AUTH=true)
- Backend never exposed directly; only Worker has the API key

## Request Flow

### Web Frontend (Browser)

1. User interacts with chat widget on hedtags.org
2. Turnstile challenge presented (visible)
3. User completes challenge, token obtained
4. Request sent to Worker with `cf-turnstile-response` header
5. Worker verifies Turnstile token with Cloudflare
6. Worker checks rate limits
7. Worker injects API key, forwards to backend
8. Backend processes request, returns response

### CLI / Programmatic (BYOK Mode)

1. User has their own OpenRouter API key
2. Request sent with `X-OpenRouter-Key` header
3. Worker detects BYOK mode, skips Turnstile
4. Worker still enforces rate limits
5. Worker injects backend API key, forwards request
6. Backend processes with user's OpenRouter key

## Environment Variables

### Worker (wrangler.toml secrets)

```toml
[vars]
BACKEND_URL = "https://api.osc.earth/osa"

# Secrets (set via wrangler secret put)
# BACKEND_API_KEY - API key for authenticating with OSA backend
# TURNSTILE_SECRET_KEY - Cloudflare Turnstile secret key
```

### Backend (.env)

```bash
# API keys for authentication (comma-separated)
API_KEYS=<generated-key>

# Require API key authentication
REQUIRE_API_AUTH=true

# Allow BYOK mode
ALLOW_BYOK=true
```

## Deployment

### Worker

```bash
cd workers/osa-worker
npm install
wrangler secret put BACKEND_API_KEY
wrangler secret put TURNSTILE_SECRET_KEY
wrangler deploy
```

### Backend

Already deployed at `api.osc.earth/osa/` via Docker and Apache reverse proxy.

## Comparison with HEDit

| Feature | HEDit | OSA |
|---------|-------|-----|
| Worker | hedit-worker | osa-worker |
| Turnstile | Invisible | Visible |
| Rate Limiting | Yes | Yes |
| BYOK | Yes | Yes |
| Backend Auth | API Key | API Key |

## Security Considerations

1. **Backend Never Exposed**: The OSA backend at api.osc.earth/osa/ should never be called directly by clients. All requests go through the Worker.

2. **API Key Rotation**: Generate new API keys periodically:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

3. **Rate Limit Tuning**: Adjust limits based on actual usage patterns.

4. **Turnstile Visibility**: Using visible Turnstile for maximum protection. Can switch to invisible if UX becomes an issue.

5. **BYOK Trust**: BYOK users are trusted because they're using their own OpenRouter credits. Still rate-limited to prevent abuse.
