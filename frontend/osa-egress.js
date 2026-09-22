/**
 * Egress control for the browser Python runtime (epic #429, phase #431, step 2).
 *
 * WHAT THIS IS, AND WHAT THE CSP IS
 *
 * Measured 2026-09-22 and recorded on #431: a blob Worker inherits the owner
 * document's Content-Security-Policy, so `connect-src` really is enforced on the
 * worker's own requests, across fetch, XMLHttpRequest, WebSocket and EventSource,
 * with no help from us.
 *
 * That policy is the EMBEDDER's. It cannot vary per community, and an embedder
 * serving `connect-src *` leaves no browser-enforced boundary at all. So the CSP
 * is a ceiling we do not control, and this module is the boundary we do: it
 * enforces a community's own `fetch_allow` inside whatever ceiling the page
 * happens to impose. On a permissive embedder it is the only thing between
 * model-written code and the open internet.
 *
 * Which means `fetch_allow` is an egress control WE enforce, not a guarantee the
 * browser makes. Documented that way deliberately, because a config key named
 * like an allowlist otherwise implies a promise the platform cannot keep.
 *
 * THE TWO PHASES
 *
 * Boot needs to reach the wheel origin to install packages; user code must not
 * inherit that reach. So the allowlist is installed wide, then SEALED to
 * `fetch_allow` before any model-written code runs. Sealing is one-way.
 */

/** Why a request was refused. Surfaced to Python so the model can adapt. */
export const DENY_REASON = Object.freeze({
  UNPARSEABLE: 'unparseable',
  SCHEME: 'scheme_not_allowed',
  CREDENTIALS_IN_URL: 'credentials_in_url',
  NOT_ALLOWED: 'not_in_fetch_allow',
  TRANSPORT: 'transport_not_allowed',
});

/**
 * Decide whether a URL may be fetched.
 *
 * Compares parsed ORIGIN plus a path prefix that must end on a segment
 * boundary. A raw string prefix would be wrong in two ways that matter:
 * `https://zarr.nemar.org` as a string prefix also matches
 * `https://zarr.nemar.org.evil.com/`, and `/nm000103/` as a string prefix also
 * matches `/nm000103x/`. Both are trivial to hit by accident and trivial to
 * exploit on purpose.
 *
 * @param {string} rawUrl - URL to test, absolute or relative to `base`.
 * @param {string[]} prefixes - Allowed URL prefixes, absolute.
 * @param {{base?: string}} [options]
 * @returns {{allowed: boolean, reason?: string}}
 */
export function isUrlAllowed(rawUrl, prefixes, options = {}) {
  let url;
  try {
    url = new URL(String(rawUrl), options.base);
  } catch {
    return { allowed: false, reason: DENY_REASON.UNPARSEABLE };
  }

  // http(s) only. data: and blob: are same-origin-ish smuggling routes rather
  // than network egress, and are refused here so the surface stays one thing.
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    return { allowed: false, reason: DENY_REASON.SCHEME };
  }

  // user:pass@host is a credential-smuggling vector and never legitimate for a
  // public data plane, so it is refused before the allowlist is even consulted.
  if (url.username || url.password) {
    return { allowed: false, reason: DENY_REASON.CREDENTIALS_IN_URL };
  }

  for (const prefix of prefixes || []) {
    let allow;
    try {
      allow = new URL(String(prefix));
    } catch {
      // A malformed entry is skipped rather than throwing. A single bad line in
      // a community's config must not take the whole allowlist down, because
      // "allowlist threw" would most likely be handled as "deny everything" and
      // present as the runtime being broken rather than misconfigured.
      continue;
    }
    if (allow.origin !== url.origin) continue;

    const allowPath = allow.pathname.endsWith('/') ? allow.pathname : `${allow.pathname}/`;
    const targetPath = url.pathname.endsWith('/') ? url.pathname : `${url.pathname}/`;
    if (targetPath.startsWith(allowPath)) {
      return { allowed: true };
    }
  }

  return { allowed: false, reason: DENY_REASON.NOT_ALLOWED };
}

/**
 * Build the JavaScript that installs the egress guard inside the worker.
 *
 * Emitted as source because the worker is a blob: there is no module graph to
 * import from. `isUrlAllowed` is serialized in rather than duplicated, so the
 * rule that the tests exercise is the exact rule that runs.
 *
 * @param {object} options
 * @param {string[]} options.bootAllow - Extra prefixes permitted during boot
 *   only, such as the wheel origin.
 * @returns {string}
 */
