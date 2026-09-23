// Boots slowly, then executes the way `executing.js` does in its plainest
// case. The delay is what makes "Stop pressed during a cold boot" testable:
// with an instant boot there is no window in which a call is waiting on it.
let executions = 0;

self.onmessage = (event) => {
  const data = event.data || {};
  if (data.type === 'boot') {
    setTimeout(() => self.postMessage({ type: 'ready', version: '0.29.5' }), 300);
    return;
  }
  if (data.type === 'execute') {
    // Counted, so a test can prove which code reached this worker and which
    // never did: a result nobody waits on is dropped, so absence is otherwise
    // invisible.
    executions += 1;
    self.postMessage({
      type: 'result',
      call_id: data.call_id,
      status: 'ok',
      stdout: '',
      stderr: '',
      summary: `run ${executions}: ${data.code}`,
      images: [],
      artifacts: [],
      elapsed_ms: 1,
    });
  }
};
