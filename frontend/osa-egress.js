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
    // WHATWG URL parsing collapses ".." at parse time, so an entry like
    // "https://host/data/../" parses to pathname "/" and silently grants the
    // WHOLE ORIGIN instead of the scoped path someone meant to write. This is
    // config input rather than attacker input, but a typo that quietly widens an
    // allowlist is worse than one that errors, so entries containing dot
    // segments are refused outright.
    if (/(^|\/)\.\.?(\/|$)/.test(String(prefix))) {
      continue;
    }
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
  // IMPORTANT: this is a FRAGMENT, not a standalone script. The caller must
  // embed it inside a function scope (buildWorkerSource wraps the whole worker
  // in an IIFE).
  //
  // An earlier version emitted these as top-level statements. In a classic
  // Worker, top-level `function` declarations become properties of the global
  // object, which made `self.__seal` a directly callable, unauthenticated
  // global: one line of executed code re-widened the allowlist to any origin.
  // That was demonstrated with a working proof. Nothing here may be declared
  // at top level, and nothing may be assigned to `self` except the shims.
  return `
  const DENY_REASON = ${JSON.stringify(DENY_REASON)};
  const isUrlAllowed = ${isUrlAllowed.toString()};

  const __egress = { allow: ${JSON.stringify(bootAllow)}, sealed: false };

  // One-shot. The previous version tracked a sealed flag and never read it,
  // so sealing could be repeated and widened at will.
  const __seal = (fetchAllow) => {
    if (__egress.sealed) {
      throw new Error('egress allowlist is already sealed and cannot be changed');
    }
    __egress.allow = Array.isArray(fetchAllow) ? fetchAllow.slice() : [];
    __egress.sealed = true;
  };

  const __denied = (url, reason) => {
    const err = new Error(
      'Blocked by the runtime egress policy (' + reason + '): ' + String(url).slice(0, 200) +
      '. Only origins in this community\\'s fetch_allow are reachable from executed code.'
    );
    err.name = 'EgressDenied';
    err.reason = reason;
    return err;
  };

  const __check = (url) => {
    const verdict = isUrlAllowed(url, __egress.allow);
    if (!verdict.allowed) throw __denied(url, verdict.reason);
    return true;
  };

  // Replace a property so the original is unreachable and the replacement
  // cannot be swapped back out. WebIDL members usually live on the PROTOTYPE, so
  // defining an own property alone would leave the pristine native one reachable
  // as Object.getPrototypeOf(self).fetch.
  //
  // FAILS CLOSED. An earlier version swallowed both failures: a prototype member
  // that would not delete stayed reachable, and a defineProperty that threw fell
  // back to plain assignment, leaving a shim executed code could reassign. Both
  // were silent, so the seal could be partial with nothing anywhere saying so.
  // Throwing here happens at the worker's top level, before it answers anything,
  // so the host sees a worker error and the boot fails visibly instead.
  const __lock = (target, name, descriptor) => {
    // The WHOLE chain, Object.prototype included. Stopping short of it skipped
    // the one prototype a Bun worker global has, where a transport defined there
    // stayed reachable from any plain object. Only these transport names are
    // ever deleted, and none of them belongs on Object.prototype.
    let proto = Object.getPrototypeOf(target);
    while (proto) {
      if (Object.getOwnPropertyDescriptor(proto, name)) {
        try { delete proto[name]; } catch (e) { /* checked on the next line */ }
        if (Object.getOwnPropertyDescriptor(proto, name)) {
          throw new Error('egress guard: the native ' + name + ' could not be removed from the prototype chain, so it would stay reachable');
        }
      }
      proto = Object.getPrototypeOf(proto);
    }
    Object.defineProperty(target, name, Object.assign({ configurable: false, enumerable: true }, descriptor));
    const locked = Object.getOwnPropertyDescriptor(target, name);
    const valueHeld = !('value' in descriptor) || (locked && locked.value === descriptor.value && !locked.writable);
    if (!locked || locked.configurable || !valueHeld) {
      throw new Error('egress guard: ' + name + ' could not be locked');
    }
  };
  const __install = (name, value) => __lock(self, name, { value: value, writable: false });

  const __nativeFetch = self.fetch.bind(self);

  __install('fetch', function (input, init) {
    // Normalize to a Request ONCE, then check and dispatch the SAME object.
    // Reading \`input.url\` for the check while passing \`input\` to fetch reads one
    // object through two different algorithms, and they can be made to
    // disagree: { url: allowedUrl, toString() { return evilUrl } } passed the
    // check and fetched the evil URL. Proven with running code.
    let request;
    try {
      request = new Request(input, init);
    } catch (e) {
      throw __denied(typeof input === 'string' ? input : '[unparseable request]', DENY_REASON.UNPARSEABLE);
    }
    __check(request.url);
    // credentials omitted so a same-origin request cannot carry the embedding
    // page's cookies. redirect 'error' because a 302 from an ALLOWED origin to a
    // disallowed one was followed and its body delivered; also proven. Manual
    // redirect handling cannot re-check, since an opaque-redirect response
    // exposes no Location header cross-origin, so failing closed is the only
    // correct option. A data plane that legitimately redirects will surface as
    // an error rather than as a silent hole.
    return __nativeFetch(new Request(request, { credentials: 'omit', redirect: 'error' }));
  });

  if (self.XMLHttpRequest) {
    const __open = self.XMLHttpRequest.prototype.open;
    self.XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      __check(url);
      this.withCredentials = false;
      return __open.call(this, method, url, ...rest);
    };
  }

  // importScripts performs a real cross-origin GET and is completely outside
  // fetch and XHR. It was previously unguarded, and the worker's own boot
  // depends on it, so it is guarded rather than removed.
  if (typeof self.importScripts === 'function') {
    const __nativeImport = self.importScripts.bind(self);
    __install('importScripts', function (...urls) {
      for (const u of urls) __check(u);
      return __nativeImport(...urls);
    });
  }

  // CacheStorage performs real network requests through Cache.add/addAll, which
  // never touch the fetch shim. Nothing in the runtime needs it. Removed from the
  // prototype as well: an own getter alone left the native one reachable through
  // Object.getPrototypeOf(self).
  if ('caches' in self) {
    __lock(self, 'caches', { get: function () { throw __denied('caches', DENY_REASON.TRANSPORT); } });
  }

  // Defined UNCONDITIONALLY, not guarded by an existence check. An earlier
  // version only replaced these when the global already existed, which left two
  // holes: an environment that lacks one today and gains it later would run
  // unshimmed, and a test could not tell "absent" from "allowed" and so passed
  // vacuously.
  __install('WebSocket', function () { throw __denied('websocket', DENY_REASON.TRANSPORT); });
  __install('EventSource', function () { throw __denied('eventsource', DENY_REASON.TRANSPORT); });

  // A NESTED WORKER IS A FRESH, UNSHIMMED GLOBAL. Everything above is installed
  // on this worker's own global object, and a worker spawned from here gets its
  // own, with the native fetch intact. It would still be under the page's CSP,
  // but connect-src is the embedder's ceiling, not this community's
  // fetch_allow, so a permissive embedder would leave no boundary at all.
  // Nothing in the runtime spawns one.
  __install('Worker', function () { throw __denied('worker', DENY_REASON.TRANSPORT); });
  __install('SharedWorker', function () { throw __denied('sharedworker', DENY_REASON.TRANSPORT); });

  // sendBeacon is a fire-and-forget POST that returns a boolean and reports
  // nothing, which makes it the transport a leak would prefer. It is not on
  // WorkerNavigator in current browsers, so this is a guard against gaining it
  // rather than against having it.
  if (self.navigator && typeof self.navigator.sendBeacon === 'function') {
    __lock(self.navigator, 'sendBeacon', {
      value: function () { throw __denied('sendbeacon', DENY_REASON.TRANSPORT); },
      writable: false,
    });
  }
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
  // Verified against real Pyodide 0.28.3 in Chrome on 2026-09-22, under
  // nemar.org's production Content-Security-Policy, and recorded on #431: every
  // blocked root is refused through the import statement, through
  // importlib.import_module and as a submodule, while unblocked imports still
  // work. It is also compiled by a real Python in the test suite, because a
  // malformed f-string in an earlier version passed every regex assertion.
  //
  // Reading this source for the right words is NOT verification; that is how
  // the earlier version was checked, and it could not tell a working seal from
  // a comment mentioning one.
  return [
    'import builtins as _b, sys as _sys',
    '',
    '# Names executed code must not be able to reach.',
    '#   micropip      installs packages, which makes allow_install advisory',
    '#   js            the bridge to the host global scope; js.fetch would route',
    '#                 around the shimmed self.fetch entirely',
    '#   pyodide       pyodide.code.run_js executes arbitrary JavaScript, which is',
    '#                 a direct path back to every global the guard just closed.',
    '#                 The previous version missed this entirely: it dropped',
    '#                 pyodide_js, a different module, and left pyodide.code open.',
    '_BLOCKED_ROOTS = frozenset({',
    '    "micropip", "js", "pyodide", "pyodide_js", "pyodide_http", "ctypes",',
    '})',
    '',
    '# One reason per root. A single shared sentence said "network access goes',
    '# through the runtime\'s own client" for ctypes too, which is not why ctypes',
    '# is blocked and reads as a bug in the person\'s own code.',
    '_NETWORK = (',
    '    "Read data with osa.fetch_bytes(url) or osa.fetch_text(url), which "',
    '    "enforces this community\'s fetch_allow."',
    ')',
    '_WHY = {',
    '    "micropip": ("Packages are installed when the runtime starts, from this "',
    '                 "community\'s allow_install list, and nothing is installed "',
    '                 "on demand."),',
    '    "js": _NETWORK,',
    '    "pyodide": _NETWORK,',
    '    "pyodide_js": _NETWORK,',
    '    "pyodide_http": _NETWORK,',
    '    "ctypes": ("Foreign function calls are not available in this runtime."),',
    '}',
    '',
    '',
    'class _ImportBlocker:',
    '    """Refuse blocked modules at the FINDER level, not the cache level.',
    '',
    '    Popping sys.modules only evicts a cache entry. Pyodide builds js and',
    '    pyodide_js through an import hook over the live JS globals, so a later',
    '    "import js" simply rebuilds an equally capable proxy. Worse, importlib',
    '    .import_module does not go through builtins.__import__ at all, so',
    '    wrapping that alone leaves a second door open. A meta_path finder sits',
    '    in front of both.',
    '    """',
    '',
    '    def find_spec(self, fullname, path=None, target=None):',
    '        root = fullname.partition(".")[0]',
    '        if root in _BLOCKED_ROOTS:',
    '            raise ImportError(f"{fullname!r} is not available to executed code. " + _WHY[root])',
    '        return None',
    '',
    '',
    '_sys.meta_path.insert(0, _ImportBlocker())',
    '',
    '# Evict anything already imported, so a live reference cannot be re-fetched',
    '# from the cache without going through the finder above.',
    'for _name in [n for n in _sys.modules if n.partition(".")[0] in _BLOCKED_ROOTS]:',
    '    _sys.modules.pop(_name, None)',
    '',
    '# Belt and braces: __import__ is what the import statement compiles to.',
    '# Built through a factory so the original import is captured in a CLOSURE',
    '# rather than left in module globals. An earlier version bound it globally',
    '# and then deleted it, which made the FIRST import after sealing raise',
    '# NameError instead of working. Executing this found that immediately;',
    '# matching its source text never would have.',
    'def _install_import_guard(_real):',
    '    def _guarded_import(name, *args, **kwargs):',
    '        if name.partition(".")[0] in _BLOCKED_ROOTS:',
    '            raise ImportError(f"{name!r} is not available to executed code.")',
    '        return _real(name, *args, **kwargs)',
    '',
    '    return _guarded_import',
    '',
    '',
    '_b.__import__ = _install_import_guard(_b.__import__)',
    '',
    'del _b, _sys, _install_import_guard',
  ].join('\n');
}

/**
 * Build the Python that gives executed code its ONE sanctioned network route.
 *
 * Measured in Chrome on 2026-09-22, against real Pyodide under nemar.org's
 * production policy: once `buildNamespaceSealSource` has run, executed code has
 * no way to reach the network at all. `js`, `pyodide` and `micropip` are gone,
 * and `urllib` fails with "unknown url type: https" because Pyodide ships no
 * socket transport. A runtime that cannot read the archive cannot do the one
 * thing this feature exists for, so the seal has to be paired with a client
 * rather than left to stand alone.
 *
 * WHAT THIS IS AND IS NOT A BOUNDARY AGAINST
 *
 * It is not a Python/JavaScript boundary, and nothing in Pyodide could be. A
 * function handed to executed code exposes its `__closure__` and `__globals__`,
 * and any JsProxy reaches the whole JavaScript world through its own
 * `constructor`. Hiding the reference harder buys nothing.
 *
 * The boundary is the fetch shim itself, which is why decision 1 of the phase
 * plan puts it there: the native fetch is deleted from the prototype chain and
 * the shim installed non-writable and non-configurable, so code that reaches
 * JavaScript still finds only the guarded function. That is also why nested
 * workers are blocked, since a fresh global is the one way to get an unshimmed
 * one. What the namespace seal buys is that the obvious routes are gone and a
 * model does not stumble onto one, not that a determined escape is impossible.
 *
 * @returns {string} Python source, run in the user namespace before the seal.
 */
export function buildDataClientSource() {
  return [
    'import importlib.util as _ilu',
    'import sys as _sys',
    'import types as _types',
    'import js as _js',
    '',
    'def _build_osa_client(_fetch):',
    '    """Close over the SHIMMED fetch, which is the enforcement point."""',
    '',
    '    async def fetch_bytes(url):',
    '        """Read a URL inside this community\'s fetch_allow and return bytes."""',
    '        response = await _fetch(url)',
    '        if not response.ok:',
    '            raise OSError("HTTP %s for %s" % (response.status, url))',
    '        buffer = await response.arrayBuffer()',
    '        return bytes(buffer.to_py())',
    '',
    '    async def fetch_text(url, encoding="utf-8"):',
    '        """The same, decoded."""',
    '        return (await fetch_bytes(url)).decode(encoding)',
    '',
    '    module = _types.ModuleType("osa")',
    '    module.__doc__ = (',
    '        "Network access for code running in the browser runtime. "',
    '        "Both calls are async and must be awaited: zarr\'s synchronous API "',
    '        "starts an IO thread, which Pyodide\'s main thread cannot do, and the "',
    '        "RuntimeError it raises names neither zarr nor the browser."',
    '    )',
    '    module.fetch_bytes = fetch_bytes',
    '    module.fetch_text = fetch_text',
    '    # Given a real spec and registered, so `import osa` works and so the',
    '    # static import gate can resolve it. A ModuleType built by hand has',
    '    # __spec__ of None, and find_spec RAISES on that rather than returning',
    '    # None, which the gate would read as unavailable and deny.',
    '    module.__spec__ = _ilu.spec_from_loader("osa", loader=None)',
    '    return module',
    '',
    '_sys.modules["osa"] = _build_osa_client(_js.fetch)',
    'osa = _sys.modules["osa"]',
    '',
    'del _ilu, _sys, _types, _js, _build_osa_client',
  ].join('\n');
}
