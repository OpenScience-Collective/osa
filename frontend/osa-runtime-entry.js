// Entry point for osa-runtime.bundle.js, built by scripts/build-runtime-bundle.js.
//
// The widget is a classic script pinned by SRI, so it cannot import these
// modules; it loads this bundle instead, only for a community that configures
// client tools, and only after the browser has checked it against the hash the
// widget carries. Everything the widget needs is reached through one global.
import { ClientToolController, FULL_OUTPUT_TOOL_NAME, GATE_DECISION } from './osa-controller.js';
import { highlightPython } from './osa-highlight.js';
import { buildConversationNotebook } from './osa-notebook-builder.js';
import * as plan from './osa-plan.js';
import { buildWorkerConfig, buildWorkerSource, PyodideRuntime, RUNTIME_STATE } from './osa-runtime.js';
import { createWorkerRuntime } from './osa-worker-core.js';
import {
  buildNotebook,
  buildWorkspaceZip,
  deriveManifest,
  validateWorkspacePath,
  WORKSPACE_LIMITS,
  WorkspaceStore,
} from './osa-workspace.js';

globalThis.OSARuntime = Object.freeze({
  PyodideRuntime,
  RUNTIME_STATE,
  ClientToolController,
  FULL_OUTPUT_TOOL_NAME,
  GATE_DECISION,
  highlightPython,
  buildWorkerSource,
  buildWorkerConfig,
  // Exposed so the bundle's own test can run the core AS BUILT: minification is
  // exactly what could break the core's self-containment, and the source copy
  // cannot show that.
  createWorkerRuntime,
  // The workspace (#433): persistence is WorkspaceStore, and the rest are the
  // pure functions the widget's Settings panel and export button also need
  // directly (path validation for its own defense-in-depth, size constants
  // for the panel's usage display).
  WorkspaceStore,
  deriveManifest,
  buildNotebook,
  buildWorkspaceZip,
  validateWorkspacePath,
  WORKSPACE_LIMITS,
  // A chat's successful runs as a notebook and a percent-format script (E4.1, #667).
  buildConversationNotebook,
  // The plan card's and the step progress's pure logic (E2.3, E2.4): the widget declares
  // `propose_plan` only when this is here, since only then can it answer one.
  plan: Object.freeze({ ...plan }),
});
