// Reports a runtime error through the protocol.
self.onmessage = (event) => {
  if ((event.data || {}).type !== 'boot') return;
  self.postMessage({ type: 'error', kind: 'runtime', message: 'no WebAssembly.instantiate' });
};
