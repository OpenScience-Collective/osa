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
  isUrlAllowed,
  DENY_REASON,
  buildEgressGuardSource,
  buildNamespaceSealSource,
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

console.log('\nthe generated worker guard');
{
  const src = buildEgressGuardSource({ bootAllow: ['https://cdn.jsdelivr.net/'] });
  assert(src.includes('credentials: \'omit\''), 'fetch is forced to credentials: omit');
  assert(src.includes('XMLHttpRequest'), 'XMLHttpRequest is shimmed, not just fetch');
  assert(src.includes('withCredentials = false'), 'XHR credentials are disabled too');
  assert(src.includes('self.WebSocket = function'), 'WebSocket is replaced');
  assert(src.includes('self.EventSource = function'), 'EventSource is replaced');
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
      // Sealed to the data plane only. The boot allowlist must NOT survive.
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
  assert(String(r.lookalikeHost).startsWith('DENIED'),
    'a lookalike host is refused at runtime, not merely in theory');
  assert(String(r.arbitrary).startsWith('DENIED'),
    'an arbitrary origin is refused at runtime');
  assert(String(r.credentialsInUrl).startsWith('DENIED'),
    'credentials in the URL are refused at runtime');

  // The seal is the whole two-phase design. If the boot origin survives it,
  // executed code inherits the reach that installing packages needed.
  assert(String(r.bootOriginAfterSeal).startsWith('DENIED'),
    'THE SEAL HOLDS: the boot-only wheel origin is refused once sealed');

  assert(String(r.websocket).startsWith('DENIED'), 'WebSocket construction is refused at runtime');
  assert(String(r.eventsource).startsWith('DENIED'), 'EventSource construction is refused at runtime');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed === 0 ? 0 : 1);
