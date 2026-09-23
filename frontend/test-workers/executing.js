// A real worker that speaks the boot and execute protocol, used to test the
// HOST's half: correlation by call_id, teardown of in-flight work, and
// boot-on-demand. It is not a Python runtime and does not pretend to be one.
//
// The Python side of execution (the import gate, the seal ordering, micropip)
// is not testable here at all, because Pyodide needs a browser. That half is
// verified against real Pyodide in a browser harness and recorded on #431;
// asserting it against a stand-in would be asserting the stand-in.
//
// Replies are deliberately delivered OUT OF ORDER: a directive of
// `DELAY:<ms>` in the code makes a call answer late, so a host that assumed
// results arrive in the order they were sent, or that tracked only one
// outstanding call, mismatches them.
self.onmessage = (event) => {
  const data = event.data || {};

  if (data.type === 'boot') {
    self.postMessage({ type: 'ready', version: '0.29.5' });
    return;
  }

  if (data.type === 'execute') {
    const code = String(data.code || '');

    if (code.startsWith('NEVER')) {
      return; // Answers nothing, which is what a hung execution looks like.
    }

    if (code.startsWith('CRASH')) {
      // Dies without answering, which is what a wasm memory abort looks like:
      // the instance goes down and there is no exception for anyone to catch.
      setTimeout(() => {
        throw new Error('simulated worker abort');
      }, 0);
      return;
    }

    if (code.startsWith('OOM')) {
      self.postMessage({
        type: 'result',
        call_id: data.call_id,
        status: 'oom',
        stdout: '',
        stderr: 'out of memory',
        summary: '',
        images: [],
        artifacts: [],
        elapsed_ms: 1,
      });
      return;
    }

    const delayMatch = code.match(/^DELAY:(\d+)/);
    const delay = delayMatch ? Number(delayMatch[1]) : 0;

    // LONG:<n> produces n characters of full stdout, of which only a bounded
    // copy is returned, which is the shape the real harness sends. IMAGE adds
    // one image, so the store's handling of figures is exercised.
    const longMatch = code.match(/^LONG:(\d+)/);
    const fullStdout = longMatch ? 'x'.repeat(Number(longMatch[1])) : `stdout of ${code}`;
    // ERR:<n> puts n characters in the full stderr and a traceback beside it, so
    // get_full_output's handling of those streams can be exercised.
    const errMatch = code.match(/^ERR:(\d+)/);
    const fullStderr = errMatch ? 'e'.repeat(Number(errMatch[1])) : '';
    const fullTraceback = errMatch ? 'Traceback (most recent call last):\n  File "<cell>", line 1\nValueError: boom' : '';
    const images = code.startsWith('IMAGE')
      ? [{ mime: 'image/png', data_base64: 'iVBORw0KGgo=', width: 1, height: 1 }]
      : [];

    // FILES:<n> saves n explicitly-named artifacts, the way a real run's
    // osa.save_artifact would report them: `files` (base64 bytes, host-only)
    // beside `artifacts` (the names, which DO reach ClientToolResult). Used
    // by controller- and runtime-level tests to exercise workspace
    // persistence without booting real Pyodide.
    //
    // BADFILE reports one artifact whose base64 is genuinely malformed --
    // real Python/JS base64 encoding (osa-egress.js's save_artifact through
    // the worker's own reply path) can never produce this, so it stands in
    // for a bug elsewhere that reaches recordRun, the one case the
    // unexpected-failure handling exists for: WorkspaceStore.recordRun calling atob() on it must
    // genuinely throw (a real DOMException, in a real browser with real
    // IndexedDB), reaching ClientToolController#persist's catch for real.
    const filesMatch = code.match(/^FILES:(\d+)/);
    const fileCount = filesMatch ? Number(filesMatch[1]) : 0;
    const artifacts = [];
    const files = [];
    for (let i = 0; i < fileCount; i++) {
      const path = `artifacts/file-${i}.txt`;
      artifacts.push(path);
      files.push({ path, data_base64: btoa(`contents of file ${i}`) });
    }
    if (code.startsWith('BADFILE')) {
      artifacts.push('artifacts/bad.bin');
      files.push({ path: 'artifacts/bad.bin', data_base64: 'not-valid-base64!!!' });
    }

    const reply = () => {
      self.postMessage({
        type: 'result',
        call_id: data.call_id,
        status: 'ok',
        stdout: fullStdout.slice(0, 64),
        stderr: '',
        // Echoed so a test can prove THIS result belongs to THIS call.
        summary: code,
        images,
        artifacts,
        elapsed_ms: delay,
        full: { stdout: fullStdout, stderr: fullStderr, traceback: fullTraceback, summary: code },
        files,
      });
    };

    if (delay > 0) setTimeout(reply, delay);
    else reply();
    return;
  }

  self.postMessage({ type: 'error', kind: 'protocol', message: 'unexpected: ' + data.type });
};
