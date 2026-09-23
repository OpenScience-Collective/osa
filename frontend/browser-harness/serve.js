#!/usr/bin/env bun
/**
 * Serve the browser runtime harness under a REAL Content-Security-Policy header.
 *
 * Bun and Node enforce no CSP, so no other test in the repository can say whether
 * Pyodide boots under the policy an embedding site actually sends. The path prefix
 * picks the policy, and everything else is identical across them:
 *
 *   /nemarlike/  nemar.org's production policy, plus the jsDelivr entry the loader needs
 *   /control/    no wasm grant at all, so Pyodide MUST fail: a harness whose negative
 *                control also passes has measured nothing
 *   /strict/, /loose/, /withpypi/, /withpypi_eval/  the variants the CSP question was
 *                first answered with (#431), kept so the answer can be re-measured
 *
 * Beside the pages it serves NEMAR's lock overlay the way the API does: each wheel
 * the overlay lists at runtime/nemar/<file>, and at tampered/nemar/<file> a valid
 * wheel one byte longer (see tamperWheel), the negative control for the browser's
 * integrity check.
 * harness-config.json carries the Pyodide version the npm package pins and NEMAR's
 * runtime config and overlay, so the page runs what ships.
 *
 * Usage: bun frontend/browser-harness/serve.js [port]
 * chrome.js imports startServer to drive the same pages headless in CI.
 */

import pyodidePackage from 'pyodide/package.json';

const FRONTEND = new URL('../', import.meta.url);
const ASSISTANTS = new URL('../../src/assistants/', import.meta.url);
const CDN = 'https://cdn.jsdelivr.net';

// The API's real wheel route sends this (src/api/routers/community.py,
// RUNTIME_WHEEL_CACHE_CONTROL) and so does the worker in front of it
// (workers/osa-worker/index.js, IMMUTABLE); this harness has to match or a
// warm-cache measurement here would prove nothing about what ships. A wheel's
// name is its identity, so this is correct for BOTH /runtime/ and /tampered/:
// the tampered bytes live at their own path (never the real wheel's name),
// so caching them aggressively cannot make a later request see the wrong
// bytes under a name that means something else. tests/test_api/
// test_runtime_wheel_cache_control.py keeps this string equal to the other
// two.
const RUNTIME_WHEEL_CACHE_CONTROL = 'public, max-age=31536000, immutable';

// Everything the runtime needs, held constant across strict and loose so the only
// difference is the eval grant itself.
const COMMON =
  "default-src 'self'; " +
  `connect-src 'self' ${CDN} https://zarr.nemar.org https://api.nemar.org; ` +
  "worker-src 'self' blob:; child-src 'self' blob:; img-src 'self' data: blob:; " +
  "style-src 'self' 'unsafe-inline'; form-action 'self'";

// nemar.org's PRODUCTION connect-src, verbatim as of website v0.2.16, plus the
// jsDelivr entry. PyPI is not in it.
const NEMAR_CONNECT =
  "connect-src 'self' https://*.nemar.org https://cdn.jsdelivr.net " +
  'https://widget.osc.earth https://develop-widget.osc.earth';
const PYPI_CONNECT = `${NEMAR_CONNECT} https://pypi.org https://files.pythonhosted.org`;
const REST =
  "default-src 'self'; worker-src 'self' blob:; child-src 'self' blob:; " +
  "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; form-action 'self'";

export const POLICIES = Object.freeze({
  // Exactly what nemar.org serves today, plus wasm.
  nemarlike: `script-src 'self' 'wasm-unsafe-eval' ${CDN}; ${NEMAR_CONNECT}; ${REST}`,
  // Same, but PyPI reachable. Isolates "blocked by CSP" from "needs unsafe-eval".
  withpypi: `script-src 'self' 'wasm-unsafe-eval' ${CDN}; ${PYPI_CONNECT}; ${REST}`,
  // PyPI reachable AND unsafe-eval granted: a package that works here but not in
  // withpypi genuinely needs the stronger grant.
  withpypi_eval: `script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval' ${CDN}; ${PYPI_CONNECT}; ${REST}`,
  strict: `script-src 'self' 'wasm-unsafe-eval' ${CDN}; ${COMMON}`,
  loose: `script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval' ${CDN}; ${COMMON}`,
  // The negative control: no wasm grant, so Pyodide must fail here.
  control: `script-src 'self' ${CDN}; ${COMMON}`,
});

/**
 * The wheel one byte longer and still a valid wheel: the byte becomes the zip's
 * comment. Only the entry's digest can tell it from the committed one, so a boot
 * that refuses it proves the digest is checked. Appending a bare byte would not:
 * it breaks the zip, and a boot would fail with or without the check.
 */
export function tamperWheel(bytes) {
  const end = bytes.length - 22;
  const isEndRecord = bytes[end] === 0x50 && bytes[end + 1] === 0x4b && bytes[end + 2] === 0x05 && bytes[end + 3] === 0x06;
  if (!isEndRecord || bytes[end + 20] !== 0 || bytes[end + 21] !== 0) {
    throw new Error('expected a wheel whose zip has no comment');
  }
  const tampered = new Uint8Array(bytes.length + 1);
  tampered.set(bytes);
  tampered[bytes.length - 2] = 1; // the comment length, little-endian
  tampered[bytes.length] = 0x20;
  return tampered;
}

