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
import { WORKSPACE_LIMITS, WorkspaceStore } from '../osa-workspace.js';

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
 * How long any one phase (or a self-contained check group run inside it) may
 * take before it is treated as hung rather than left to leave no verdict at
 * all (#433). `runPage` in chrome.js has its own, larger, page-level
 * timeout; this is a SECOND, tighter bound inside the page itself, so a hang
 * fails with a named reason in the results this page already reports,
 * rather than only as chrome.js's generic "timed out waiting for the page".
 * Comfortably under chrome.js's own PAGE_TIMEOUT_MS.workspace (120s), which
 * also has to cover this page's real Pyodide boot before this deadline
 * even starts timing.
 */
const PHASE_TIMEOUT_MS = 90_000;

function withDeadline(promise, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`${label} did not finish within ${Math.round(PHASE_TIMEOUT_MS / 1000)}s`)),
      PHASE_TIMEOUT_MS
    );
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/**
 * A valid base64 string decoding to `byteLength` zero bytes, built directly
 * as text rather than by base64-encoding a real `Uint8Array`: "AAAA" is
 * exactly 3 zero bytes' worth, so repeating it is both cheap at multi-
 * megabyte sizes and exactly as valid as a real encoder's output for what
 * the budget and concurrency checks below need, which is SIZE and DISTINCTNESS, not particular
 * content.
 */
function base64OfZeros(byteLength) {
  if (byteLength % 3 !== 0) throw new Error('base64OfZeros needs a multiple of 3');
  return 'AAAA'.repeat(byteLength / 3);
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
const SESSION_ID_2 = 'harness-session-2';
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

  // A SECOND session in the same community. sizeUsed already aggregates
  // across every session (checked above); what is new here is that
  // manifests() reports BOTH sessions, each scoped to only its own run.
  const r3 = await controller.answer({
    call_id: 'call-3',
    tool: 'execute_code',
    session_id: SESSION_ID_2,
    args: { code: 'print("a second, unrelated session")', description: 'a run in a second session' },
    requires_permission: false,
  });
  check('a run in a second session succeeds', r3.status === 'ok', JSON.stringify(r3));

  const manifestsWithSecondSession = await workspace.manifests();
  check(
    'manifests() lists BOTH sessions',
    JSON.stringify(Object.keys(manifestsWithSecondSession).sort()) === JSON.stringify([SESSION_ID, SESSION_ID_2].sort()),
    JSON.stringify(Object.keys(manifestsWithSecondSession))
  );
  check(
    "the first session's manifest is unchanged by the second session existing",
    manifestsWithSecondSession[SESSION_ID]?.runs.length === 2,
    JSON.stringify(manifestsWithSecondSession[SESSION_ID])
  );
  check(
    "the second session's manifest has exactly its own one run",
    manifestsWithSecondSession[SESSION_ID_2]?.runs.length === 1 && manifestsWithSecondSession[SESSION_ID_2].runs[0].call_id === 'call-3',
    JSON.stringify(manifestsWithSecondSession[SESSION_ID_2])
  );
  const usedWithSecondSession = await workspace.sizeUsed();
  check(
    'sizeUsed grew after the second session wrote its own files',
    usedWithSecondSession > used,
    `before=${used} after=${usedWithSecondSession}`
  );

  await runProtocolWorkerChecks();
  await runDirectStoreChecks();

  // usedWithSecondSession, not the earlier `used`: the second session
  // writes into this SAME community, so the community's real total grew
  // after `used` was captured. The read phase's own sizeUsed() reflects
  // that growth for real, and the expectation this page leaves behind has
  // to agree with it, not with a snapshot from before that session ran.
  localStorage.setItem(
    EXPECTATION_KEY,
    JSON.stringify({ used: usedWithSecondSession, runCount: manifest ? manifest.runs.length : 0 })
  );
}

/**
 * A worker `files` entry with genuinely malformed base64 -- not
 * something real Python/JS encoding can ever produce, see the comment on
 * the BADFILE directive in test-workers/executing.js -- makes
 * WorkspaceStore.recordRun's own atob() call throw for real, reaching
 * ClientToolController#persist's catch. This needs a REAL browser and a
 * REAL IndexedDB (recordRun's `available` check returns early under Bun,
 * before ever reaching atob()), so it cannot be tested under Bun at all;
 * this harness is the only place it can run for real.
 *
 * The worker here deliberately speaks the execute protocol without booting
 * real Pyodide (test-workers/executing.js, the same real, non-mock stand-in
 * frontend/test-controller.js already uses under Bun for controller-level
 * tests): what is under test is WorkspaceStore and ClientToolController,
 * neither of which this worker's own logic replaces.
 */
