# OSA Cloudflare Worker

Security proxy for the Open Science Assistant backend. Provides:
- **Turnstile verification** (visible widget) for bot protection
- **Hybrid rate limiting** (IP-based, per-minute and per-hour)
  - Per-minute: Built-in API (fast bot protection, <1ms)
  - Per-hour: KV (global human abuse prevention)
- **CORS validation** for allowed origins
- **API key injection** for backend authentication
- **BYOK mode** for CLI/programmatic access

## Architecture

```
Web Frontend                    CLI / Programmatic
     │                                │
     │ Turnstile token                │ X-Anthropic-API-Key or
     │                                │ X-OpenRouter-Key header
     ▼                                ▼
┌─────────────────────────────────────────────────┐
│              OSA Cloudflare Worker              │
│                                                 │
│  1. Verify Turnstile (web) or detect BYOK      │
│  2. Check rate limits                          │
│  3. Inject backend API key                     │
│  4. Proxy to backend                           │
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│              OSA Backend                         │
│          (api.osc.earth/osa/)                   │
└─────────────────────────────────────────────────┘
```

This worker's public hostnames are `widget.osc.earth/osa` (production) and
`develop-widget.osc.earth/osa` (dev); see `wrangler.toml`'s `[[routes]]` /
`[[env.dev.routes]]` and issue #437. `widget.osc.earth` is a generic host
reserved for widgets in general; `/osa` is this widget's own path under it,
so a future widget can be added at its own path on the same host instead of
consuming a subdomain each. These are ordinary zone routes (path-mounted,
not a whole-hostname Custom Domain) rather than the account-scoped
`*.workers.dev` default, so moving the worker to a different Cloudflare
account is a route change, not a change every consumer (the widget,
`nemarOrg/website`'s Content-Security-Policy) has to make too. The
`your-subdomain.workers.dev`
examples below are generic placeholders for anyone deploying their own fork
of this worker under their own account.

## Setup

### 1. Install wrangler

```bash
npm install -g wrangler
wrangler login
```

### 2. KV namespaces

KV namespaces are already configured in `wrangler.toml` for per-hour rate limiting. No additional setup needed.

### 3. Set up Turnstile

1. Go to Cloudflare Dashboard > Turnstile
2. Create a new widget with **Visible** mode
3. Add allowed hostnames:
   - `hedtags.org`
   - `hed-examples.org`
   - `osc.earth`
   - `localhost` (for development)
4. Copy the Site Key (for frontend integration)
5. Copy the Secret Key (for this worker)

### 4. Set secrets

```bash
# Backend API key (generate with: python -c "import secrets; print(secrets.token_urlsafe(32))")
wrangler secret put BACKEND_API_KEY

# Turnstile secret key (from Cloudflare dashboard)
wrangler secret put TURNSTILE_SECRET_KEY

# For development environment
wrangler secret put BACKEND_API_KEY --env dev
wrangler secret put TURNSTILE_SECRET_KEY --env dev
```

### 5. Deploy

```bash
# Production
wrangler deploy

# Development
wrangler deploy --env dev
```

## Endpoints

| Endpoint | Method | Description | Protection |
|----------|--------|-------------|------------|
| `/` | GET | Worker info | None |
| `/health` | GET | Health check | None |
| `/version` | GET | Backend version | None |
| `/hed/ask` | POST | Single question | Turnstile + Rate limit |
| `/hed/chat` | POST | Multi-turn chat | Turnstile + Rate limit |
| `/hed/chat/resume` | POST | Resume chat after a client-executed tool call | Rate limit only (per-minute, plus its own resume-chain hourly cap; exempt from the `/chat` per-hour counter) |
| `/feedback` | POST | Submit feedback | Rate limit only |

## Rate Limits

Hybrid approach for optimal performance and protection:

| Environment | Per Minute (Bot Protection) | Per Hour (Human Abuse) |
|-------------|----------------------------|----------------------|
| Production | 10 (built-in API) | 20 (KV, global) |
| Development | 60 (built-in API) | 100 (KV, global) |

**Why hybrid?**
- **Per-minute**: Needs to be fast (<1ms), catches bots immediately → Built-in API
- **Per-hour**: Needs global consistency across edge locations → KV
- **Result**: 50% fewer KV writes (1 vs 2 per request), faster bot protection

**Rate limit scope:**
- Limits are **per IP address**, not per session
- 20/hour in production = ~1 question every 3 minutes (reasonable for research)
- Prevents abuse while allowing legitimate use

### `/chat/resume` and the resume-chain budget

`/chat/resume` is a browser posting back the result of a model-written Python
execution so the conversation can continue. It skips Turnstile (the widget's
token is single-use and already spent by the `/chat` call that started the
turn) and is exempt from the `/chat` hourly counter above: one turn with N
tool executions is 1 + N HTTP requests, and charging every resume against
the 20/hour `/chat` budget would let two executions in one turn burn three
of a user's twenty hourly requests, shared across a NAT'd lab.

That exemption only holds for one turn. A resume call can itself end in
another tool request, whose resume is also exempt, and so on, so nothing
else bounds how many times that repeats. Unchecked, a single hourly-counted
`/chat` call could chain resume calls up to the per-minute limiter's ceiling
alone (10/min × 60 = 600/hour in production, ~30x the 20/hour `/chat`
budget), and a successful prompt injection through the tool's own stdout
(an explicit injection channel) could drive that chain without the user's
cooperation.

To bound it, `/chat/resume` keeps its own KV counter, independent of the
`/chat` hourly counter:

| Environment | Resume-Chain Budget (Per Hour) |
|-------------|--------------------------------|
| Production | 100 |
| Development | 500 |

That is 5x the `/chat` hourly cap in both environments: generous enough
that a legitimate multi-step analysis (several tool executions spread
across many of the hour's chat turns) is never throttled as if each
execution were its own chat turn, while still capping the worst-case
amplification at 5x instead of the unbounded ~30x the per-minute limiter
alone would allow. Exceeding it returns 429 with `Too many resume requests
per hour`, distinct from the `/chat` hourly rejection reason.

## BYOK Mode

CLI and programmatic clients can bypass Turnstile by providing their own Anthropic or OpenRouter API key:

```bash
curl -X POST https://osa-worker.your-subdomain.workers.dev/hed/ask \
  -H "Content-Type: application/json" \
  -H "X-Anthropic-API-Key: your-anthropic-key" \
  -d '{"question": "What is HED?"}'
```

```bash
curl -X POST https://osa-worker.your-subdomain.workers.dev/hed/ask \
  -H "Content-Type: application/json" \
  -H "X-OpenRouter-Key: your-openrouter-key" \
  -d '{"question": "What is HED?"}'
```

BYOK users:
- Skip Turnstile verification
- Still subject to rate limits
- Use their own API credits

## Frontend Integration

Add the Turnstile widget to your frontend:

```html
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>

<div id="turnstile-container"></div>

<script>
  turnstile.render('#turnstile-container', {
    sitekey: 'YOUR_SITE_KEY',
    callback: function(token) {
      // Include token in API requests
      fetch('https://osa-worker.your-subdomain.workers.dev/hed/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: 'What is HED?',
          cf_turnstile_response: token
        })
      });
    }
  });
</script>
```

## Development

```bash
# Run locally
wrangler dev

# Tail logs
wrangler tail

# Check deployment
curl https://osa-worker.your-subdomain.workers.dev/health
```

## Troubleshooting

**Turnstile verification failed**
- Check that the hostname is added to the Turnstile widget
- Verify the secret key is set correctly
- Check browser console for Turnstile errors

**Rate limit exceeded**
- Wait for the rate limit window to reset
- Use BYOK mode if you have your own API key

**Backend unreachable**
- Check that BACKEND_URL is correct
- Verify backend is running and healthy
- Check BACKEND_API_KEY is set and matches backend API_KEYS
