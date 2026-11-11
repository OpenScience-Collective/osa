/**
 * An in-memory stand-in for the part of IndexedDB that WorkspaceStore uses, so a Bun
 * test can run the store's own code (recordRun, figuresForCalls) end to end: Bun has
 * no IndexedDB, and this is the only thing the tests that use it stand in for. The
 * real IndexedDB, across a reload, is the Chrome harness's
 * (frontend/browser-harness/workspace.js and history-check.mjs).
 *
 * What it keeps: named databases of named object stores of rows keyed by `row.key`
 * (the store's keyPath), copied in and out as IndexedDB's structured clone does. What
 * it answers: open (with an upgrade), transaction, objectStore, get, getAll over a key
 * range, put and delete, each asynchronously, and a transaction's `oncomplete` once
 * its requests have all answered. It installs `IDBKeyRange.bound` on the global, as
 * WorkspaceStore reads it from there.
 *
 * Usage: `new WorkspaceStore({ community, dbFactory: memoryIndexedDB() })`.
 */

export function memoryIndexedDB() {
  globalThis.IDBKeyRange = { bound: (lower, upper) => ({ lower, upper }) };
  const databases = new Map();

  function transaction(db, names) {
    const allowed = Array.isArray(names) ? names : [names];
    const tx = { pending: 0, finished: false, oncomplete: null, onerror: null, onabort: null };
    const request = (compute) => {
      const req = {};
      tx.pending += 1;
      setTimeout(() => {
        req.result = compute();
        req.onsuccess?.();
        tx.pending -= 1;
        // Done once nothing more was asked of it by the answer's own handlers.
        if (tx.pending === 0) {
          setTimeout(() => {
            if (tx.pending === 0 && !tx.finished) {
              tx.finished = true;
              tx.oncomplete?.();
            }
          }, 0);
        }
      }, 0);
      return req;
    };
    tx.objectStore = (name) => {
      if (!allowed.includes(name)) throw new Error(`the transaction does not cover ${name}`);
      const rows = db.stores.get(name);
      return {
        get: (key) => request(() => (rows.has(key) ? structuredClone(rows.get(key)) : undefined)),
        getAll: (range) => request(() => [...rows.entries()]
          .filter(([key]) => key >= range.lower && key <= range.upper)
          .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
          .map(([, row]) => structuredClone(row))),
        put: (row) => request(() => {
          rows.set(row.key, structuredClone(row));
          return row.key;
        }),
        delete: (range) => request(() => {
          for (const key of [...rows.keys()]) {
            if (key >= range.lower && key <= range.upper) rows.delete(key);
          }
        }),
      };
    };
    return tx;
  }

  return {
    open(name) {
      const request = {};
      setTimeout(() => {
        let db = databases.get(name);
        const fresh = !db;
        if (fresh) {
          db = { stores: new Map() };
          databases.set(name, db);
        }
        request.result = {
          objectStoreNames: { contains: (store) => db.stores.has(store) },
          createObjectStore: (store) => db.stores.set(store, new Map()),
          transaction: (names) => transaction(db, names),
        };
        if (fresh) request.onupgradeneeded?.();
        request.onsuccess?.();
      }, 0);
      return request;
    },
  };
}
