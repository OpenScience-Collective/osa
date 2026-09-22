/**
 * Egress control tests (#431 step 2).
 *
 * The allow decision is a pure function, so most of this is exhaustive on the
 * ways an allowlist is usually wrong rather than on the happy path. The happy
 * path is one line; the bypasses are the product.
 *
 * Run with: bun frontend/test-egress.js
 */

import {
  DENY_REASON,
  buildDataClientSource,
  buildEgressGuardSource,
  buildNamespaceSealSource,
  isUrlAllowed,
} from './osa-egress.js';

let passed = 0;
let failed = 0;

const SUITE_TIMEOUT_MS = 30_000;
const watchdog = setTimeout(() => {
  console.error('\n  x FAIL: suite did not finish; something awaited a promise that never settles');
  process.exit(1);
}, SUITE_TIMEOUT_MS);
watchdog.unref?.();

function allowed(url, prefixes, msg) {
  const v = isUrlAllowed(url, prefixes);
  if (v.allowed) {
    console.log(`  ok ${msg}`);
    passed++;
  } else {
    console.error(`  x FAIL: ${msg}\n    expected ALLOW, got deny (${v.reason})`);
    failed++;
  }
}

function denied(url, prefixes, expectedReason, msg) {
  const v = isUrlAllowed(url, prefixes);
  if (!v.allowed && (!expectedReason || v.reason === expectedReason)) {
    console.log(`  ok ${msg}`);
    passed++;
  } else if (v.allowed) {
    console.error(`  x FAIL: ${msg}\n    expected DENY, got ALLOW`);
    failed++;
  } else {
    console.error(`  x FAIL: ${msg}\n    expected reason ${expectedReason}, got ${v.reason}`);
    failed++;
  }
}

function assert(cond, msg) {
  if (cond) {
    console.log(`  ok ${msg}`);
    passed++;
  } else {
    console.error(`  x FAIL: ${msg}`);
    failed++;
  }
}

const ALLOW = ['https://zarr.nemar.org/', 'https://api.nemar.org/datasets/'];

console.log('='.repeat(60));
console.log('Runtime egress control');
console.log('='.repeat(60));

console.log('\nthe data plane is reachable');
allowed('https://zarr.nemar.org/nm000103/zarr/index.json', ALLOW, 'an allowed origin and path');
allowed('https://zarr.nemar.org/', ALLOW, 'the bare allowed origin');
allowed('https://api.nemar.org/datasets/nm000103', ALLOW, 'a path under an allowed prefix');

console.log('\nTHE BYPASSES. A string-prefix allowlist gets every one of these wrong.');
denied(
  'https://zarr.nemar.org.evil.com/steal',
  ALLOW,
  DENY_REASON.NOT_ALLOWED,
  'a host that merely STARTS WITH an allowed host is refused'
);
denied(
  'https://api.nemar.org/datasets-secret/x',
  ALLOW,
  DENY_REASON.NOT_ALLOWED,
  'a path that merely starts with an allowed path segment is refused'
);
denied(
  'https://evil.com/?next=https://zarr.nemar.org/',
  ALLOW,
  DENY_REASON.NOT_ALLOWED,
  'an allowed URL in the query string does not launder the real destination'
);
denied(
  'https://evil.com/#https://zarr.nemar.org/',
  ALLOW,
  DENY_REASON.NOT_ALLOWED,
  'nor does one in the fragment'
);
denied(
  'https://user:pass@zarr.nemar.org/x',
  ALLOW,
  DENY_REASON.CREDENTIALS_IN_URL,
  'credentials in the URL are refused even on an ALLOWED origin'
);
denied('http://zarr.nemar.org/x', ALLOW, DENY_REASON.NOT_ALLOWED, 'http is a different origin from https and is refused');
denied('https://zarr.nemar.org:8443/x', ALLOW, DENY_REASON.NOT_ALLOWED, 'a different port is a different origin');

