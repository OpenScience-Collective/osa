/**
 * The browser workspace, against a REAL IndexedDB (epic #429, phase #433).
 *
 * Everything storage-independent is already covered by frontend/test-workspace.js
 * under Bun. What only a real browser can check, and what this page exists for,
 * is IndexedDB itself: that ClientToolController's persistence actually lands
 * there, that WorkspaceStore reads it back the same way, and that a SECOND page
 * load -- a fresh module graph, a fresh WorkspaceStore, the same origin -- still
 * sees what the first one wrote. Nothing here stubs IndexedDB or the runtime;
 * `osa.save_script`/`osa.save_artifact` run in real Pyodide and the writes they
 * produce go through the real `ClientToolController` -> `WorkspaceStore` path
 * production uses.
 *
 * `?phase=write` (default) boots a runtime, runs two real executions through a
 * real ClientToolController with a workspace attached, and checks what landed:
 * the result's `artifacts`, the derived manifest, `sizeUsed`, and the exported
 * zip's actual entries (parsed by hand below; the FORMAT is already proven
 * elsewhere by an independent reader, so this only checks the entries this
 * session's writes should have produced). It leaves what it expects in
 * localStorage, on the same origin, for the second phase to compare against.
 *
 * `?phase=read` is a FRESH page load: a new WorkspaceStore for the same
 * community, with no execution and no ClientToolController, reading back
 * exactly what the first phase wrote. It also exercises deleteAll and checks
 * the store is empty afterward.
 *
 * chrome.js runs both phases against the same served origin and requires
 * both to report every check ok: a workspace that silently failed to persist
 * would still leave `done: true` with SOME checks passing, which is why every
 * check that matters is asserted explicitly rather than inferred from "the
 * page did not throw".
 */
import { ClientToolController, GATE_DECISION } from '../osa-controller.js';
import { PyodideRuntime } from '../osa-runtime.js';
import { WorkspaceStore } from '../osa-workspace.js';

const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');
const results = [];

function log(line) {
  logEl.textContent += `${line}\n`;
  console.log('[harness]', line);
}

function check(name, ok, detail) {
  results.push({ name, ok: ok === true, detail: detail === undefined ? '' : String(detail) });
  log(`${ok === true ? 'ok  ' : 'FAIL'}  ${name}${detail !== undefined ? `  -- ${detail}` : ''}`);
}

/**
 * Entry names from a real zip's own central directory, read by hand.
 *
 * Not a general-purpose reader: it exists so this page can assert on the
 * exported zip's ACTUAL entries without importing a reader library into the
 * runtime bundle. The format itself (headers, CRC-32, the end-of-central-
 * directory record) is proven correct elsewhere by two independent readers
 * (`unzip` and Python's `zipfile`, in frontend/test-workspace.js and
 * tests/test_frontend/test_workspace_export.py); this only has to agree with
 * them on this one archive.
 */
