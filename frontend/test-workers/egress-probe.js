// Evaluates the real generated guard in a REAL worker and probes it.
// String assertions about the guard source prove nothing about whether a
// request is actually refused; this runs the thing.
self.onmessage = async (event) => {
  const { guardSource, sealTo, probes } = event.data;
  const results = {};
  try {
    // eslint-disable-next-line no-eval
    (0, eval)(guardSource);
  } catch (err) {
    self.postMessage({ fatal: 'guard failed to evaluate: ' + err.message });
    return;
  }

  // Seal, exactly as boot does before user code runs.
  if (sealTo) {
    try {
      // eslint-disable-next-line no-eval
      (0, eval)('__seal(' + JSON.stringify(sealTo) + ')');
    } catch (err) {
      self.postMessage({ fatal: 'seal failed: ' + err.message });
      return;
    }
  }

  for (const [name, url] of Object.entries(probes)) {
    try {
      // Never actually completes a network call in CI: an allowed URL is only
      // required to get PAST the guard, and any network failure after that is
      // reported distinctly from a refusal.
      await self.fetch(url);
      results[name] = 'reached_network';
    } catch (err) {
      results[name] = err && err.name === 'EgressDenied'
        ? 'DENIED:' + err.reason
        : 'network_error_after_guard';
    }
  }

  // Transports that must be refused outright.
  try {
    new self.WebSocket('wss://zarr.nemar.org/x');
    results.websocket = 'CONSTRUCTED';
  } catch (err) {
    results.websocket = err && err.name === 'EgressDenied' ? 'DENIED:' + err.reason : 'other_error';
  }
  try {
    new self.EventSource('https://zarr.nemar.org/x');
    results.eventsource = 'CONSTRUCTED';
  } catch (err) {
    results.eventsource = err && err.name === 'EgressDenied' ? 'DENIED:' + err.reason : 'other_error';
  }

  self.postMessage({ results });
};
