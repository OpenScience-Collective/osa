// A real worker speaking the real boot protocol, without downloading Pyodide.
// Not a stand-in for the runtime's logic: every decision under test (idempotence,
// the deadline, state transitions, teardown) runs for real against this.
self.onmessage = (event) => {
  if ((event.data || {}).type !== 'boot') return;
  self.postMessage({ type: 'progress', phase: 'loading_runtime' });
  self.postMessage({ type: 'progress', phase: 'loading_package', package: 'numpy', index: 0, total: 1 });
  self.postMessage({ type: 'ready', version: '0.29.5' });
};
