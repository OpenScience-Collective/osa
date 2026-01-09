/**
 * OSA Cloudflare Worker
 *
 * Security proxy for the Open Science Assistant backend:
 * - Turnstile verification (visible widget) for bot protection
 * - IP-based rate limiting
 * - CORS validation
 * - API key injection for backend auth
 * - BYOK mode for CLI/programmatic access
 */

// Worker configuration
function getConfig(env) {
  const isDev = env.ENVIRONMENT === 'development';
  return {
    RATE_LIMIT_PER_MINUTE: isDev ? 60 : 10,
    RATE_LIMIT_PER_HOUR: isDev ? 600 : 100,
    REQUEST_TIMEOUT: 120000, // 2 minutes for LLM responses
    IS_DEV: isDev,
  };
}

/**
 * Verify Cloudflare Turnstile token
 */
async function verifyTurnstileToken(token, secretKey, ip) {
  // If no secret key configured, skip verification (for development/testing)
  if (!secretKey) {
    console.warn('TURNSTILE_SECRET_KEY not configured, skipping verification');
    return { success: true };
  }

  if (!token) {
    return { success: false, error: 'Missing Turnstile token' };
  }

  try {
    const formData = new URLSearchParams();
    formData.append('secret', secretKey);
    formData.append('response', token);
    if (ip) {
      formData.append('remoteip', ip);
    }

    const response = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: formData,
    });

    const result = await response.json();

    if (result.success) {
      return { success: true };
    } else {
      return {
        success: false,
        error: `Turnstile verification failed: ${result['error-codes']?.join(', ') || 'Unknown error'}`,
      };
    }
  } catch (error) {
    return { success: false, error: `Turnstile verification error: ${error.message}` };
  }
}

/**
 * Check rate limit using KV storage
 */
async function checkRateLimit(request, env, CONFIG) {
  if (!env.RATE_LIMITER) return { allowed: true };

  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const now = Math.floor(Date.now() / 1000);
  const minuteKey = `rl:min:${ip}:${Math.floor(now / 60)}`;
  const hourKey = `rl:hour:${ip}:${Math.floor(now / 3600)}`;

  // Check per-minute limit
  const minuteCount = parseInt(await env.RATE_LIMITER.get(minuteKey) || '0');
  if (minuteCount >= CONFIG.RATE_LIMIT_PER_MINUTE) {
    return { allowed: false, reason: 'Too many requests per minute' };
  }

  // Check per-hour limit
  const hourCount = parseInt(await env.RATE_LIMITER.get(hourKey) || '0');
  if (hourCount >= CONFIG.RATE_LIMIT_PER_HOUR) {
    return { allowed: false, reason: 'Too many requests per hour' };
  }

  // Increment counters
  await Promise.all([
    env.RATE_LIMITER.put(minuteKey, (minuteCount + 1).toString(), { expirationTtl: 120 }),
    env.RATE_LIMITER.put(hourKey, (hourCount + 1).toString(), { expirationTtl: 7200 }),
  ]);

  return { allowed: true };
}

/**
 * Check if origin is allowed
 */
function isAllowedOrigin(origin) {
  if (!origin) return false;

  // Allowed origins for OSA
  const allowedPatterns = [
    'https://hedtags.org',
    'https://hed-examples.org',
    'https://osc.earth',
  ];

  // Check exact matches
  if (allowedPatterns.includes(origin)) return true;

  // Check subdomains
  if (origin.endsWith('.hedtags.org')) return true;
  if (origin.endsWith('.hed-examples.org')) return true;
  if (origin.endsWith('.osc.earth')) return true;

  // Allow specific Cloudflare Pages projects (not all .pages.dev)
  if (origin === 'https://osa-demo.pages.dev') return true;
  if (origin === 'https://develop.osa-demo.pages.dev') return true;
  // Allow preview deployments (format: https://<hash>.osa-demo.pages.dev)
  if (/^https:\/\/[a-f0-9]+\.osa-demo\.pages\.dev$/.test(origin)) return true;

  // Allow localhost for development
  if (origin.startsWith('http://localhost:')) return true;
  if (origin.startsWith('http://127.0.0.1:')) return true;

  return false;
}