// A prefix written WITHOUT a trailing slash is ordinary config, and it is where a
// naive string-prefix allowlist actually breaks. Every bypass case above happens
// to survive a naive matcher purely because the entries end in "/", which is an
// accident of how they were written and not a property of the code. Mutation
// testing caught that: swapping in raw startsWith matching left all of them
// passing. These are the cases that fail it.
console.log('\nprefixes WITHOUT a trailing slash, where string matching really breaks');
{
  const NO_SLASH = ['https://zarr.nemar.org'];
  allowed('https://zarr.nemar.org/nm000103/x', NO_SLASH, 'the allowed origin still works');
  denied(
    'https://zarr.nemar.org.evil.com/steal',
    NO_SLASH,
    DENY_REASON.NOT_ALLOWED,
    'a lookalike host is refused even though it string-prefix matches'
  );
  denied(
    'https://zarr.nemar.org.attacker.test/',
    NO_SLASH,
    DENY_REASON.NOT_ALLOWED,
    'and so is another suffix on the same trick'
  );

  const PATH_NO_SLASH = ['https://api.nemar.org/datasets'];
  allowed('https://api.nemar.org/datasets/nm1', PATH_NO_SLASH, 'a path under the prefix works');
  denied(
    'https://api.nemar.org/datasets-private/nm1',
    PATH_NO_SLASH,
    DENY_REASON.NOT_ALLOWED,
    'a sibling path that string-prefix matches is refused'
  );
}

console.log('\nnon-http transports are refused by scheme, not by allowlist');
denied('data:text/plain,hello', ALLOW, DENY_REASON.SCHEME, 'data: is refused');
denied('blob:https://zarr.nemar.org/abc', ALLOW, DENY_REASON.SCHEME, 'blob: is refused');
denied('file:///etc/passwd', ALLOW, DENY_REASON.SCHEME, 'file: is refused');
denied('wss://zarr.nemar.org/live', ALLOW, DENY_REASON.SCHEME, 'wss: is refused');
denied('javascript:alert(1)', ALLOW, DENY_REASON.SCHEME, 'javascript: is refused');

console.log('\ndegenerate input fails closed');
denied('not a url', ALLOW, DENY_REASON.UNPARSEABLE, 'an unparseable URL is refused');
denied('https://zarr.nemar.org/x', [], DENY_REASON.NOT_ALLOWED, 'an EMPTY allowlist allows nothing');
denied('https://zarr.nemar.org/x', undefined, DENY_REASON.NOT_ALLOWED, 'an undefined allowlist allows nothing');
{
  // A malformed entry must not take the list down with it, because a throwing
  // allowlist most likely reads as "the runtime is broken" rather than
  // "the config is wrong".
  const mixed = ['::: not a url :::', 'https://zarr.nemar.org/'];
  allowed('https://zarr.nemar.org/ok', mixed, 'a malformed entry is skipped, the good entry still works');
  denied('https://evil.com/', mixed, DENY_REASON.NOT_ALLOWED, 'and a malformed entry does not become a wildcard');
}

console.log('\ncase and normalization');
allowed('https://ZARR.nemar.org/x', ALLOW, 'host case is normalized by URL parsing');
denied('https://zarr.nemar.org/../secret', ['https://zarr.nemar.org/data/'], DENY_REASON.NOT_ALLOWED, 'dot segments cannot escape an allowed path prefix');

console.log('\ndot segments in the TARGET, which the prefix comparison must not be fooled by');
{
  // The allowlist rejects entries containing dot segments, but the request side
  // is attacker-controlled and must be handled by resolution rather than by
  // refusal. WHATWG URL parsing collapses dot segments before `pathname` is
  // read, including percent-encoded ones: `%2e` is a dot to the path parser.
  // These assert that the comparison happens AFTER that resolution, so a path
  // that climbs out of the allowed prefix is denied on the way out.
  const allow = ['https://zarr.nemar.org/nm000103/'];
  assert(!isUrlAllowed('https://zarr.nemar.org/nm000103/../nm000999/x', allow).allowed,
    'a literal .. that climbs out of the allowed prefix is denied');
  assert(!isUrlAllowed('https://zarr.nemar.org/nm000103/%2e%2e/nm000999/x', allow).allowed,
    'a PERCENT-ENCODED .. is denied too, because %2e is a dot to the path parser');
  assert(!isUrlAllowed('https://zarr.nemar.org/nm000103/a/../../nm000999/x', allow).allowed,
    'several segments of climbing are resolved, not just one');
  assert(isUrlAllowed('https://zarr.nemar.org/nm000103/./a', allow).allowed,
    'a single dot resolves in place and stays allowed, so this is resolution and not a ban on dots');
  assert(isUrlAllowed('https://zarr.nemar.org/nm000103/a/../b', allow).allowed,
    'climbing that stays INSIDE the prefix is still allowed');
}