async function runProtocolWorkerChecks() {
  const workspace = new WorkspaceStore({ community: 'workspace-harness-protocol' });
  const rt = new PyodideRuntime({
    runtime: { pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 30 } },
    workerFactory: () => new Worker(new URL('../test-workers/executing.js', import.meta.url)),
    bootTimeoutMs: 15_000,
  });
  await rt.boot();

  const controller = new ClientToolController({
    runtime: rt,
    tools: [{ name: 'execute_code', runtime: 'python', requires_permission: false }],
    gate: async () => GATE_DECISION.RUN,
    workspace,
  });

  // console.error is intercepted, not replaced: the real implementation
  // still runs (so a genuine failure is still visible in the page's own
  // console), this only also records what it was called with.
  const errorLog = [];
  const realConsoleError = console.error;
  console.error = (...args) => {
    errorLog.push(args.map(String).join(' '));
    realConsoleError.apply(console, args);
  };
  let result;
  try {
    result = await controller.answer({
      call_id: 'call-badfile',
      tool: 'execute_code',
      session_id: 'protocol-session-1',
      args: { code: 'BADFILE', description: 'a run whose reported file is malformed base64' },
      requires_permission: false,
    });
  } finally {
    console.error = realConsoleError;
  }

  check('a run with a malformed-base64 file still answers, status ok', result.status === 'ok', JSON.stringify(result));
  check('the malformed file is dropped from artifacts entirely', JSON.stringify(result.artifacts) === '[]', JSON.stringify(result.artifacts));
  check(
    "stderr carries the deterministic 'workspace failed unexpectedly' note",
    /\[workspace\] could not save this run's files: the workspace failed unexpectedly/.test(result.stderr),
    JSON.stringify(result.stderr)
  );
  check(
    'the genuine atob failure was logged, not silently swallowed',
    errorLog.some((line) => line.includes('[OSA] Workspace persist failed unexpectedly')),
    JSON.stringify(errorLog)
  );

  rt.terminate();
}

/**
 * WorkspaceStore called directly, bypassing Python and the
 * controller entirely, against a REAL IndexedDB.
 */