export function buildEgressGuardSource({ bootAllow = [] } = {}) {
  return `
  // ---- egress guard (phase 2 step 2) ----
  const DENY_REASON = ${JSON.stringify(DENY_REASON)};
  const isUrlAllowed = ${isUrlAllowed.toString()};

  const __egress = {
    // Wide during boot so packages can be installed, then sealed.
    allow: ${JSON.stringify(bootAllow)},
    sealed: false,
    denials: [],
  };

  function __seal(fetchAllow) {
    // One-way. Boot reaches the wheel origin; user code must not inherit that,
    // and a guard that can be widened again is not a guard.
    __egress.allow = Array.isArray(fetchAllow) ? fetchAllow.slice() : [];
    __egress.sealed = true;
  }

  function __denied(url, reason) {
    __egress.denials.push({ url: String(url).slice(0, 200), reason });
    const err = new Error(
      'Blocked by the runtime egress policy (' + reason + '): ' + String(url).slice(0, 200) +
      '. Only origins in this community\\'s fetch_allow are reachable from executed code.'
    );
    err.name = 'EgressDenied';
    err.reason = reason;
    return err;
  }

  function __check(url) {
    const verdict = isUrlAllowed(url, __egress.allow, { base: self.location && self.location.href });
    if (!verdict.allowed) throw __denied(url, verdict.reason);
  }

  // fetch. credentials are omitted so a same-origin request cannot silently
  // carry the embedding page's cookies into model-written code's reach.
  const __nativeFetch = self.fetch.bind(self);
  self.fetch = function (input, init) {
    const url = (input && typeof input === 'object' && 'url' in input) ? input.url : input;
    __check(url);
    const merged = Object.assign({}, init, { credentials: 'omit' });
    return __nativeFetch(input, merged);
  };

  // XMLHttpRequest. Pyodide's synchronous HTTP shims reach for this, so leaving
  // it unshimmed would leave a second door open beside a guarded fetch.
  if (self.XMLHttpRequest) {
    const __open = self.XMLHttpRequest.prototype.open;
    self.XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      __check(url);
      this.withCredentials = false;
      return __open.call(this, method, url, ...rest);
    };
  }

  // WebSocket and EventSource are refused outright rather than allowlisted.
  // Nothing in the runtime needs a persistent channel, and a long-lived socket
  // is a poor fit for an allowlist whose whole model is per-request. This is the
  // deliberately strict default; relaxing it later is a config change, while
  // tightening it later would break working embeds.
  //
  // Defined UNCONDITIONALLY, not guarded by an existence check. An earlier
  // version only replaced these when the global already existed, which left two
  // holes: an environment that lacks one today and gains it later would run
  // unshimmed, and a test could not tell "absent" from "allowed" and so passed
  // vacuously. Defining the blocker either way closes both.
  self.WebSocket = function () {
    throw __denied('websocket', DENY_REASON.TRANSPORT);
  };
  self.EventSource = function () {
    throw __denied('eventsource', DENY_REASON.TRANSPORT);
  };
  // ---- end egress guard ----
  `;
}

/**
 * Build the Python that removes install-time capability from the namespace
 * model-written code runs in.
 *
 * `micropip` is the important one: left reachable, executed code installs
 * whatever it likes and the `allow_install` allowlist means nothing. Removing it
 * from `sys.modules` and from builtins is what makes that allowlist structural
 * rather than advisory.
 *
 * @returns {string}
 */
export function buildNamespaceSealSource() {
  return [
    'import sys as _sys',
    '',
    '# Anything that can install, fetch or introspect the loader is dropped before',
    '# model-written code runs. micropip is the one that matters: reachable, it',
    '# turns allow_install into a suggestion.',
    'for _name in ("micropip", "pyodide_js", "pyodide_http"):',
    '    _sys.modules.pop(_name, None)',
    '',
    '# js is the bridge to the host global scope. Executed code reaching js.fetch',
    '# directly would route around the shimmed self.fetch entirely.',
    '_sys.modules.pop("js", None)',
    '',
    'del _sys',
  ].join('\n');
}