console.log('\nthe generated worker guard');
{
  const src = buildEgressGuardSource({ bootAllow: ['https://cdn.jsdelivr.net/'] });
  assert(src.includes('credentials: \'omit\''), 'fetch is forced to credentials: omit');
  assert(src.includes('XMLHttpRequest'), 'XMLHttpRequest is shimmed, not just fetch');
  assert(src.includes('withCredentials = false'), 'XHR credentials are disabled too');
  // Installed UNCONDITIONALLY. The runtime probe below cannot tell 'blocked'
  // from 'absent in this environment', so the unconditional install is pinned
  // here, where an `if (self.WebSocket)` regression is visible.
  assert(src.includes("__install('WebSocket'"), 'WebSocket is replaced');
  assert(src.includes("__install('EventSource'"), 'EventSource is replaced');
  assert(src.includes('cdn.jsdelivr.net'), 'the boot allowlist reaches the guard');
  assert(src.includes('__egress.sealed = true'), 'sealing is present');
  assert(
    src.includes('function isUrlAllowed') || src.includes('isUrlAllowed ='),
    'the SAME allow function is serialized in, not a second copy of the rule'
  );
}

console.log('\nthe namespace seal');
{
  const py = buildNamespaceSealSource();
  assert(/micropip/.test(py), 'micropip is removed, which is what makes allow_install structural');
  assert(/"js"/.test(py), 'the js bridge is removed, so js.fetch cannot route around the shim');
  assert(/pyodide_js/.test(py), 'pyodide_js is removed');
}

