# OSA Cloudflare Worker

Security proxy for the Open Science Assistant backend. Provides:
- **Turnstile verification** (visible widget) for bot protection
- **Rate limiting** (IP-based, per-minute and per-hour)
- **CORS validation** for allowed origins
- **API key injection** for backend authentication
- **BYOK mode** for CLI/programmatic access

## Architecture

```
Web Frontend                    CLI / Programmatic
     │                                │
     │ Turnstile token                │ X-OpenRouter-Key header
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

## Setup

### 1. Install wrangler

```bash
npm install -g wrangler
wrangler login
```

### 2. Create KV namespaces for rate limiting

```bash
# Production
wrangler kv:namespace create "RATE_LIMITER"
# Copy the ID and update wrangler.toml

# Development
wrangler kv:namespace create "RATE_LIMITER" --env dev
# Copy the ID and update wrangler.toml [env.dev.kv_namespaces]
```

### 3. Update wrangler.toml

Replace `REPLACE_WITH_KV_ID` and `REPLACE_WITH_DEV_KV_ID` with the IDs from step 2.

### 4. Set up Turnstile

1. Go to Cloudflare Dashboard > Turnstile
2. Create a new widget with **Visible** mode
3. Add allowed hostnames:
   - `hedtags.org`
   - `hed-examples.org`
   - `osc.earth`
   - `localhost` (for development)
4. Copy the Site Key (for frontend integration)
5. Copy the Secret Key (for this worker)

### 5. Set secrets

```bash
# Backend API key (generate with: python -c "import secrets; print(secrets.token_urlsafe(32))")
wrangler secret put BACKEND_API_KEY

# Turnstile secret key (from Cloudflare dashboard)
wrangler secret put TURNSTILE_SECRET_KEY

# For development environment
wrangler secret put BACKEND_API_KEY --env dev
wrangler secret put TURNSTILE_SECRET_KEY --env dev
```

### 6. Deploy

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
| `/feedback` | POST | Submit feedback | Rate limit only |

## Rate Limits

| Environment | Per Minute | Per Hour |
|-------------|------------|----------|
| Production | 10 | 100 |
| Development | 60 | 600 |

## BYOK Mode

CLI and programmatic clients can bypass Turnstile by providing their own OpenRouter API key:

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
