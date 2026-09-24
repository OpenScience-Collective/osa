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

// Path segments that are actual routes, never valid community IDs
export const RESERVED_PATHS = ['health', 'version', 'feedback', 'communities', 'metrics', 'sync'];

// Route matchers used directly by fetch() below. Exported so tests exercise
// the exact patterns that route real traffic, rather than a parallel copy
// that could drift from them.
export const ROUTE_PATTERNS = {
  // /:communityId/ask and /:communityId/chat -- the two-segment action route.
  communityAction: /^\/([^\/]+)\/(ask|chat)$/,
  // /:communityId/chat/resume -- three segments, so it can never be matched
  // by communityAction above (which is anchored to exactly two segments).
  communityChatResume: /^\/([^\/]+)\/chat\/resume$/,
  // /:communityId/runtime/:file -- a wheel the browser runtime loads (#431).
  // A coarse filter: anything that is not a bare wheel name never reaches the
  // backend. The backend owns the exact rule (pure-Python -py3-none-any wheels,
  // in src/core/config/runtime_lock.py) and serves only the names its lock
  // overlay lists, so the edge does not repeat it.
  communityRuntimeFile: /^\/([^\/]+)\/runtime\/([A-Za-z0-9_.+-]+\.whl)$/,
};

// This worker is reachable two ways: its default *.workers.dev hostname
// (unprefixed), and the product-owned widget.osc.earth/osa/* path mount
// (#437). A Cloudflare route of the form "widget.osc.earth/osa/*" delivers
// the FULL path, prefix included, to the worker: it sees "/osa/nemar/chat",
// not "/nemar/chat". Strip the prefix once, here, before any route is
// matched, rather than baking "/osa" into each route's own regex; a
// per-route prefix rots the moment someone adds a route and forgets it.
const MOUNT_PREFIX = '/osa';

// The hosts Cloudflare mounts this worker on at MOUNT_PREFIX. Must match the
// `[[routes]]` / `[[env.dev.routes]]` patterns in wrangler.toml.
//
// Stripping has to be gated on the host, not on the path alone. The bare
// *.workers.dev hostname stays live (wrangler.toml sets workers_dev = true),
// and `wrangler dev` serves on localhost, and on BOTH of those the prefix is
// not added by anyone, so a path-only strip would eat a real leading
// segment. Concretely: a community whose ID is "osa" is valid today
// (isValidCommunityId accepts it, RESERVED_PATHS does not list it), and a
// path-only strip turns "/osa/chat" on workers.dev into "/chat", which 404s,
// while "/osa/logo" silently becomes community "logo". The product is named
// OSA, so that ID is a plausible thing for someone to create, and every
// symptom of the collision looks like an unrelated bug.
//
// Adding a mount host without adding it here fails loudly and immediately
// (every route 404s, and the fall-through below logs both paths), which is
// the failure direction to prefer.
const MOUNTED_HOSTS = new Set(['widget.osc.earth', 'develop-widget.osc.earth']);

/**
 * Strip a leading mount-prefix segment so every route matcher can be
 * written once, unprefixed, and work under every hostname.
 *
 * Only strips on a host where Cloudflare actually mounts us at the prefix.
 * On any other host the path is returned unchanged, including the bare
 * *.workers.dev and `wrangler dev` cases.
 */
function stripMountPrefix(pathname, hostname) {
  if (!MOUNTED_HOSTS.has(hostname)) {
    return pathname;
  }
  if (pathname === MOUNT_PREFIX) {
    return '/';
  }
  if (pathname.startsWith(`${MOUNT_PREFIX}/`)) {
    return pathname.slice(MOUNT_PREFIX.length);
  }
  return pathname;
}