console.log('\nTHE GUARD RUNNING IN A REAL WORKER, not inspected as a string');
{
  const worker = new Worker(new URL('./test-workers/egress-probe.js', import.meta.url).href);
  const reply = await new Promise((resolve, reject) => {
    worker.onmessage = (e) => resolve(e.data);
    worker.onerror = (e) => reject(new Error('probe worker error: ' + (e.message || e)));
    setTimeout(() => reject(new Error('probe timed out')), 20_000);
    worker.postMessage({
      guardSource: buildEgressGuardSource({ bootAllow: ['https://cdn.jsdelivr.net/'] }),
      sealTo: ['https://zarr.nemar.org/'],
      probes: {
        allowedDataPlane: 'https://zarr.nemar.org/nm000103/zarr/index.json',
        bootOriginAfterSeal: 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.js',
        lookalikeHost: 'https://zarr.nemar.org.evil.com/steal',
        arbitrary: 'https://example.com/',
        credentialsInUrl: 'https://user:pass@zarr.nemar.org/x',
      },
    });
  });
  worker.terminate();

  assert(!reply.fatal, `the guard evaluates inside a worker${reply.fatal ? ': ' + reply.fatal : ''}`);
  const r = reply.results || {};

  assert(r.allowedDataPlane !== undefined && !String(r.allowedDataPlane).startsWith('DENIED'),
    'an allowed data-plane URL gets PAST the guard');
  assert(String(r.lookalikeHost).startsWith('DENIED'), 'a lookalike host is refused at runtime');
  assert(String(r.arbitrary).startsWith('DENIED'), 'an arbitrary origin is refused at runtime');
  assert(String(r.credentialsInUrl).startsWith('DENIED'), 'credentials in the URL are refused at runtime');
  assert(String(r.bootOriginAfterSeal).startsWith('DENIED'),
    'THE SEAL HOLDS: the boot-only wheel origin is refused once sealed');

  // Each of the following was a PROVEN bypass before it was fixed.
  assert(Array.isArray(r.__globalReachable) && r.__globalReachable.length === 0,
    `NOTHING the guard declares is reachable from global scope (found: ${JSON.stringify(r.__globalReachable)})`);
  assert(r.__reseal === 'REFUSED',
    'sealing is ONE-SHOT: a second seal is refused rather than re-widening the allowlist');

  // XHR is an independent network path and previously had zero runtime coverage:
  // deleting its check killed no assertions at all.
  assert(r.__xhrDenied === 'DENIED:not_in_fetch_allow' || r.__xhrDenied === 'NO_XHR_IN_ENV',
    `XMLHttpRequest to a disallowed origin is refused at runtime (got ${r.__xhrDenied})`);
  assert(r.__xhrAllowed === 'PASSED_GUARD' || r.__xhrAllowed === 'NO_XHR_IN_ENV',
    `XMLHttpRequest to an allowed origin passes the guard (got ${r.__xhrAllowed})`);

  assert(String(r.__importScripts).startsWith('DENIED') || r.__importScripts === 'NO_IMPORTSCRIPTS_IN_ENV',
    `importScripts to a disallowed origin is refused (got ${r.__importScripts})`);

  assert(String(r.__WebSocket).startsWith('DENIED'), 'WebSocket construction is refused at runtime');
  assert(String(r.__EventSource).startsWith('DENIED'), 'EventSource construction is refused at runtime');
  // A nested worker would get a fresh global with the native fetch intact, so
  // it is the one transport that defeats every shim above at once.
  assert(String(r.__Worker).startsWith('DENIED'), 'a nested Worker cannot be spawned to obtain an unshimmed global');
  assert(String(r.__SharedWorker).startsWith('DENIED'), 'nor a SharedWorker');
}

console.log('\nTOCTOU, decided by what the SERVER received, not by which coercion won');
{
  // The one assertion in this file that watches the wire. An argument-shape
  // confusion is only a vulnerability if the bytes leave for the attacker's
  // path, and only a real server can say whether they did. A local one keeps
  // this a unit test: no live host, no network, deterministic.
  const received = [];
  const server = Bun.serve({
    port: 0,
    fetch(req) {
      received.push(new URL(req.url).pathname);
      return new Response('ok');
    },
  });
  const base = `http://127.0.0.1:${server.port}`;

  try {
    const worker = new Worker(new URL('./test-workers/egress-probe.js', import.meta.url).href);
    const reply = await new Promise((resolve, reject) => {
      worker.onmessage = (e) => resolve(e.data);
      worker.onerror = (e) => reject(new Error('probe worker error: ' + (e.message || e)));
      setTimeout(() => reject(new Error('probe timed out')), 20_000);
      worker.postMessage({
        guardSource: buildEgressGuardSource({ bootAllow: [`${base}/boot/`] }),
        sealTo: [`${base}/allowed/`],
        probes: { allowedLocal: `${base}/allowed/ok` },
        toctou: { url: `${base}/allowed/ok`, evil: `${base}/evil/steal` },
      });
    });
    worker.terminate();
    assert(!reply.fatal, `the TOCTOU probe evaluates${reply.fatal ? ': ' + reply.fatal : ''}`);
    const r = reply.results || {};

    // Proves the server is reachable and the harness is really talking to it.
    // Without this, a guard that denied EVERYTHING would pass the check below
    // vacuously, which is the same vacuous pass the XHR block used to have.
    assert(r.allowedLocal === 'reached_network',
      `an allowed local URL genuinely reaches the server (got ${r.allowedLocal})`);
    assert(received.includes('/allowed/ok'), 'the server recorded the allowed request');

    // THE PROPERTY: the URL the guard checked is the URL that was dispatched.
    // Reverting the shim to check `input.url` and dispatch `input` puts
    // '/evil/steal' in this list under a browser's coercion and is the bypass
    // this exists to catch.
    assert(!received.some((path) => path.startsWith('/evil')),
      `NO request reached the disallowed path, whichever coercion the runtime uses (server saw: ${JSON.stringify(received)})`);
    assert(r.__toctou === 'REACHED' || String(r.__toctou).startsWith('DENIED'),
      `the confused request either was denied or resolved to the allowed URL (got ${r.__toctou})`);
    assert(r.__toctouGetter === 'REACHED' || String(r.__toctouGetter).startsWith('DENIED'),
      `a mutating url getter is normalized once, not read twice (got ${r.__toctouGetter})`);
  } finally {
    server.stop(true);
  }
}

