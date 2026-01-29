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
 * Hybrid rate limiting approach:
 * - Per-minute (bot protection): Built-in API (fast, <1ms, in-memory)
 * - Per-hour (human abuse): KV (global consistency, 1 write per request)
 *
 * Benefits:
 * - 50% reduction in KV writes (1 vs 2 per request)
 * - Faster bot protection (<1ms vs ~10-50ms for critical first check)
 * - Global hourly limits across all edge locations
 */
async function checkRateLimit(request, env, CONFIG) {
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';

  // Per-minute bot protection (built-in API, fast)
  if (env.RATE_LIMITER_MINUTE) {
    try {
      const { success } = await env.RATE_LIMITER_MINUTE.limit({ key: ip });
      if (!success) {
        return { allowed: false, reason: 'Too many requests per minute' };
      }
    } catch (error) {
      console.error('Per-minute rate limit check error:', error);
      // Fail open for built-in API errors
    }
  }

  // Per-hour human abuse protection (KV, global)
  if (env.RATE_LIMITER_KV) {
    try {
      const now = Math.floor(Date.now() / 1000);
      const hourKey = `rl:hour:${ip}:${Math.floor(now / 3600)}`;

      // Check current count
      const hourCount = parseInt(await env.RATE_LIMITER_KV.get(hourKey) || '0');
      if (hourCount >= CONFIG.RATE_LIMIT_PER_HOUR) {
        return { allowed: false, reason: 'Too many requests per hour' };
      }

      // Increment counter (1 write per request instead of 2)
      await env.RATE_LIMITER_KV.put(hourKey, (hourCount + 1).toString(), { expirationTtl: 7200 });
    } catch (error) {
      console.error('Per-hour rate limit check error:', error);
      // Fail open for KV errors
    }
  }

  return { allowed: true };
}

/**
 * Check if origin is allowed
 */
function isAllowedOrigin(origin) {
  if (!origin) return false;

  // Allowed origins for OSA
  const allowedPatterns = [
    'https://osc.earth',
    'https://eeglab.org',
    'https://hedtags.org',
    'https://sccn.github.io',
    'https://www.eeglab.org',
    'https://www.hedtags.org'
  ];

  // Check exact matches
  if (allowedPatterns.includes(origin)) return true;

  // Check subdomains
  if (origin.endsWith('.eeglab.org')) return true;
  if (origin.endsWith('.github.io')) return true;
  if (origin.endsWith('.hedtags.org')) return true;

  // Allow osa-demo.pages.dev and all subdomains (previews, branches)
  if (origin === 'https://osa-demo.pages.dev') return true;
  if (origin.endsWith('.osa-demo.pages.dev')) return true;

  // Allow localhost for development
  if (origin.startsWith('http://localhost:')) return true;
  if (origin.startsWith('http://127.0.0.1:')) return true;

  return false;
}

/**
 * Validate community ID format
 */
