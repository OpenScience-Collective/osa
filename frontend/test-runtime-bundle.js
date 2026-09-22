// The runtime bundle, tested AS BUILT.
//
// Every other suite imports the source modules. The widget never does: it loads
// frontend/osa-runtime.bundle.js, minified. Minification renames functions and
// variables, and the worker source is assembled partly by serializing functions
// with toString, so a rename that is harmless in a module can leave the worker
// calling a name that no longer exists. That breakage exists only in the built
// file, so only a test of the built file can see it.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { loadPyodide } from 'pyodide';

let passed = 0;
let failed = 0;

function assert(condition, msg) {
  if (condition) {
    passed++;
    console.log(`  ok ${msg}`);
  } else {
    failed++;
    console.error(`\x1b[31m  x FAIL: ${msg}\x1b[0m`);
  }
}

const SUITE_TIMEOUT_MS = 180_000;
setTimeout(() => {
  console.error(`\x1b[31m  x FAIL: the suite did not finish within ${SUITE_TIMEOUT_MS / 1000}s.\x1b[0m`);
  process.exit(1);
}, SUITE_TIMEOUT_MS).unref();

const BUNDLE = new URL('./osa-runtime.bundle.js', import.meta.url);
const WIDGET = new URL('./osa-chat-widget.js', import.meta.url);
const PACKAGE_CACHE = new URL('../.cache/pyodide-packages/', import.meta.url).pathname;

console.log('='.repeat(60));
console.log('The runtime bundle, as built');
console.log('='.repeat(60));

console.log('\nthe widget carries the hash of the bundle it will load');
const bundleText = readFileSync(BUNDLE, 'utf8');
{
  const integrity = `sha384-${createHash('sha384').update(bundleText).digest('base64')}`;
  const widget = readFileSync(WIDGET, 'utf8');
  const declared = (widget.match(/const RUNTIME_BUNDLE_INTEGRITY = '([^']+)'/) || [])[1];
  // A mismatch is not cosmetic: the browser refuses the bundle, and every
  // community with client tools silently loses browser execution.
  assert(declared === integrity, `the widget's RUNTIME_BUNDLE_INTEGRITY is the bundle's sha384 (widget ${declared}, bundle ${integrity})`);
}

console.log('\nthe bundle loads as a classic script and exposes one global');
{
  delete globalThis.OSARuntime;
  // eslint-disable-next-line no-new-func
  new Function(bundleText)();
  const api = globalThis.OSARuntime;
  assert(api !== undefined, 'it defines OSARuntime');
  assert(Object.isFrozen(api), 'and freezes it, so the page cannot swap a piece out');
  for (const name of [
    'PyodideRuntime',
    'buildWorkerSource',
    'buildWorkerConfig',
    'createWorkerRuntime',
    'RUNTIME_STATE',
    'ClientToolController',
    'GATE_DECISION',
    'highlightPython',
  ]) {
    assert(api && api[name] !== undefined, `it exposes ${name}`);
  }
}

const api = globalThis.OSARuntime;

console.log('\nthe gate pieces the BUNDLE carries work as built');
{
  const html = api.highlightPython('x = "<img onerror=alert(1)>"  # ok');
  assert(!html.includes('<img'), 'the bundled highlighter escapes markup in the code');
  assert(html.includes('<span class="osa-py-str">'), 'and still highlights');
  // A private field or a renamed method would fail here and nowhere else.
  const rt = new api.PyodideRuntime({ runtime: { pyodide_version: '0.28.3' } });
  const controller = new api.ClientToolController({
    runtime: rt,
    tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    gate: async () => api.GATE_DECISION.DENY,
  });
  assert(JSON.stringify(controller.declared) === '["execute_code","get_full_output"]', 'the bundled controller declares its tools');
  const denied = await controller.answer({ call_id: 'b-deny', tool: 'execute_code', args: { code: 'x', description: 'd' } });
  assert(denied.status === 'denied' && denied.call_id === 'b-deny', 'and answers a declined request');
}