console.log('\nthe data client is real Python, checked by a real compiler');
{
  // A malformed multi-line f-string in the namespace seal passed every regex
  // assertion and only failed when the generated source was executed. This one
  // cannot be executed outside Pyodide, since it imports the js bridge, so it
  // is handed to a compiler instead. That is the strongest check available
  // here, and strictly stronger than matching substrings.
  const py = buildDataClientSource();
  // Fed on stdin rather than embedded in -c, because it must be COMPILED and
  // not run: it imports the js bridge, which exists only inside Pyodide.
  const proc = Bun.spawnSync(
    ['python3', '-c', 'import sys; compile(sys.stdin.read(), "osa-data-client", "exec"); print("COMPILED")'],
    { stdin: new TextEncoder().encode(py) }
  );
  const out = proc.stdout.toString().trim();
  assert(out === 'COMPILED',
    `the generated data client compiles (got: ${out || proc.stderr.toString().trim().split('\n').slice(-2).join(' | ')})`);

  assert(/def fetch_bytes/.test(py) && /def fetch_text/.test(py), 'it offers both a bytes and a text read');
  assert(/module.__spec__ = _ilu.spec_from_loader/.test(py),
    'it carries a real __spec__, without which find_spec RAISES and the import gate denies `import osa`');
  assert(/del _ilu, _sys, _types, _js, _build_osa_client/.test(py),
    'the bridge it was built from is not left lying in the namespace');
}

console.log('\nthe namespace seal, EXECUTED rather than regex-matched');
{
  const py = buildNamespaceSealSource();
  const proc = Bun.spawnSync(['python3', '-c', py + `

import importlib, json
out = {}
def check(k, fn):
    try:
        fn(); out[k] = "REACHED"
    except ImportError: out[k] = "BLOCKED"
    except Exception as e: out[k] = "OTHER:" + type(e).__name__

# ctypes stands in for the blocked roots. pyodide, micropip and js do not exist
# in CPython, so blocking them here would prove nothing; ctypes is in the same
# blocked set AND importable by default, so it is the one that demonstrates the
# finder actually works.
check("import_statement", lambda: __import__("ctypes"))
check("importlib", lambda: importlib.import_module("ctypes"))
check("submodule", lambda: importlib.import_module("ctypes.util"))
check("unblocked", lambda: importlib.import_module("base64"))
print("RESULTS:" + json.dumps(out))
`]);

  const stdout = proc.stdout ? new TextDecoder().decode(proc.stdout) : '';
  const line = stdout.split('\n').find((l) => l.startsWith('RESULTS:'));
  if (!line) {
    const err = proc.stderr ? new TextDecoder().decode(proc.stderr) : '';
    assert(false, `the generated Python runs without error (stderr: ${err.slice(-300)})`);
  } else {
    const out = JSON.parse(line.slice('RESULTS:'.length));
    assert(out.import_statement === 'BLOCKED', 'a blocked root is refused via the import statement');
    // importlib.import_module does NOT go through builtins.__import__, which is
    // why evicting sys.modules and wrapping __import__ both miss it. The
    // meta_path finder is what closes this.
    assert(out.importlib === 'BLOCKED', 'and via importlib.import_module, which bypasses __import__');
    assert(out.submodule === 'BLOCKED', 'and for a submodule of a blocked root');
    assert(out.unblocked === 'REACHED', 'while an unblocked module still imports, so the seal has not broken Python');
  }
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed === 0 ? 0 : 1);
