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
    self.postMessage({ type: 'ready', version: '0.28.3' });
    return;
  }

  if (data.type === 'execute') {
    const code = String(data.code || '');

    if (code.startsWith('NEVER')) {
      return; // Answers nothing, which is what a hung execution looks like.
    }

    const delayMatch = code.match(/^DELAY:(\d+)/);
    const delay = delayMatch ? Number(delayMatch[1]) : 0;

    const reply = () => {
      self.postMessage({
        type: 'result',
        call_id: data.call_id,
        status: 'ok',
        stdout: '',
        stderr: '',
        // Echoed so a test can prove THIS result belongs to THIS call.
        summary: code,
        images: [],
        artifacts: [],
        elapsed_ms: delay,
      });
    };

    if (delay > 0) setTimeout(reply, delay);
    else reply();
    return;
  }

  self.postMessage({ type: 'error', kind: 'protocol', message: 'unexpected: ' + data.type });
};