// Worker configuration
function getConfig(env) {
  const isDev = env.ENVIRONMENT === 'development';
  return {
    RATE_LIMIT_PER_MINUTE: isDev ? 60 : 10,
    RATE_LIMIT_PER_HOUR: isDev ? 100 : 20,
    // Separate, more generous hourly budget for /chat/resume chains. See
    // checkResumeChainLimit for why a distinct counter exists and why 5x.
    RESUME_LIMIT_PER_HOUR: isDev ? 500 : 100,
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
 *
 * Known limitation:
 * - KV read-then-write is not atomic; concurrent requests from same IP
 *   may slightly exceed hourly limit. Per-minute guard constrains this.
 *
 * @param {object} [options]
 * @param {boolean} [options.countHourly=true] - When false, skip both the
 *   hourly KV gate and its increment. Used by /chat/resume: one conversational
 *   turn with N client-executed tool calls is 1 + N HTTP requests (the
 *   original /chat plus one /chat/resume per execution), so counting the
 *   resume leg against the hourly cap would let two executions burn three of
 *   a user's twenty hourly requests, shared across a NAT'd lab. The
 *   per-minute limiter (bot protection) always runs regardless of this flag.
 */
async function checkRateLimit(request, env, CONFIG, options = {}) {
  const { countHourly = true } = options;
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';

  // Check hourly limit first (KV, read-only, no token consumed)
  // This prevents wasting per-minute tokens on already-rejected requests
  if (countHourly && env.RATE_LIMITER_KV) {
    try {
      const now = Math.floor(Date.now() / 1000);
      const hourKey = `rl:hour:${ip}:${Math.floor(now / 3600)}`;

      // Check current count
      const hourCount = parseInt(await env.RATE_LIMITER_KV.get(hourKey) || '0', 10);
      if (hourCount >= CONFIG.RATE_LIMIT_PER_HOUR) {
        return { allowed: false, reason: 'Too many requests per hour' };
      }
    } catch (error) {
      console.error('Per-hour rate limit check error:', error);
      // Fail open for KV errors
    }
  }

  // Check per-minute limit (built-in API, fast, consumes token)
  // Only check this AFTER hourly passes to avoid wasting tokens.
  // This is the bot-protection path and always runs, regardless of countHourly.
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

  // Increment hourly counter (1 write per request instead of 2)
  // Done last, after both checks pass. Skipped along with the gate above
  // when countHourly is false.
  if (countHourly && env.RATE_LIMITER_KV) {
    try {
      const now = Math.floor(Date.now() / 1000);
      const hourKey = `rl:hour:${ip}:${Math.floor(now / 3600)}`;
      const hourCount = parseInt(await env.RATE_LIMITER_KV.get(hourKey) || '0', 10);
      await env.RATE_LIMITER_KV.put(hourKey, (hourCount + 1).toString(), { expirationTtl: 7200 });
    } catch (error) {
      console.error('Per-hour rate limit increment error:', error);
      // Already allowed, so don't fail the request
    }
  }

  return { allowed: true };
}

/**
 * Check rate limit and return a 429 response if exceeded, or null if allowed.
 *
 * @param {object} [options] - Forwarded to checkRateLimit; see its doc for
 *   countHourly.
 */
async function rateLimitOrReject(request, env, corsHeaders, CONFIG, options = {}) {
  const rl = await checkRateLimit(request, env, CONFIG, options);
  if (!rl.allowed) {
    return new Response(
      JSON.stringify({ error: 'Rate limit exceeded', details: rl.reason }),
      { status: 429, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }
  return null;
}

/**
 * Cap the number of /chat/resume calls a single IP can make per hour.
 *
 * This is deliberately a separate counter from the /chat hourly budget
 * above, not a reuse of it. handleChatResume exempts resume calls from the
 * /chat hourly counter (countHourly: false) because one legitimate
 * conversational turn with N client-executed tool calls costs 1 + N HTTP
 * requests, and counting each resume against the /chat cap would punish a
 * multi-step analysis as if every execution were its own chat turn.
 *
 * But a resume call can itself end in another tool_request, whose resume is
 * also exempt, and so on -- nothing before this function bounds how many
 * times that repeats. Left unchecked, a single hourly-counted /chat call
 * could chain resume calls up to the per-minute limiter's ceiling alone:
 * 10/min * 60 = 600/hour in production, roughly 30x the 20/hour /chat
 * budget. The tool's own stdout is an explicit prompt-injection channel
 * (see src/api/tool_results.py), so a successful injection could drive that
 * chain without the user's cooperation.
 *
 * RESUME_LIMIT_PER_HOUR is 5x the /chat hourly cap in both environments
 * (100/hour in production, 500/hour in dev): generous enough that a
 * legitimate multi-step analysis, several tool executions spread across
 * many of the hour's 20 (or 100 in dev) /chat turns, is not throttled, while
 * still capping the worst-case amplification at 5x instead of the
 * unbounded ~30x the per-minute limiter alone would allow.
 */
async function checkResumeChainLimit(request, env, CONFIG) {
  if (!env.RATE_LIMITER_KV) {
    return { allowed: true };
  }

  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';

  try {
    const now = Math.floor(Date.now() / 1000);
    const hourKey = `rl:resume:hour:${ip}:${Math.floor(now / 3600)}`;

    const count = parseInt(await env.RATE_LIMITER_KV.get(hourKey) || '0', 10);
    if (count >= CONFIG.RESUME_LIMIT_PER_HOUR) {
      return { allowed: false, reason: 'Too many resume requests per hour' };
    }

    await env.RATE_LIMITER_KV.put(hourKey, (count + 1).toString(), { expirationTtl: 7200 });
  } catch (error) {
    console.error('Resume chain limit check error:', error);
    // Fail open for KV errors, consistent with checkRateLimit above.
  }

  return { allowed: true };
}

/**
 * Check the resume-chain limit and return a 429 response if exceeded, or
 * null if allowed. Mirrors rateLimitOrReject's shape for handleChatResume.
 */
async function resumeChainLimitOrReject(request, env, corsHeaders, CONFIG) {
  const rl = await checkResumeChainLimit(request, env, CONFIG);
  if (!rl.allowed) {
    return new Response(
      JSON.stringify({ error: 'Rate limit exceeded', details: rl.reason }),
      { status: 429, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }
  return null;
}

/**
 * Check if origin is allowed
 */
function isAllowedOrigin(origin) {
  if (!origin) return false;

  // Allowed origins for OSA
  const allowedPatterns = [
    'https://osc.earth',
    'https://*.mne.tools',
    'https://bids-specification--2442.org.readthedocs.build',
    'https://bids-specification.readthedocs.io',
    'https://bids-website--847.org.readthedocs.build',
    'https://bids.neuroimaging.io',
    'https://eeglab.org',
    'https://fieldtriptoolbox.org',
    'https://hedtags.org',
    'https://hedtools.org',
    'https://metabci.readthedocs.io',
    'https://mne.tools',
    'https://nemar.org',
    'https://openneuropet.github.io',
    'https://sccn.github.io',
    'https://test.nemar.org',
    'https://www.eeglab.org',
    'https://www.fieldtriptoolbox.org',
    'https://www.hedtags.org',
    'https://www.hedtools.org',
    'https://www.nemar.org'
  ];

  // Check exact matches
  if (allowedPatterns.includes(origin)) return true;

  // Check subdomains
  if (origin.startsWith('https://') && origin.endsWith('.eeglab.org')) return true;
  if (origin.startsWith('https://') && origin.endsWith('.fieldtriptoolbox.org')) return true;
  if (origin.startsWith('https://') && origin.endsWith('.github.io')) return true;
  if (origin.startsWith('https://') && origin.endsWith('.hedtags.org')) return true;
  if (origin.startsWith('https://') && origin.endsWith('.hedtools.org')) return true;
  if (origin.startsWith('https://') && origin.endsWith('.mne.tools')) return true;
  if (origin.startsWith('https://') && origin.endsWith('.nemar.org')) return true;
  if (origin.startsWith('https://') && origin.endsWith('.neuroimaging.io')) return true;
  if (origin.startsWith('https://') && origin.endsWith('.readthedocs.build')) return true;
  if (origin.startsWith('https://') && origin.endsWith('.readthedocs.io')) return true;

  // Allow demo.osc.earth and single-level subdomains (develop-demo, PR previews)
  if (origin === 'https://demo.osc.earth') return true;
  if (origin.startsWith('https://') && origin.endsWith('-demo.osc.earth')) return true;

  // Allow osa-demo.pages.dev and subdomains (backward compatibility)
  if (origin === 'https://osa-demo.pages.dev') return true;
  if (origin.startsWith('https://') && origin.endsWith('.osa-demo.pages.dev')) return true;

  // Allow localhost for development
  if (origin.startsWith('http://localhost:')) return true;
  if (origin.startsWith('http://127.0.0.1:')) return true;

  return false;
}

/**
 * Validate community ID format
 */
export function isValidCommunityId(id) {
  // Allow alphanumeric, hyphen, underscore, 1-50 chars
  return /^[a-zA-Z0-9_-]{1,50}$/.test(id);
}

/**
 * Build CORS headers
 */
function getCorsHeaders(origin) {
  const allowedOrigin = isAllowedOrigin(origin) ? origin : 'https://demo.osc.earth';

  return {
    'Access-Control-Allow-Origin': allowedOrigin,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, X-API-Key, X-Anthropic-API-Key, X-OpenRouter-Key, X-User-Id, cf-turnstile-response',
    'Access-Control-Allow-Credentials': 'true',
  };
}

/**
 * Validate community ID and return error response if invalid, or null if valid.
 */
export function validateCommunityId(communityId, corsHeaders) {
  if (RESERVED_PATHS.includes(communityId)) {
    return new Response('Not Found', { status: 404, headers: corsHeaders });
  }
  if (!isValidCommunityId(communityId)) {
    return new Response(JSON.stringify({ error: 'Invalid community ID format' }), {
      status: 400,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }
  return null;
}

/**
 * Proxy request to backend with the worker's own API key.
 * Used for public endpoints and widget traffic where the worker
 * authenticates on behalf of the client.
 */
async function proxyToBackend(request, env, path, body, corsHeaders, CONFIG) {
  return _proxyToBackend(request, env, path, body, corsHeaders, CONFIG, 'worker');
}

/**
 * Proxy request to backend, forwarding the client's own headers.
 * Used for admin/authenticated endpoints where the client must provide
 * their own API key. The worker does NOT inject its key.
 */
async function proxyToBackendPassthrough(request, env, path, body, corsHeaders, CONFIG) {
  return _proxyToBackend(request, env, path, body, corsHeaders, CONFIG, 'client');
}

/**
 * Internal proxy implementation.
 *
 * @param {string} authMode - 'worker' to inject BACKEND_API_KEY, 'client' to forward client's X-API-Key
 */
async function _proxyToBackend(request, env, path, body, corsHeaders, CONFIG, authMode) {
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

  // Auth mode: inject worker key or forward client key
  if (authMode === 'worker') {
    if (env.BACKEND_API_KEY) {
      backendHeaders['X-API-Key'] = env.BACKEND_API_KEY;
    }
  } else {
    // Forward client's API key for backend to validate
    const clientKey = request.headers.get('X-API-Key');
    if (clientKey) {
      backendHeaders['X-API-Key'] = clientKey;
    }
  }

  // Forward Origin header to backend for origin-based authorization checks
  // Only forward if the origin passed CORS validation
  const origin = request.headers.get('Origin');
  if (origin && isAllowedOrigin(origin)) {
    backendHeaders['Origin'] = origin;
  }

  // Forward BYOK headers
  const byokHeaders = ['X-Anthropic-API-Key', 'X-OpenRouter-Key', 'X-User-Id'];
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
      } catch (parseErr) {
        console.warn('Failed to parse backend error response:', parseErr.message);
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
      // The bare mount path (#500) has one canonical form, with its slash, as
      // the notebook host's "/osa" already does: redirect rather than serve the
      // root at two addresses. Only on a mounted host, where "/osa" is the mount
      // and not a path of its own (see MOUNTED_HOSTS).
      if (MOUNTED_HOSTS.has(url.hostname) && url.pathname === MOUNT_PREFIX) {
        return new Response(null, {
          status: 308,
          headers: { ...corsHeaders, Location: `${MOUNT_PREFIX}/${url.search}` },
        });
      }
      // Strip the widget.osc.earth/osa mount prefix (#437) once, up front.
      // Every matcher below is written unprefixed and sees this pathname,
      // never url.pathname directly, so it works the same reached via the
      // path-mounted host or the bare *.workers.dev / wrangler dev host.
      const pathname = stripMountPrefix(url.pathname, url.hostname);

      // Route requests
      if (pathname === '/') {
        return handleRoot(corsHeaders, CONFIG);
      } else if (pathname === '/health') {
        return await handleHealth(env, corsHeaders, CONFIG);
      } else if (pathname === '/version') {
        return await proxyToBackend(request, env, '/version', null, corsHeaders, CONFIG);
      } else if (pathname === '/feedback' && request.method === 'POST') {
        // Feedback endpoint has lighter protection (rate limit only, no Turnstile)
        return await handleFeedback(request, env, corsHeaders, CONFIG);
      }

      // --- Public read-only endpoints (GET only, rate-limited) ---

      // Communities metadata (widget config)
      if (pathname === '/communities' && request.method === 'GET') {
        const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
        if (rejected) return rejected;
        return await proxyToBackend(request, env, '/communities', null, corsHeaders, CONFIG);
      }

      // Global public metrics: /metrics/public/overview
      if (pathname === '/metrics/public/overview' && request.method === 'GET') {
        const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
        if (rejected) return rejected;
        return await proxyToBackend(request, env, '/metrics/public/overview', null, corsHeaders, CONFIG);
      }

      // Admin metrics endpoints: client must provide their own API key
      if (pathname.match(/^\/metrics\/(overview|tokens|quality)$/) && request.method === 'GET') {
        const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
        if (rejected) return rejected;
        const path = pathname + url.search;
        return await proxyToBackendPassthrough(request, env, path, null, corsHeaders, CONFIG);
      }

      // Sync status endpoints (public, read-only)
      if ((pathname === '/sync/status' || pathname === '/sync/health') && request.method === 'GET') {
        const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
        if (rejected) return rejected;
        return await proxyToBackend(request, env, pathname, null, corsHeaders, CONFIG);
      }

      // Community config endpoint: /:communityId/ (GET)
      const communityConfigMatch = pathname.match(/^\/([^\/]+)\/?$/);
      if (communityConfigMatch && request.method === 'GET') {
        const communityId = communityConfigMatch[1];

        const invalid = validateCommunityId(communityId, corsHeaders);
        if (invalid) return invalid;

        const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
        if (rejected) return rejected;

        return await proxyToBackend(request, env, `/${communityId}/`, null, corsHeaders, CONFIG);
      }

      // Community public metrics endpoints (GET)
      const communityMetricsMatch = pathname.match(/^\/([^\/]+)\/(metrics\/public(?:\/usage)?)$/);
      if (communityMetricsMatch && request.method === 'GET') {
        const communityId = communityMetricsMatch[1];

        const invalid = validateCommunityId(communityId, corsHeaders);
        if (invalid) return invalid;

        const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
        if (rejected) return rejected;

        return await proxyToBackend(request, env, pathname, null, corsHeaders, CONFIG);
      }

      // Community sessions endpoint (GET, authenticated -- forward client key)
      const communitySessionsMatch = pathname.match(/^\/([^\/]+)\/sessions$/);
      if (communitySessionsMatch && request.method === 'GET') {
        const communityId = communitySessionsMatch[1];

        const invalid = validateCommunityId(communityId, corsHeaders);
        if (invalid) return invalid;

        const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
        if (rejected) return rejected;

        return await proxyToBackendPassthrough(request, env, pathname, null, corsHeaders, CONFIG);
      }

      // Community logo endpoint: /:communityId/logo (GET, returns image)
      const communityLogoMatch = pathname.match(/^\/([^\/]+)\/logo$/);
      if (communityLogoMatch && request.method === 'GET') {
        const communityId = communityLogoMatch[1];

        const invalid = validateCommunityId(communityId, corsHeaders);
        if (invalid) return invalid;

        const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
        if (rejected) return rejected;

        // Proxy logo as raw binary (not JSON)
        const backendUrl = env.BACKEND_URL;
        if (!backendUrl) {
          return new Response(JSON.stringify({ error: 'Backend not configured' }), {
            status: 503,
            headers: { ...corsHeaders, 'Content-Type': 'application/json' },
          });
        }

        const backendHeaders = {};
        if (env.BACKEND_API_KEY) {
          backendHeaders['X-API-Key'] = env.BACKEND_API_KEY;
        }

        try {
          const response = await fetch(`${backendUrl}/${communityId}/logo`, {
            method: 'GET',
            headers: backendHeaders,
            signal: AbortSignal.timeout(CONFIG.REQUEST_TIMEOUT),
          });

          if (!response.ok) {
            return new Response('Not Found', { status: 404, headers: corsHeaders });
          }

          const contentType = response.headers.get('Content-Type') || 'application/octet-stream';
          return new Response(response.body, {
            status: 200,
            headers: {
              ...corsHeaders,
              'Content-Type': contentType,
              'Cache-Control': 'public, max-age=86400',
            },
          });
        } catch (error) {
          console.error('Logo proxy error:', error.message);
          return new Response('Not Found', { status: 404, headers: corsHeaders });
        }
      }

      // Community runtime wheel: /:communityId/runtime/:file (GET, immutable bytes)
      const communityRuntimeMatch = pathname.match(ROUTE_PATTERNS.communityRuntimeFile);
      if (communityRuntimeMatch && request.method === 'GET') {
        const [, communityId, fileName] = communityRuntimeMatch;

        const invalid = validateCommunityId(communityId, corsHeaders);
        if (invalid) return invalid;

        return await handleRuntimeFile(request, env, ctx, communityId, fileName, corsHeaders, CONFIG);
      }

      // Community endpoint: /:communityId/chat/resume (three segments).
      // Matched ahead of the two-segment ask/chat route below purely for
      // readability; the two patterns are anchored to different segment
      // counts (communityChatResume always has a literal /chat/resume tail)
      // so neither can shadow the other regardless of order.
      const communityChatResumeMatch = pathname.match(ROUTE_PATTERNS.communityChatResume);
      if (communityChatResumeMatch && request.method === 'POST') {
        const [, communityId] = communityChatResumeMatch;

        const invalid = validateCommunityId(communityId, corsHeaders);
        if (invalid) return invalid;

        return await handleChatResume(request, env, communityId, corsHeaders, CONFIG);
      }

      // Community endpoints: /:communityId/ask and /:communityId/chat
      const communityActionMatch = pathname.match(ROUTE_PATTERNS.communityAction);
      if (communityActionMatch && request.method === 'POST') {
        const [, communityId, action] = communityActionMatch;

        const invalid = validateCommunityId(communityId, corsHeaders);
        if (invalid) return invalid;

        return await handleProtectedEndpoint(request, env, ctx, `/${communityId}/${action}`, corsHeaders, CONFIG);
      }

      // Log both paths, not just one. A 404 here has two very different
      // causes that are indistinguishable from the response: the client asked
      // for a route that does not exist, or MOUNT_PREFIX / MOUNTED_HOSTS and
      // the deployed wrangler.toml route have drifted apart, in which case
      // every request on the mounted host 404s while the deploy output looks
      // clean. Printing raw and stripped side by side is what makes the second
      // one greppable in `wrangler tail` instead of invisible.
      console.warn('[osa-worker] no route matched', {
        rawPath: url.pathname,
        strippedPath: pathname,
        host: url.hostname,
        method: request.method,
        mounted: MOUNTED_HOSTS.has(url.hostname),
      });
      return new Response('Not Found', { status: 404, headers: corsHeaders });
    } catch (error) {
      console.error('Unhandled worker error:', error);
      return new Response(JSON.stringify({ error: 'Internal server error' }), {
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
      'POST /:communityId/chat/resume': 'Resume a conversation after a client-executed tool call',
      'GET /:communityId/runtime/:file': 'A wheel the community\'s browser runtime loads (immutable)',
      'GET /:communityId/metrics/public': 'Public community metrics',
      'GET /:communityId/sessions': 'List sessions (requires API key)',
      'GET /communities': 'List communities with widget configuration',
      'GET /metrics/public/overview': 'Public metrics overview',
      'GET /metrics/overview': 'Admin metrics overview (requires API key)',
      'GET /sync/status': 'Knowledge sync status',
      'POST /feedback': 'Submit feedback',
      'GET /health': 'Health check',
      'GET /version': 'Get API version',
    },
    security: {
      turnstile: 'visible (required for web clients)',
      byok: 'Bring Your Own Key mode for CLI/programmatic access',
      rate_limit: `${CONFIG.RATE_LIMIT_PER_MINUTE}/min, ${CONFIG.RATE_LIMIT_PER_HOUR}/hour`,
      resume_rate_limit: `${CONFIG.RATE_LIMIT_PER_MINUTE}/min (shared per-minute limiter), ${CONFIG.RESUME_LIMIT_PER_HOUR}/hour (separate resume-chain cap; /chat/resume is exempt from the rate_limit hourly figure above)`,
    },
    notes: {
      communities: 'Available communities: hed, bids, eeglab, nemar (check /communities endpoint for full list)',
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
  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON in request body' }), {
      status: 400,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }

  // Check for BYOK mode - CLI/programmatic access with user's own API key
  // (Anthropic or OpenRouter). BYOK users skip Turnstile but still get
  // rate limited.
  const isBYOK = request.headers.get('X-Anthropic-API-Key') !== null ||
    request.headers.get('X-OpenRouter-Key') !== null;

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
        hint: 'Complete the Turnstile challenge or use BYOK mode with an X-Anthropic-API-Key or X-OpenRouter-Key header',
      }), {
        status: 403,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }
  }

  // Check rate limit
  const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
  if (rejected) return rejected;

  // Remove Turnstile token from body before forwarding
  const { cf_turnstile_response, ...cleanBody } = body;

  return await proxyToBackend(request, env, path, cleanBody, corsHeaders, CONFIG);
}

/**
 * Handle feedback endpoint (rate limited but no Turnstile)
 */
async function handleFeedback(request, env, corsHeaders, CONFIG) {
  const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG);
  if (rejected) return rejected;

  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON in request body' }), {
      status: 400,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }
  return await proxyToBackend(request, env, '/feedback', body, corsHeaders, CONFIG);
}

/**
 * Handle the chat resume endpoint: rate-limit-only, no Turnstile, and
 * exempt from the hourly counter.
 *
 * Rate-limit-only, no Turnstile: protected POSTs (handleProtectedEndpoint)
 * verify a single-use Turnstile token that the widget clears after each
 * message it sends. A /chat/resume call follows a client-executed tool
 * call, not a new widget-composed message, so it cannot carry a valid
 * token. Routed the same way as /feedback (handleFeedback above), which the
 * worker already treats as rate-limit-only. This is latent today because
 * Turnstile verification is disabled (no TURNSTILE_SECRET_KEY configured),
 * and would be fatal the day it is switched on: every resume call would be
 * rejected as a failed bot check.
 *
 * Exempt from the hourly counter: production is 10/minute and 20/hour per
 * IP. One conversational turn with N client-executed tool calls is 1 + N
 * HTTP requests (the original /chat plus one /chat/resume per execution),
 * so counting resume calls against the hourly cap would let two executions
 * in one turn burn three of a user's twenty hourly requests -- a budget
 * shared across an entire NAT'd lab. The per-minute limiter (bot
 * protection) still applies via rateLimitOrReject's default behavior.
 *
 * That exemption is only safe because resumeChainLimitOrReject, below,
 * bounds the chain itself against a separate, generous hourly budget --
 * see checkResumeChainLimit for why an unbounded chain would otherwise
 * let this exemption be abused.
 */
/** Wheels are named by version and verified by sha256, so a name never changes bytes. */
const IMMUTABLE = 'public, max-age=31536000, immutable';

/**
 * Serve a wheel a community's browser runtime loads (#431).
 *
 * The edge cache answers repeats, so only a miss spends rate budget, and never
 * the hourly budget /chat counts against: one cold start fetches a wheel or two
 * per reader, and charging those as chats would spend a NAT'd lab's twenty
 * hourly questions on downloads. The cache holds the bytes without CORS
 * headers, because they are the same for every embedder and the headers are
 * not; each response gets its own.
 */
async function handleRuntimeFile(request, env, ctx, communityId, fileName, corsHeaders, CONFIG) {
  const path = `/${communityId}/runtime/${fileName}`;
  // The Cache API exists in Workers and not in Bun, where test-routing.js
  // supplies one; on a *.workers.dev host it exists and stores nothing, which
  // is only a miss.
  const cache = typeof caches !== 'undefined' ? caches.default : null;
  const cacheKey = new Request(new URL(path, request.url).toString(), { method: 'GET' });
  const respond = (body) => new Response(body, {
    status: 200,
    headers: { ...corsHeaders, 'Content-Type': 'application/octet-stream', 'Cache-Control': IMMUTABLE },
  });

  if (cache) {
    const cached = await cache.match(cacheKey);
    if (cached) return respond(cached.body);
  }

  const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG, { countHourly: false });
  if (rejected) return rejected;

  if (!env.BACKEND_URL) {
    return new Response(JSON.stringify({ error: 'Backend not configured' }), {
      status: 503,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }
  const backendHeaders = {};
  if (env.BACKEND_API_KEY) backendHeaders['X-API-Key'] = env.BACKEND_API_KEY;

  let upstream;
  try {
    upstream = await fetch(`${env.BACKEND_URL}${path}`, {
      method: 'GET',
      headers: backendHeaders,
      signal: AbortSignal.timeout(CONFIG.REQUEST_TIMEOUT),
    });
  } catch (error) {
    console.error('Runtime file proxy error:', error.message);
    return new Response('Bad Gateway', { status: 502, headers: corsHeaders });
  }
  // A 404 is the backend's answer (not a listed wheel) and passes through. Any
  // other failure, the backend's own 503 for an overlay that does not verify
  // included, is not cached and must not read as "no such file".
  if (upstream.status === 404) return new Response('Not Found', { status: 404, headers: corsHeaders });
  if (!upstream.ok) {
    console.error(`Runtime file ${path}: backend answered ${upstream.status}`);
    return new Response('Bad Gateway', { status: 502, headers: corsHeaders });
  }

  const bytes = await upstream.arrayBuffer();
  if (cache) {
    const stored = new Response(bytes, {
      headers: { 'Content-Type': 'application/octet-stream', 'Cache-Control': IMMUTABLE },
    });
    // Logged, because a write that fails leaves every later request a miss,
    // each spending rate budget, with nothing else to show for it.
    ctx.waitUntil(
      cache.put(cacheKey, stored).catch((error) => {
        console.error(`Runtime file ${path}: cache write failed:`, error && error.message);
      })
    );
  }
  return respond(bytes);
}

async function handleChatResume(request, env, communityId, corsHeaders, CONFIG) {
  const rejected = await rateLimitOrReject(request, env, corsHeaders, CONFIG, { countHourly: false });
  if (rejected) return rejected;

  // Separate, generous cap on resume chains themselves; see
  // checkResumeChainLimit for why this exists alongside the exemption above.
  const chainRejected = await resumeChainLimitOrReject(request, env, corsHeaders, CONFIG);
  if (chainRejected) return chainRejected;

  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON in request body' }), {
      status: 400,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }

  // Correlation identifiers (session_id, call_id) travel in this JSON body,
  // not as headers: the worker forwards only an allowlisted header set to
  // the backend, and this route does not add to it.
  return await proxyToBackend(request, env, `/${communityId}/chat/resume`, body, corsHeaders, CONFIG);
}