function listZipEntries(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= 0; i--) {
    if (view.getUint32(i, true) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error('no end-of-central-directory record found');
  const entryCount = view.getUint16(eocd + 10, true);
  let offset = view.getUint32(eocd + 16, true);
  const names = [];
  for (let i = 0; i < entryCount; i++) {
    if (view.getUint32(offset, true) !== 0x02014b50) {
      throw new Error(`central directory record ${i} has the wrong signature`);
    }
    const nameLen = view.getUint16(offset + 28, true);
    const extraLen = view.getUint16(offset + 30, true);
    const commentLen = view.getUint16(offset + 32, true);
    names.push(new TextDecoder().decode(bytes.slice(offset + 46, offset + 46 + nameLen)));
    offset += 46 + nameLen + extraLen + commentLen;
  }
  return names;
}

const COMMUNITY = 'workspace-harness';
const SESSION_ID = 'harness-session-1';
const EXPECTATION_KEY = 'osa-workspace-harness-expected';

async function runWritePhase(rt) {
  const workspace = new WorkspaceStore({ community: COMMUNITY });
  check('WorkspaceStore.available is true in a real browser', workspace.available === true, `available=${workspace.available}`);

  const controller = new ClientToolController({
    runtime: rt,
    tools: [{ name: 'execute_code', runtime: 'python', requires_permission: false }],
    gate: async () => GATE_DECISION.RUN,
    workspace,
  });

  const r1 = await controller.answer({
    call_id: 'call-1',
    tool: 'execute_code',
    session_id: SESSION_ID,
    args: {
      code:
        'a = osa.save_script("greeting", "print(1)")\n' +
        'b = osa.save_artifact("data.txt", "hello")\n' +
        'print(a, b)',
      description: 'save a script and an artifact',
    },
    requires_permission: false,
  });
  check('run 1 succeeds', r1.status === 'ok', JSON.stringify(r1));
  check(
    'run 1 lists both explicit saves as artifacts',
    JSON.stringify(r1.artifacts) === JSON.stringify(['artifacts/data.txt', 'scripts/greeting.py']),
    JSON.stringify(r1.artifacts)
  );
  check('no host-only fields (full, files) reached this caller', !('full' in r1) && !('files' in r1), JSON.stringify(Object.keys(r1)));

  const r2 = await controller.answer({
    call_id: 'call-2',
    tool: 'execute_code',
    session_id: SESSION_ID,
    args: { code: 'print("nothing explicit here")', description: 'a plain run' },
    requires_permission: false,
  });
  check('run 2 succeeds', r2.status === 'ok', JSON.stringify(r2));
  check('run 2 lists no artifacts, since it saved nothing explicitly', JSON.stringify(r2.artifacts) === '[]', JSON.stringify(r2.artifacts));

  const manifests = await workspace.manifests();
  const manifest = manifests[SESSION_ID];
  check('the session has a derived manifest with 2 runs', !!manifest && manifest.runs.length === 2, JSON.stringify(manifest));
  const run1 = manifest?.runs.find((r) => r.ordinal === 1);
  check(
    'run 1 in the manifest carries its automatic AND explicit files',
    !!run1 &&
      run1.files.includes('scripts/run-001.py') &&
      run1.files.includes('results/run-001/stdout.txt') &&
      run1.files.includes('artifacts/data.txt') &&
      run1.files.includes('scripts/greeting.py'),
    JSON.stringify(run1)
  );
  check('run 1 in the manifest carries its description', run1?.description === 'save a script and an artifact', JSON.stringify(run1));

  const used = await workspace.sizeUsed();
  check('sizeUsed is greater than zero after real writes', used > 0, `used=${used}`);

  const zipBytes = await workspace.exportZip();
  const isZip = zipBytes.length > 4 && zipBytes[0] === 0x50 && zipBytes[1] === 0x4b && zipBytes[2] === 0x03 && zipBytes[3] === 0x04;
  check('exportZip produces real zip bytes (a PK local-file-header signature)', isZip, `length=${zipBytes.length}`);

  let entries = [];
  try {
    entries = listZipEntries(zipBytes);
  } catch (err) {
    check('the exported zip has a readable central directory', false, err.message);
  }
  const expectedEntries = [
    `${SESSION_ID}/manifest.json`,
    `${SESSION_ID}/notebook.ipynb`,
    `${SESSION_ID}/scripts/run-001.py`,
    `${SESSION_ID}/scripts/greeting.py`,
    `${SESSION_ID}/artifacts/data.txt`,
    `${SESSION_ID}/results/run-001/stdout.txt`,
    `${SESSION_ID}/results/run-002/stdout.txt`,
  ];
  const missing = expectedEntries.filter((name) => !entries.includes(name));
  check('every expected file is a real entry in the exported zip', missing.length === 0, `missing: ${missing.join(', ') || 'none'}; got ${entries.length} entries`);

  localStorage.setItem(EXPECTATION_KEY, JSON.stringify({ used, runCount: manifest ? manifest.runs.length : 0 }));
}

async function runReadPhase() {
  const expectedRaw = localStorage.getItem(EXPECTATION_KEY);
  check('the write phase left an expectation in localStorage on this origin', !!expectedRaw, expectedRaw || '(none)');
  if (!expectedRaw) return;
  const expected = JSON.parse(expectedRaw);

  // A FRESH store: nothing here was touched by the write phase's own instance,
  // so every read below proves IndexedDB itself persisted the data, not that
  // one JS object remembered it.
  const workspace = new WorkspaceStore({ community: COMMUNITY });
  check('WorkspaceStore.available is true on the second page load too', workspace.available === true);

  const manifests = await workspace.manifests();
  const manifest = manifests[SESSION_ID];
  check('after a fresh page load, the session manifest still exists', !!manifest, JSON.stringify(Object.keys(manifests)));
  check(
    'after a fresh page load, the run count is unchanged',
    !!manifest && manifest.runs.length === expected.runCount,
    `got ${manifest ? manifest.runs.length : 'none'}, expected ${expected.runCount}`
  );

  const used = await workspace.sizeUsed();
  check('after a fresh page load, sizeUsed matches what the first page wrote', used === expected.used, `got ${used}, expected ${expected.used}`);

  const zipBytes = await workspace.exportZip();
  check('after a fresh page load, exportZip still produces real bytes', zipBytes.length > 0, `length=${zipBytes.length}`);

  const deleted = await workspace.deleteAll();
  check('deleteAll reports success against a real database', deleted.ok === true, JSON.stringify(deleted));
  const usedAfterDelete = await workspace.sizeUsed();
  check('sizeUsed is zero after deleteAll', usedAfterDelete === 0, `used=${usedAfterDelete}`);
  localStorage.removeItem(EXPECTATION_KEY);
}

async function main() {
  const phase = new URLSearchParams(location.search).get('phase') === 'read' ? 'read' : 'write';
  let rt = null;
  try {
    if (phase === 'write') {
      const HARNESS = await (await fetch('./harness-config.json')).json();
      rt = new PyodideRuntime({
        runtime: {
          pyodide_version: HARNESS.pyodide_version,
          preload: [],
          allow_install: [],
          preload_on: 'widget_open',
          fetch_allow: [],
          index_urls: [],
          limits: { exec_seconds: 20 },
        },
        bootTimeoutMs: 45_000,
        onProgress: (p) => log(`  progress: ${p.phase}${p.package ? ` ${p.package}` : ''}`),
      });
      const { version } = await rt.boot();
      check('the runtime boots real Pyodide', true, `v${version}`);
      await runWritePhase(rt);
    } else {
      await runReadPhase();
    }
  } catch (err) {
    check(`the ${phase} phase completed without throwing`, false, (err && err.message) || String(err));
  } finally {
    if (rt) rt.terminate();
  }

  const ok = results.length > 0 && results.every((r) => r.ok);
  statusEl.textContent = ok ? 'OK' : 'FAILED';
  statusEl.className = ok ? 'ok' : 'fail';
  window.__harness = { done: true, ok, phase, results };
}

main();
