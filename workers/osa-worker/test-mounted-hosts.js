/**
 * Agreement test: MOUNTED_HOSTS in index.js must equal the hostnames in the
 * [[routes]] patterns of wrangler.toml (#437, #442).
 *
 * These are two files that have to say the same thing and nothing enforces
 * it, so they can drift. Both drift directions are bad, and one of them is
 * completely silent:
 *
 *   Host in a route but NOT in MOUNTED_HOSTS.
 *     Cloudflare invokes the worker, stripMountPrefix is a no-op, every path
 *     keeps its "/osa" prefix, and every route matcher misses. Every request
 *     404s. Loud to the user, and the 404 fall-through logs both paths, so it
 *     is at least diagnosable from `wrangler tail`.
 *
 *   Host in MOUNTED_HOSTS but NOT in a route.
 *     Cloudflare never invokes the worker for that hostname at all, because
 *     nothing routes to it. No worker code runs, so no console.warn can ever
 *     fire and no application-level log can catch it. The only symptom is an
 *     external request failing, and nothing here watches that. This is the
 *     direction this test exists for: it is invisible at runtime, so it has
 *     to be caught at build time.
 *
 * Run with: bun workers/osa-worker/test-mounted-hosts.js
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

let passed = 0;
let failed = 0;

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    console.error(`  x FAIL: ${message}`);
    console.error(`    Expected: ${expected}`);
    console.error(`    Actual:   ${actual}`);
    failed++;
  } else {
    console.log(`  ok ${message}`);
    passed++;
  }
}

function assert(condition, message) {
  if (!condition) {
    console.error(`  x FAIL: ${message}`);
    failed++;
  } else {
    console.log(`  ok ${message}`);
    passed++;
  }
}

console.log('='.repeat(60));
console.log('MOUNTED_HOSTS / wrangler.toml agreement');
console.log('='.repeat(60));

const indexSource = readFileSync(join(here, 'index.js'), 'utf8');
const wranglerSource = readFileSync(join(here, 'wrangler.toml'), 'utf8');

// Parse MOUNTED_HOSTS out of index.js. Read the source rather than importing
// the module: the constant is deliberately not exported, and exporting it
// only so a test can read it would widen the module's surface for no runtime
// reason.
const mountedMatch = indexSource.match(/const MOUNTED_HOSTS = new Set\(\[([^\]]*)\]\)/);
assert(mountedMatch !== null, 'MOUNTED_HOSTS is declared in index.js as a Set literal');

const mountedHosts = mountedMatch
  ? [...mountedMatch[1].matchAll(/['"]([^'"]+)['"]/g)].map((m) => m[1]).sort()
  : [];

// Parse the hostname out of every [[routes]] pattern in wrangler.toml.
// A pattern looks like "widget.osc.earth/osa/*"; the hostname is everything
// before the first slash. Commented-out lines are ignored, which matters
// because this file carries a lot of explanatory prose containing examples.
// A host carries two routes (#500), so the hostnames are de-duplicated.
const routeHosts = [
  ...new Set(
    [...wranglerSource.matchAll(/^\s*pattern\s*=\s*["']([^"']+)["']/gm)].map((m) => m[1].split('/')[0])
  ),
].sort();

// The prefixes the worker actually strips, so the patterns can be checked for
// them. The shared edge host mounts the dev worker at its own "-dev" path, so
// the expected prefix depends on which wrangler environment a route belongs
// to; expectedPrefix mirrors mountPrefixFor in index.js.
function constant(name) {
  const m = indexSource.match(new RegExp(`const ${name} = ['"]([^'"]+)['"]`));
  return m ? m[1] : null;
}
const mountPrefix = constant('MOUNT_PREFIX');
const edgeHost = constant('EDGE_HOST');
const devEdgeMountPrefix = constant('DEV_EDGE_MOUNT_PREFIX');
assert(mountPrefix !== null, 'MOUNT_PREFIX is declared in index.js');
assert(edgeHost !== null && devEdgeMountPrefix !== null, 'EDGE_HOST and DEV_EDGE_MOUNT_PREFIX are declared in index.js');

function expectedPrefix(host, environment) {
  return host === edgeHost && environment === 'dev' ? devEdgeMountPrefix : mountPrefix;
}

// Each pattern with the wrangler environment it belongs to: top-level
// [[routes]] is production, [[env.<name>.routes]] is <name>. Walk the table
// headers rather than matching patterns alone, since the same host can
// appear in both environments at different prefixes.
const routes = [];
{
  let environment = null;
  for (const line of wranglerSource.split('\n')) {
    const header = line.match(/^\s*\[\[?([^\]]+)\]\]?\s*$/);
    if (header) {
      const table = header[1].trim();
      const envMatch = table.match(/^env\.([^.]+)\.routes$/);
      environment = table === 'routes' ? 'production' : envMatch ? envMatch[1] : null;
      continue;
    }
    const pattern = line.match(/^\s*pattern\s*=\s*["']([^"']+)["']/);
    if (pattern && environment !== null) {
      routes.push({ environment, pattern: pattern[1], host: pattern[1].split('/')[0] });
    }
  }
}
assertEqual(
  routes.length,
  [...wranglerSource.matchAll(/^\s*pattern\s*=\s*["']([^"']+)["']/gm)].length,
  'every pattern in wrangler.toml sits under a [[routes]] or [[env.<name>.routes]] table'
);

console.log(`\nMOUNTED_HOSTS: ${JSON.stringify(mountedHosts)}`);
console.log(`route hosts:   ${JSON.stringify(routeHosts)}`);
console.log(`MOUNT_PREFIX:  ${mountPrefix}  (${edgeHost} in dev: ${devEdgeMountPrefix})\n`);

assert(routeHosts.length > 0, 'wrangler.toml declares at least one [[routes]] pattern');
assert(mountedHosts.length > 0, 'MOUNTED_HOSTS is non-empty');

assertEqual(
  JSON.stringify(mountedHosts),
  JSON.stringify(routeHosts),
  'every routed hostname is in MOUNTED_HOSTS, and every MOUNTED_HOSTS entry is routed'
);

// Each direction named separately, so a failure says which way it drifted
// rather than only that two lists differ.
for (const host of routeHosts) {
  assert(
    mountedHosts.includes(host),
    `routed host ${host} is in MOUNTED_HOSTS (otherwise every request to it 404s)`
  );
}
for (const host of mountedHosts) {
  assert(
    routeHosts.includes(host),
    `MOUNTED_HOSTS entry ${host} has a route (otherwise the worker is never invoked and nothing logs)`
  );
}

// The strip is only correct if the routes actually mount at the prefix the
// worker strips. A route on "widget.osc.earth/something-else/*" with
// MOUNT_PREFIX "/osa" would pass the hostname check above and still be wrong.
console.log('');
for (const { environment, pattern, host } of routes) {
  const prefix = expectedPrefix(host, environment);
  const path = pattern.slice(pattern.indexOf('/'));
  assert(
    path === `${prefix}*` || path.startsWith(`${prefix}/`),
    `route pattern ${pattern} (${environment}) mounts at ${prefix}`
  );
}

// Each mounted host needs BOTH routes (#500), in every environment that
// routes it: "/osa/*" does not match the bare "/osa", which otherwise reaches
// the placeholder origin and answers 522. The bare route ends in "*" because
// a pattern without a trailing wildcard never matches a URL with a query
// string, so "/osa?x=1" would still 522.
console.log('');
for (const environment of [...new Set(routes.map((r) => r.environment))]) {
  const patterns = routes.filter((r) => r.environment === environment).map((r) => r.pattern);
  for (const host of [...new Set(routes.filter((r) => r.environment === environment).map((r) => r.host))]) {
    const prefix = expectedPrefix(host, environment);
    assert(patterns.includes(`${host}${prefix}/*`), `${environment}: ${host} routes ${prefix}/*`);
    assert(patterns.includes(`${host}${prefix}*`), `${environment}: ${host} routes the bare ${prefix}, query included (${prefix}*)`);
    assert(!patterns.includes(`${host}${prefix}`), `${environment}: ${host} has no exact ${prefix} route, which a query string would miss`);
  }
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);

if (failed === 0) {
  console.log('\nAll tests passed!');
  process.exit(0);
} else {
  console.log(`\n${failed} test(s) failed`);
  process.exit(1);
}