/** A community's runtime config and overlay, and its wheels by file name. */
async function readCommunityRuntime(id) {
  const folder = new URL(`${id}/`, ASSISTANTS);
  const config = Bun.YAML.parse(await Bun.file(new URL('config.yaml', folder)).text());
  const python = config.runtime.python;
  const lockfile = new URL(python.lockfile, folder);
  const { packages } = JSON.parse(await Bun.file(lockfile).text());
  const wheels = new Map();
  for (const entry of Object.values(packages)) {
    wheels.set(entry.file_name, new Uint8Array(await Bun.file(new URL(`wheels/${entry.file_name}`, lockfile)).arrayBuffer()));
  }
  return { runtime: python, packages, wheels };
}

/**
 * A wheel's file name with a build tag inserted before its python tag, still
 * a name `_WHEEL_FILE_NAME` (src/core/config/runtime_lock.py) accepts: PEP
 * 427 allows an optional build tag there, so this is a real, valid rename,
 * not a synthetic one.
 */
function withBuildTag(fileName, tag) {
  const suffix = '-py3-none-any.whl';
  if (!fileName.endsWith(suffix)) throw new Error(`not a wheel file name: ${fileName}`);
  return `${fileName.slice(0, -suffix.length)}-${tag}${suffix}`;
}

/**
 * Start the harness server.
 *
 * @param {{port?: number}} [options] - 0 picks a free port.
 * @returns {Promise<import('bun').Server>}
 */
export async function startServer({ port = 8787 } = {}) {
  const nemar = await readCommunityRuntime('nemar');

  // A second overlay variant for the lock-change cache test (chrome.js): ONE
  // wheel (eegprep-lean) renamed with a build tag, same bytes and the same
  // sha256, so booting it must fetch exactly that one wheel from the network
  // while every other wheel this browser already has is served from its HTTP
  // cache. Built here, in memory, from the SAME read-only overlay above: the
  // real files under src/assistants/nemar/runtime/ are never touched.
  const changedEntry = nemar.packages['eegprep-lean'];
  const changedFileName = withBuildTag(changedEntry.file_name, '2');
  nemar.wheels.set(changedFileName, nemar.wheels.get(changedEntry.file_name));
  const lockChangedPackages = { ...nemar.packages, 'eegprep-lean': { ...changedEntry, file_name: changedFileName } };

  const harnessConfig = JSON.stringify({
    pyodide_version: pyodidePackage.version,
    nemar: { runtime: nemar.runtime, packages: nemar.packages },
    nemarLockChanged: { runtime: nemar.runtime, packages: lockChangedPackages },
  });

  return Bun.serve({
    hostname: '127.0.0.1',
    port,
    async fetch(request) {
      const { pathname } = new URL(request.url);
      const variant = Object.keys(POLICIES).find((name) => pathname.startsWith(`/${name}/`)) || null;
      const rest = variant ? pathname.slice(variant.length + 1) : pathname;
      const headers = { 'Cache-Control': 'no-store' };
      // Cross-origin isolation is deliberately NOT set. #431 says not to require
      // it, since it would constrain every embedding page, and cancellation
      // terminates the worker instead of relying on SharedArrayBuffer.
      if (variant) {
        headers['Content-Security-Policy'] = POLICIES[variant];
        headers['X-CSP-Variant'] = variant;
      }

      if (rest === '/browser-harness/harness-config.json') {
        return new Response(harnessConfig, { headers: { ...headers, 'Content-Type': 'application/json' } });
      }
      // A lookup against the overlay, as the API's route is: nothing it does not list.
      // Immutable, like the API's real route and the worker in front of it: a
      // wheel's name is its identity, so the browser can keep these bytes
      // forever without ever needing to revalidate them.
      const wheel = /^\/(runtime|tampered)\/nemar\/([^/]+)$/.exec(rest);
      if (wheel) {
        const bytes = nemar.wheels.get(wheel[2]);
        if (!bytes) return new Response('Not Found', { status: 404, headers });
        const body = wheel[1] === 'tampered' ? tamperWheel(bytes) : bytes;
        return new Response(body, {
          headers: { ...headers, 'Content-Type': 'application/octet-stream', 'Cache-Control': RUNTIME_WHEEL_CACHE_CONTROL },
        });
      }

      // Static files from frontend/, and never above it: the URL parser has
      // already folded any "..", and the prefix check refuses what is left.
      const file = new URL(`.${rest.endsWith('/') ? `${rest}index.html` : rest}`, FRONTEND);
      if (!file.pathname.startsWith(FRONTEND.pathname)) return new Response('Forbidden', { status: 403, headers });
      const found = Bun.file(file);
      if (!(await found.exists())) return new Response('Not Found', { status: 404, headers });
      return new Response(found, { headers: { ...headers, 'Content-Type': found.type } });
    },
  });
}

if (import.meta.main) {
  const server = await startServer({ port: Number(process.argv[2] || 8787) });
  console.log(`serving on http://127.0.0.1:${server.port}`);
  for (const name of Object.keys(POLICIES)) {
    console.log(`  http://127.0.0.1:${server.port}/${name}/browser-harness/index.html`);
  }
}