console.log('\nthe worker source the BUNDLE builds is real JavaScript');
{
  const source = api.buildWorkerSource({
    pyodide_version: '0.28.3',
    preload: ['numpy'],
    fetch_allow: ['https://zarr.nemar.org/'],
    limits: {},
  });
  let parseError = null;
  try {
    // eslint-disable-next-line no-new-func
    new Function(source);
  } catch (err) {
    parseError = err;
  }
  assert(parseError === null, `it parses${parseError ? ': ' + parseError.message : ''}`);
}

console.log('\nthe egress guard the BUNDLE builds runs in a real worker');
{
  // The guard serializes the allowlist check into the worker by value. If
  // minification renamed it while the guard still called it by its source name,
  // the worker would throw a ReferenceError on its first request.
  const source = api.buildWorkerSource({ pyodide_version: '0.28.3', fetch_allow: ['https://zarr.nemar.org/'], limits: {} });
  const guard = source.slice(source.indexOf('(function () {') + '(function () {'.length, source.indexOf('const runtime ='));
  const reply = await new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./test-workers/egress-probe.js', import.meta.url).href);
    worker.onmessage = (e) => { worker.terminate(); resolve(e.data); };
    worker.onerror = (e) => { worker.terminate(); resolve({ fatal: String(e.message || e) }); };
    setTimeout(() => { worker.terminate(); reject(new Error('probe timed out')); }, 20_000);
    worker.postMessage({
      guardSource: guard,
      sealTo: ['https://zarr.nemar.org/'],
      probes: { arbitrary: 'https://example.com/', lookalikeHost: 'https://zarr.nemar.org.evil.com/x' },
    });
  });
  assert(!reply.fatal, `the bundled guard evaluates in a worker${reply.fatal ? ': ' + reply.fatal : ''}`);
  const r = reply.results || {};
  assert(String(r.arbitrary).startsWith('DENIED'), `and still refuses an arbitrary origin (got ${r.arbitrary})`);
  assert(String(r.lookalikeHost).startsWith('DENIED'), `and a lookalike host (got ${r.lookalikeHost})`);
  assert(Array.isArray(r.__globalReachable) && r.__globalReachable.length === 0, 'and leaks nothing to global scope');
}

console.log('\nthe worker core the BUNDLE carries runs on real Pyodide');
{
  // Rebuilt from its own text, as the worker receives it. A minified reference
  // to a name outside the function would be a ReferenceError here and nowhere
  // in the source-module suites.
  // eslint-disable-next-line no-new-func
  const core = new Function(`return (${api.createWorkerRuntime.toString()});`)();
  // The configuration the BUNDLE builds, generated Python included.
  const config = api.buildWorkerConfig({ pyodide_version: '0.28.3', fetch_allow: ['http://127.0.0.1/allowed/'], limits: {} });

  const messages = [];
  const sealed = [];
  const runtime = core(config, {
    load: () => loadPyodide({ packageCacheDir: PACKAGE_CACHE, stdout: () => {}, stderr: () => {} }),
    seal: (prefixes) => sealed.push(prefixes),
    send: (message) => messages.push(message),
  });
  await runtime.handle({ type: 'boot' });
  const ready = messages.find((m) => m.type === 'ready');
  assert(ready !== undefined, `the bundled core boots (got ${JSON.stringify(messages.at(-1))})`);
  assert(sealed.length === 1 && sealed[0][0] === 'http://127.0.0.1/allowed/', 'and seals to fetch_allow');

  const run = async (code, id) => {
    const from = messages.length;
    await runtime.handle({ type: 'execute', call_id: id, code });
    return messages.slice(from).find((m) => m.type === 'result');
  };
  const ok = await run('answer = 6 * 7\nprint(answer)', 'b1');
  assert(ok && ok.status === 'ok' && ok.stdout === '42\n', `it executes (got ${JSON.stringify(ok && ok.stdout)})`);
  assert(ok && /answer: int = 42/.test(ok.summary), 'and summarizes');
  const denied = await run('import js', 'b2');
  assert(denied && denied.status === 'denied', 'and the seal holds');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
