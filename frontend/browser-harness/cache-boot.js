/**
 * Boot NEMAR's runtime once, and only that, on a fresh page.
 *
 * `harness.js` already proves the overlay boots correctly; this page exists so
 * `chrome.js` has something minimal to point a NEW page and worker target at,
 * for the warm-cache and lock-change measurements (README.md, "What it
 * measures"). Everything those measure is external to this page: which of
 * this boot's wheel requests DevTools sees served from the browser's own HTTP
 * cache. This page itself only has to boot and say so.
 *
 * `?variant=` picks which overlay from harness-config.json to boot: `nemar`
 * (the shipped overlay, for cold/warm) or `nemarLockChanged` (one wheel
 * renamed with a build tag, same bytes, for the lock-change scenario).
 */
import { PyodideRuntime } from '../osa-runtime.js';

async function main() {
  const variant = new URLSearchParams(location.search).get('variant') || 'nemar';
  const started = performance.now();
  let rt = null;
  try {
    const harness = await (await fetch('./harness-config.json')).json();
    const overlay = harness[variant];
    if (!overlay) throw new Error(`harness-config.json has no overlay named ${JSON.stringify(variant)}`);

    rt = new PyodideRuntime({
      runtime: overlay.runtime,
      lock: { packages: overlay.packages, baseUrl: new URL('../runtime/nemar/', location.href).href },
      bootTimeoutMs: 120_000,
    });
    const { version } = await rt.boot();
    // Proof the boot used the overlay it was handed, not merely that
    // loadPyodide resolved: a wrong or stale wheel would fail this import.
    const imported = await rt.execute('import eegprep_lean, zarr\n"ok"');
    if (imported.status !== 'ok') throw new Error(`overlay packages did not import: ${imported.status} ${imported.stderr}`);

    window.__harness = { done: true, ok: true, variant, version, elapsedMs: Math.round(performance.now() - started) };
  } catch (err) {
    window.__harness = {
      done: true,
      ok: false,
      variant,
      error: (err && err.message) || String(err),
      elapsedMs: Math.round(performance.now() - started),
    };
  } finally {
    if (rt) rt.terminate();
  }
}

main();
