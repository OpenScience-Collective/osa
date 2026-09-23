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
        artifacts: [],
        elapsed_ms: delay,
        full: { stdout: fullStdout, stderr: fullStderr, traceback: fullTraceback },
      });
    };

    if (delay > 0) setTimeout(reply, delay);
    else reply();
    return;
  }

  self.postMessage({ type: 'error', kind: 'protocol', message: 'unexpected: ' + data.type });
};