async function runDirectStoreChecks() {
  // The host-side per-run budget loop in recordRun. Three explicit
  // files that each fit the per-file cap but together cross the 25 MB
  // per-run budget, so the third must be refused by name while the first
  // two, and the run record itself, still succeed.
  {
    const workspace = new WorkspaceStore({ community: 'workspace-harness-budget' });
    const perFile = 9_000_000;
    check(
      'the fixture sizes actually straddle the per-run cap as intended',
      perFile * 2 < WORKSPACE_LIMITS.MAX_RUN_BYTES && perFile * 3 > WORKSPACE_LIMITS.MAX_RUN_BYTES,
      `perFile=${perFile} MAX_RUN_BYTES=${WORKSPACE_LIMITS.MAX_RUN_BYTES}`
    );
    const explicitFiles = [
      { path: 'artifacts/a.bin', data_base64: base64OfZeros(perFile) },
      { path: 'artifacts/b.bin', data_base64: base64OfZeros(perFile) },
      { path: 'artifacts/c.bin', data_base64: base64OfZeros(perFile) },
    ];
    const result = await workspace.recordRun({
      session: 'budget-session',
      callId: 'call-budget',
      status: 'ok',
      description: 'direct host-side run-budget check',
      code: 'print(1)',
      stdout: '',
      stderr: '',
      summary: '',
      images: [],
      explicitFiles,
    });
    const failedPaths = result.failures.map((f) => f.path);
    check('the run record was still written (only the third file failed)', result.ordinal !== null, JSON.stringify(result));
    check(
      'the first two files saved as artifacts',
      JSON.stringify(result.savedArtifacts) === JSON.stringify(['artifacts/a.bin', 'artifacts/b.bin']),
      JSON.stringify(result.savedArtifacts)
    );
    check('exactly the third file is named as a failure', JSON.stringify(failedPaths) === JSON.stringify(['artifacts/c.bin']), JSON.stringify(failedPaths));
    check(
      "the failure names the per-run limit as why",
      /per-run limit/.test((result.failures[0] || {}).reason || ''),
      JSON.stringify(result.failures)
    );

    const manifests = await workspace.manifests();
    const run = manifests['budget-session']?.runs.find((r) => r.ordinal === result.ordinal);
    check('the manifest omits the failed file', !!run && !run.files.includes('artifacts/c.bin'), JSON.stringify(run));
    check(
      'but keeps the two files that actually saved',
      !!run && run.files.includes('artifacts/a.bin') && run.files.includes('artifacts/b.bin'),
      JSON.stringify(run)
    );
  }

  // The host-side re-check of the 32-distinct-explicit-file cap, the other
  // half of "saved equals announced" -- Python enforces this too
  // (frontend/test-worker-core.js), but recordRun re-checks it independently
  // for a caller that reaches the store some other way. Like the run-budget check, this needs
  // real IndexedDB: `available` is false under Bun, so recordRun returns
  // before ever reaching this loop there.
  {
    const workspace = new WorkspaceStore({ community: 'workspace-harness-filecount' });
    const explicitFiles = Array.from({ length: 33 }, (_, i) => ({
      path: `artifacts/f${i}.txt`,
      data_base64: base64OfZeros(3),
    }));
    const result = await workspace.recordRun({
      session: 'filecount-session',
      callId: 'call-filecount',
      status: 'ok',
      description: 'direct 32-distinct-file check',
      code: 'print(1)',
      stdout: '',
      stderr: '',
      summary: '',
      images: [],
      explicitFiles,
    });
    check('exactly 32 of the 33 distinct files saved', result.savedArtifacts.length === 32, `saved=${result.savedArtifacts.length}`);
    check(
      'the 33rd is refused by name',
      result.failures.some((f) => f.path === 'artifacts/f32.txt'),
      JSON.stringify(result.failures)
    );
    check(
      'the refusal names the 32-file cap, not the byte budget',
      result.failures.some((f) => f.path === 'artifacts/f32.txt' && /32 files/.test(f.reason)),
      JSON.stringify(result.failures)
    );
  }

  // Two WorkspaceStore instances -- two separate IndexedDB connections
  // to the same database -- recording concurrently for the SAME session
  // must never collide on an ordinal. This is _reserveOrdinal's own claim:
  // IndexedDB serializes readwrite transactions with overlapping scope
  // across connections, which is what this proves against a real database
  // rather than against the claim alone.
  {
    const community = 'workspace-harness-concurrent';
    const session = 'concurrent-session';
    const storeA = new WorkspaceStore({ community });
    const storeB = new WorkspaceStore({ community });
    const [resultA, resultB] = await Promise.all([
      storeA.recordRun({
        session, callId: 'call-a', status: 'ok', description: 'A', code: 'print("a")',
        stdout: '', stderr: '', summary: '', images: [], explicitFiles: [],
      }),
      storeB.recordRun({
        session, callId: 'call-b', status: 'ok', description: 'B', code: 'print("b")',
        stdout: '', stderr: '', summary: '', images: [], explicitFiles: [],
      }),
    ]);
    check(
      'both concurrent recordRun calls were assigned an ordinal',
      resultA.ordinal !== null && resultB.ordinal !== null,
      JSON.stringify([resultA.ordinal, resultB.ordinal])
    );
    check(
      'the two ordinals are exactly {1, 2}, never the same value',
      JSON.stringify([resultA.ordinal, resultB.ordinal].sort((x, y) => x - y)) === JSON.stringify([1, 2]),
      JSON.stringify([resultA.ordinal, resultB.ordinal])
    );

    const manifests = await storeA.manifests();
    const runs = manifests[session] ? manifests[session].runs : [];
    check('both runs survive in the manifest, neither overwritten', runs.length === 2, JSON.stringify(runs));
    check(
      "neither run's call_id was overwritten by the other's",
      new Set(runs.map((r) => r.call_id)).size === 2,
      JSON.stringify(runs.map((r) => r.call_id))
    );
  }
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

  // Ordinals continue correctly across a reload. Recorded directly
  // against this FRESH store -- an ordinal is a WorkspaceStore/IndexedDB
  // guarantee, not something Python or the controller need be involved in
  // to prove -- and must land at run-003, leaving run-001's own files
  // completely untouched.
  const run1BeforeThird = manifest?.runs.find((r) => r.ordinal === 1);
  const thirdRun = await workspace.recordRun({
    session: SESSION_ID,
    callId: 'call-3-after-reload',
    status: 'ok',
    description: 'recorded directly against a fresh page load',
    code: 'print(3)',
    stdout: '3\n',
    stderr: '',
    summary: '',
    images: [],
    explicitFiles: [],
  });
  check('a run recorded after a fresh page load continues the ordinal sequence (run-003)', thirdRun.ordinal === 3, JSON.stringify(thirdRun));

  const manifestsAfterThird = await workspace.manifests();
  const manifestAfterThird = manifestsAfterThird[SESSION_ID];
  const run1AfterThird = manifestAfterThird?.runs.find((r) => r.ordinal === 1);
  check('run-001 still exists once a third run has been added', !!run1AfterThird, JSON.stringify(manifestAfterThird));
  check(
    "run-001's own files are byte-for-byte the same list as before the third run",
    !!run1AfterThird && !!run1BeforeThird &&
      JSON.stringify([...run1AfterThird.files].sort()) === JSON.stringify([...run1BeforeThird.files].sort()),
    JSON.stringify({ before: run1BeforeThird, after: run1AfterThird })
  );
  check('the session now has exactly 3 runs', manifestAfterThird?.runs.length === 3, JSON.stringify(manifestAfterThird));

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
      await withDeadline(runWritePhase(rt), 'the write phase');
    } else {
      await withDeadline(runReadPhase(), 'the read phase');
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