/**
 * Build CORS headers
 */
function getCorsHeaders(origin) {
  const allowedOrigin = isAllowedOrigin(origin) ? origin : 'https://osc.earth';

  return {
    'Access-Control-Allow-Origin': allowedOrigin,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, X-API-Key, X-OpenRouter-Key, X-OpenRouter-Model, X-OpenRouter-Provider, X-OpenRouter-Temperature, X-User-Id, cf-turnstile-response',
    'Access-Control-Allow-Credentials': 'true',
  };
}

/**
 * Proxy request to backend
 */
async function proxyToBackend(request, env, path, body, corsHeaders, CONFIG) {
  const backendUrl = env.BACKEND_URL;

  if (!backendUrl) {
    return new Response(JSON.stringify({ error: 'Backend not configured' }), {
      status: 503,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }

  // Prepare headers
  const backendHeaders = {
    'Content-Type': 'application/json',
  };

  // Add backend API key
  if (env.BACKEND_API_KEY) {
    backendHeaders['X-API-Key'] = env.BACKEND_API_KEY;
  }

  // Forward BYOK headers
  const byokHeaders = ['X-OpenRouter-Key', 'X-OpenRouter-Model', 'X-OpenRouter-Provider', 'X-OpenRouter-Temperature', 'X-User-Id'];
  for (const header of byokHeaders) {
    const value = request.headers.get(header);
    if (value) {
      backendHeaders[header] = value;
    }
  }

  try {
    const response = await fetch(`${backendUrl}${path}`, {
      method: request.method,
      headers: backendHeaders,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(CONFIG.REQUEST_TIMEOUT),
    });

    // Check if streaming response
    const contentType = response.headers.get('Content-Type');
    if (contentType?.includes('text/event-stream')) {
      return new Response(response.body, {
        headers: {
          ...corsHeaders,
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        },
      });
    }

    const result = await response.json();
    return new Response(JSON.stringify(result), {
      status: response.status,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  } catch (error) {
    return new Response(JSON.stringify({
      error: 'Backend request failed',
      details: error.message,
    }), {
      status: 500,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }
}

export default {
  async fetch(request, env, ctx) {
    const CONFIG = getConfig(env);
    const origin = request.headers.get('Origin');
    const corsHeaders = getCorsHeaders(origin);

    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      const url = new URL(request.url);

      // Route requests
      if (url.pathname === '/') {
        return handleRoot(corsHeaders, CONFIG);
      } else if (url.pathname === '/health') {
        return await handleHealth(env, corsHeaders, CONFIG);
      } else if (url.pathname === '/version') {
        return await proxyToBackend(request, env, '/version', null, corsHeaders, CONFIG);
      } else if (url.pathname === '/hed/ask' && request.method === 'POST') {
        return await handleProtectedEndpoint(request, env, ctx, '/hed/ask', corsHeaders, CONFIG);
      } else if (url.pathname === '/hed/chat' && request.method === 'POST') {
        return await handleProtectedEndpoint(request, env, ctx, '/hed/chat', corsHeaders, CONFIG);
      } else if (url.pathname === '/feedback' && request.method === 'POST') {
        // Feedback endpoint has lighter protection (rate limit only, no Turnstile)
        return await handleFeedback(request, env, corsHeaders, CONFIG);
      }

      return new Response('Not Found', { status: 404, headers: corsHeaders });
    } catch (error) {
      return new Response(JSON.stringify({ error: error.message }), {
        status: 500,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }
  },
};

/**
 * Root endpoint
 */
function handleRoot(corsHeaders, CONFIG) {
  return new Response(JSON.stringify({
    name: 'OSA API (Cloudflare Workers Proxy)',
    version: '1.0.0',
    description: 'Security proxy for Open Science Assistant backend',
    environment: CONFIG.IS_DEV ? 'development' : 'production',
    endpoints: {
      'POST /hed/ask': 'Ask a single question about HED',
      'POST /hed/chat': 'Multi-turn conversation about HED',
      'POST /feedback': 'Submit feedback',
      'GET /health': 'Health check',
      'GET /version': 'Get API version',
    },
    security: {
      turnstile: 'visible (required for web clients)',
      byok: 'Bring Your Own Key mode for CLI/programmatic access',
      rate_limit: `${CONFIG.RATE_LIMIT_PER_MINUTE}/min, ${CONFIG.RATE_LIMIT_PER_HOUR}/hour`,
    },
  }), {
    headers: { ...corsHeaders, 'Content-Type': 'application/json' },
  });
}

/**
 * Health check endpoint
 */
async function handleHealth(env, corsHeaders, CONFIG) {
  const backendUrl = env.BACKEND_URL;

  if (!backendUrl) {
    return new Response(JSON.stringify({
      status: 'error',
      message: 'BACKEND_URL not configured',
    }), {
      status: 503,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }

  try {
    const response = await fetch(`${backendUrl}/health`, {
      method: 'GET',
      signal: AbortSignal.timeout(5000),
    });

    const backendHealth = await response.json();

    return new Response(JSON.stringify({
      status: 'healthy',
      proxy: 'operational',
      environment: CONFIG.IS_DEV ? 'development' : 'production',
      backend: backendHealth,
    }), {
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  } catch (error) {
    return new Response(JSON.stringify({
      status: 'degraded',
      proxy: 'operational',
      backend: 'unreachable',
      error: error.message,
    }), {
      status: 503,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }
}

/**
 * Handle protected endpoints (Turnstile + rate limiting)
 */
async function handleProtectedEndpoint(request, env, ctx, path, corsHeaders, CONFIG) {
  const body = await request.json();

  // Check for BYOK mode - CLI/programmatic access with user's own API key
  // BYOK users skip Turnstile but still get rate limited
  const isBYOK = request.headers.get('X-OpenRouter-Key') !== null;

  // Verify Turnstile token for non-BYOK requests
  if (!isBYOK) {
    const clientIp = request.headers.get('CF-Connecting-IP');
    const turnstileToken = body.cf_turnstile_response || request.headers.get('cf-turnstile-response');

    const turnstileResult = await verifyTurnstileToken(
      turnstileToken,
      env.TURNSTILE_SECRET_KEY,
      clientIp
    );

    if (!turnstileResult.success) {
      return new Response(JSON.stringify({
        error: 'Bot verification failed',
        details: turnstileResult.error,
        hint: 'Complete the Turnstile challenge or use BYOK mode with X-OpenRouter-Key header',
      }), {
        status: 403,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }
  }

  // Check rate limit
  const rateLimitResult = await checkRateLimit(request, env, CONFIG);
  if (!rateLimitResult.allowed) {
    return new Response(JSON.stringify({
      error: 'Rate limit exceeded',
      details: rateLimitResult.reason,
    }), {
      status: 429,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }

  // Remove Turnstile token from body before forwarding
  const { cf_turnstile_response, ...cleanBody } = body;

  return await proxyToBackend(request, env, path, cleanBody, corsHeaders, CONFIG);
}

/**
 * Handle feedback endpoint (rate limited but no Turnstile)
 */
async function handleFeedback(request, env, corsHeaders, CONFIG) {
  // Check rate limit
  const rateLimitResult = await checkRateLimit(request, env, CONFIG);
  if (!rateLimitResult.allowed) {
    return new Response(JSON.stringify({
      error: 'Rate limit exceeded',
      details: rateLimitResult.reason,
    }), {
      status: 429,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }

  const body = await request.json();
  return await proxyToBackend(request, env, '/feedback', body, corsHeaders, CONFIG);
}
