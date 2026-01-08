# OSA Deployment Architecture

This document explains the deployment architecture for OSA (Open Science Assistant) with Cloudflare integration for security and scalability.

## Table of Contents

- [Overview](#overview)
- [Architecture Options](#architecture-options)
- [Security Layers](#security-layers)
- [Setup Instructions](#setup-instructions)
- [Configuration](#configuration)

---

## Overview

**Backend**: FastAPI + Docker on port 38528
**Frontend**: Cloudflare Pages (planned)
**API Proxy**: Cloudflare Worker with Turnstile protection

**Port Allocation:**
- HEDit prod: 38427
- HEDit dev: 38428
- OSA prod: 38528
- OSA dev: 38529

---

## Architecture Options

### Option 1: Direct Connection (Development)

```
┌─────────────────────────────┐
│  Frontend                   │  ← Static Site / Local Dev
│  (localhost:3000)           │
└──────────────┬──────────────┘
               │ HTTP
               ▼
┌─────────────────────────────┐
│  OSA Backend                │  ← FastAPI
│  localhost:38528            │
│  (CORS validation)          │
└─────────────────────────────┘
```

### Option 2: Cloudflare Worker Proxy (Production)

```
┌─────────────────────────────┐
│  Frontend                   │  ← Cloudflare Pages
│  (osa.pages.dev)            │
│  + Turnstile Challenge      │
└──────────────┬──────────────┘
               │ HTTPS + Turnstile Token
               ▼
┌─────────────────────────────┐
│  Cloudflare Worker          │  ← API Proxy
│  (api.osa.pages.dev)        │
│  - Validates Turnstile      │
│  - Rate limiting            │
│  - Adds API token           │
└──────────────┬──────────────┘
               │ HTTPS + API Token
               ▼
┌─────────────────────────────┐
│  Cloudflare Tunnel          │  ← Secure Tunnel
│  (cloudflared)              │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│  OSA Backend                │  ← Docker Container
│  127.0.0.1:38528            │
│  (Validates API Token)      │
└─────────────────────────────┘
```

---

## Security Layers

### Layer 1: Turnstile (Frontend → Worker)

**Purpose**: Bot protection at the edge
**Location**: Cloudflare Worker validates Turnstile token

Turnstile is Cloudflare's CAPTCHA alternative:
- Invisible challenge (no user interaction needed)
- Blocks automated attacks
- Free for unlimited verifications

**Frontend Integration:**
```html
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<div class="cf-turnstile" data-sitekey="YOUR_SITE_KEY"></div>
```

**Worker Validation:**
```javascript
async function validateTurnstile(token, remoteIP, env) {
  const response = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      secret: env.TURNSTILE_SECRET_KEY,
      response: token,
      remoteip: remoteIP,
    }),
  });
  const result = await response.json();
  return result.success;
}
```

### Layer 2: API Token (Worker → Backend)

**Purpose**: Authenticate Worker requests to backend
**Location**: Worker adds token, backend validates

**Worker adds token:**
```javascript
const backendRequest = new Request(backendUrl, {
  method: request.method,
  headers: {
    ...Object.fromEntries(request.headers),
    'X-API-Token': env.BACKEND_API_TOKEN,
  },
  body: request.body,
});
```

**Backend validates:**
```python
# In FastAPI middleware
def validate_api_token(request: Request):
    token = request.headers.get("X-API-Token")
    expected = settings.api_key
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="Invalid API token")
```

### Layer 3: CORS (Backend)

**Purpose**: Ensure requests only from allowed origins
**Location**: FastAPI CORS middleware

```python
# In src/api/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://osa.pages.dev", "https://api.osa.pages.dev"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Setup Instructions

### Docker Deployment

```bash
# Pull from GHCR
docker pull ghcr.io/openscience-collective/osa:latest

# Run container
docker run -d \
  --name osa \
  -p 38528:38528 \
  -e API_KEY=your-api-token \
  -e OPENROUTER_API_KEY=your-openrouter-key \
  ghcr.io/openscience-collective/osa:latest

# Verify health
curl http://localhost:38528/health
```

### Cloudflare Tunnel Setup

```bash
# Install cloudflared
# macOS: brew install cloudflare/cloudflare/cloudflared
# Linux: wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64

# Login to Cloudflare
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create osa-backend

# Configure tunnel (config.yml)
cat > ~/.cloudflared/config.yml << EOF
tunnel: YOUR_TUNNEL_ID
credentials-file: /path/to/credentials.json

ingress:
  - hostname: api.osa.pages.dev
    service: http://localhost:38528
  - service: http_status:404
EOF

# Run tunnel
cloudflared tunnel run osa-backend
```

### Cloudflare Worker Deployment

```bash
cd workers

# Install wrangler
npm install -g wrangler
wrangler login

# Set secrets
wrangler secret put TURNSTILE_SECRET_KEY
wrangler secret put BACKEND_API_TOKEN

# Deploy
wrangler deploy
```

---

## Configuration

### Environment Variables

**Backend (.env):**
```bash
# Server
PORT=38528
HOST=0.0.0.0

# Security
API_KEY=your-backend-api-token

# LLM Provider
OPENROUTER_API_KEY=your-openrouter-key
```

**Worker (wrangler.toml secrets):**
```bash
TURNSTILE_SECRET_KEY=your-turnstile-secret
BACKEND_API_TOKEN=your-backend-api-token
```

### Port Configuration

The port is configurable via environment variable:
```bash
# Default: 38528
PORT=38528 docker run ...
```

Or via CLI:
```bash
osa serve --port 38528
```

---

## Security Comparison

| Layer | Protects Against | Location |
|-------|-----------------|----------|
| Turnstile | Bots, automated abuse | Edge (Worker) |
| API Token | Unauthorized backend access | Worker → Backend |
| CORS | Cross-origin attacks | Backend |
| Rate Limiting | DoS, abuse | Worker (KV) |
| HTTPS | Man-in-the-middle | Cloudflare |

---

## Monitoring

### Health Check
```bash
curl https://api.osa.pages.dev/health
```

### Worker Logs
```bash
wrangler tail
```

### Backend Logs
```bash
docker logs -f osa
```

---

## Cost Estimation

### Cloudflare (Free Tier)
- Workers: 100,000 requests/day
- Pages: Unlimited static sites
- Turnstile: Unlimited verifications
- Tunnel: Free

### OpenRouter API
- Varies by model (see .context/research.md)
- Cerebras models: ~$0.0001/request

**Estimated monthly cost for 10,000 requests: ~$1-5**

---

**Last Updated**: January 2026
