# OSA Deployment Architecture

This document explains the deployment architecture for OSA (Open Science Assistant) with Cloudflare integration for security and scalability.

## Table of Contents

- [Overview](#overview)
- [Production URLs](#production-urls)
- [BYOK (Bring Your Own Key)](#byok-bring-your-own-key)
- [Architecture Options](#architecture-options)
- [Security Layers](#security-layers)
- [Setup Instructions](#setup-instructions)
- [Apache Reverse Proxy](#apache-reverse-proxy)
- [Configuration](#configuration)
- [CLI Usage](#cli-usage)

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

## Production URLs

| Environment | API URL | Docker Image Tag | Port |
|-------------|---------|------------------|------|
| Production | `https://api.osc.earth/osa` | `ghcr.io/openscience-collective/osa:latest` | 38528 |
| Development | `https://api.osc.earth/osa-dev` | `ghcr.io/openscience-collective/osa:dev` | 38529 |

**Frontend:**
- Production: `https://osa-demo.pages.dev`
- Development: `https://develop.osa-demo.pages.dev`

---

## BYOK (Bring Your Own Key)

OSA supports BYOK, allowing users to provide their own LLM API keys instead of relying on server-configured keys.

### How It Works

Users can pass their own API keys via HTTP headers:

| Header | Provider |
|--------|----------|
| `X-OpenAI-API-Key` | OpenAI |
| `X-Anthropic-API-Key` | Anthropic |
| `X-OpenRouter-API-Key` | OpenRouter |

### Authentication Policy

- **With BYOK**: Users providing any BYOK header bypass server API key requirement
- **Without BYOK**: Users must provide server API key via `X-API-Key` header

### Example Request with BYOK

```bash
curl -X POST https://api.osc.earth/osa-dev/hed/chat \
  -H "Content-Type: application/json" \
  -H "X-OpenRouter-API-Key: sk-or-your-key" \
  -d '{"message": "What is HED?", "stream": false}'
```

No `X-API-Key` required when using BYOK headers.

### CLI Configuration for BYOK

```bash
# Set your LLM API key
osa config set --openrouter-key "sk-or-your-key"

# Use with remote server (BYOK)
osa hed ask "What is HED?" --url https://api.osc.earth/osa-dev

# Use standalone mode (local server, no remote needed)
osa hed ask "What is HED?"
```

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

## Apache Reverse Proxy

For servers using Apache as a reverse proxy (alternative to Cloudflare Tunnel):

### Configuration

```apache
# /etc/apache2/sites-available/apache-api.osc.earth.conf

<VirtualHost *:443>
    ServerName api.osc.earth

    # SSL configuration (managed by certbot or similar)
    SSLEngine on
    SSLCertificateFile /path/to/cert.pem
    SSLCertificateKeyFile /path/to/key.pem

    # PRODUCTION: OSA API Backend (port 38528)
    ProxyPass /osa/ http://localhost:38528/
    ProxyPassReverse /osa/ http://localhost:38528/

    # DEVELOPMENT: OSA Dev API Backend (port 38529)
    ProxyPass /osa-dev/ http://localhost:38529/
    ProxyPassReverse /osa-dev/ http://localhost:38529/
</VirtualHost>
```

### Enable Required Modules

```bash
sudo a2enmod proxy proxy_http ssl
sudo systemctl reload apache2
```

---

## CLI Usage

### Installation

```bash
# From PyPI (when published)
pip install open-science-assistant

# From source
git clone https://github.com/OpenScience-Collective/osa.git
cd osa
uv sync
```

### Commands

```bash
# Show available assistants
osa

# Ask a single question (standalone mode - starts local server)
osa hed ask "What is HED?"

# Interactive chat session
osa hed chat

# Use remote server with BYOK
osa hed ask "What is HED?" --url https://api.osc.earth/osa-dev

# Configuration
osa config show                           # Show current config
osa config set --openrouter-key "sk-..."  # Set LLM API key
osa config set --api-key "server-key"     # Set server API key
osa config path                           # Show config file location

# Server management
osa serve                                 # Start API server (production)
osa serve --port 38529 --reload           # Development mode
osa health --url https://api.osc.earth/osa  # Check API health
```

### Standalone vs Remote Mode

| Mode | Description | Use Case |
|------|-------------|----------|
| Standalone (default) | Starts embedded server on localhost | Local development, offline use |
| Remote (`--url`) | Connects to external API | Production, shared infrastructure |

---

**Last Updated**: January 2026
