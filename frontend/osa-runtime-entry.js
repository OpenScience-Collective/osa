// Entry point for osa-runtime.bundle.js, built by scripts/build-runtime-bundle.js.
//
// The widget is a classic script pinned by SRI, so it cannot import these
// modules; it loads this bundle instead, only for a community that configures
// client tools, and only after the browser has checked it against the hash the
// widget carries. Everything the widget needs is reached through one global.
import { ClientToolController, GATE_DECISION } from './osa-controller.js';
import { highlightPython } from './osa-highlight.js';
import { buildWorkerConfig, buildWorkerSource, PyodideRuntime, RUNTIME_STATE } from './osa-runtime.js';
import { createWorkerRuntime } from './osa-worker-core.js';

globalThis.OSARuntime = Object.freeze({
  PyodideRuntime,
  RUNTIME_STATE,
  ClientToolController,
  GATE_DECISION,
  highlightPython,
  buildWorkerSource,
  buildWorkerConfig,
  // Exposed so the bundle's own test can run the core AS BUILT: minification is
  // exactly what could break the core's self-containment, and the source copy
  // cannot show that.
  createWorkerRuntime,
});