function isValidCommunityId(id) {
  // Allow alphanumeric, hyphen, underscore, 1-50 chars
  return /^[a-zA-Z0-9_-]{1,50}$/.test(id);
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

  // Forward Origin header to backend for origin-based authorization checks
  // Backend uses this to validate request source and apply origin-specific policies
  const origin = request.headers.get('Origin');
  if (origin) {
    backendHeaders['Origin'] = origin;
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

    // For non-2xx responses, pass through backend error details
    if (!response.ok) {
      let backendError = { error: `Backend returned ${response.status}` };
      const contentType = response.headers.get('Content-Type');

      // Try to extract backend error message
      try {
        if (contentType?.includes('application/json')) {
          backendError = await response.json();
        } else {
          const text = await response.text();
          backendError = { error: text.substring(0, 500) };
        }
      } catch {
        // Use default error if parsing fails
      }

      return new Response(JSON.stringify(backendError), {
        status: response.status,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

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
    // Only network/proxy errors reach here, not HTTP errors
    console.error('Backend proxy error:', {
      path: path,
      errorName: error.name,
      errorMessage: error.message,
      stack: error.stack
    });

    let errorMessage = 'Backend request failed';
    let statusCode = 500;

    if (error.name === 'AbortError' || error.message.includes('timeout')) {
      errorMessage = 'Backend request timed out';
      statusCode = 504;
    } else if (error.message.includes('network') || error.message.includes('fetch')) {
      errorMessage = 'Cannot reach backend service';
      statusCode = 503;
    } else if (error instanceof SyntaxError) {
      errorMessage = 'Backend returned invalid response';
      statusCode = 502;
    }

    return new Response(JSON.stringify({
      error: errorMessage,
      details: error.message,
    }), {
      status: statusCode,
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
      } else if (url.pathname === '/feedback' && request.method === 'POST') {
        // Feedback endpoint has lighter protection (rate limit only, no Turnstile)
        return await handleFeedback(request, env, corsHeaders, CONFIG);
      }

      // Community config endpoint: /:communityId/ (GET)
      const communityConfigMatch = url.pathname.match(/^\/([^\/]+)\/?$/);
      if (communityConfigMatch && request.method === 'GET') {
        const communityId = communityConfigMatch[1];

        // Reject reserved path segments as community IDs
        const reservedPaths = ['health', 'version', 'feedback', 'communities'];
        if (reservedPaths.includes(communityId)) {
          return new Response('Not Found', { status: 404, headers: corsHeaders });
        }

        // Validate community ID format
        if (!isValidCommunityId(communityId)) {
          return new Response(JSON.stringify({ error: 'Invalid community ID format' }), {
            status: 400,
            headers: { ...corsHeaders, 'Content-Type': 'application/json' },
          });
        }

        // Rate limit community config lookups
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

        return await proxyToBackend(request, env, `/${communityId}/`, null, corsHeaders, CONFIG);
      }

      // Community endpoints: /:communityId/ask and /:communityId/chat
      const communityActionMatch = url.pathname.match(/^\/([^\/]+)\/(ask|chat)$/);
      if (communityActionMatch && request.method === 'POST') {
        const [, communityId, action] = communityActionMatch;

        // Reject reserved path segments as community IDs
        const reservedPaths = ['health', 'version', 'feedback', 'communities'];
        if (reservedPaths.includes(communityId)) {
          return new Response('Not Found', { status: 404, headers: corsHeaders });
        }

        // Validate community ID format
        if (!isValidCommunityId(communityId)) {
          return new Response(JSON.stringify({ error: 'Invalid community ID format' }), {
            status: 400,
            headers: { ...corsHeaders, 'Content-Type': 'application/json' },
          });
        }

        return await handleProtectedEndpoint(request, env, ctx, `/${communityId}/${action}`, corsHeaders, CONFIG);
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
    version: '2.0.0',
    description: 'Security proxy for Open Science Assistant backend',
    environment: CONFIG.IS_DEV ? 'development' : 'production',
    endpoints: {
      'GET /:communityId/': 'Get community configuration',
      'POST /:communityId/ask': 'Ask a single question to a community',
      'POST /:communityId/chat': 'Multi-turn conversation with a community',
      'POST /feedback': 'Submit feedback',
      'GET /health': 'Health check',
      'GET /version': 'Get API version',
    },
    security: {
      turnstile: 'visible (required for web clients)',
      byok: 'Bring Your Own Key mode for CLI/programmatic access',
      rate_limit: `${CONFIG.RATE_LIMIT_PER_MINUTE}/min, ${CONFIG.RATE_LIMIT_PER_HOUR}/hour`,
    },
    notes: {
      communities: 'Available communities: hed, bids, eeglab (check backend /communities endpoint for full list)',
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
    console.error('Health check backend error:', {
      backendUrl: backendUrl,
      errorName: error.name,
      errorMessage: error.message,
      stack: error.stack
    });

    return new Response(JSON.stringify({
      status: 'degraded',
      proxy: 'operational',
      backend: 'unreachable',
      error: error.message,
      error_type: error.name,
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
