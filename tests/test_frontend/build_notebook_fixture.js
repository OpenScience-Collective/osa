#!/usr/bin/env bun
// Build the notebook and script for every recorded conversation with the REAL builder, for
// tests/test_frontend/test_notebook_builder.py to validate with the real nbformat and jupytext.
//
// It calls the same buildConversationNotebook that frontend/osa-runtime-entry.js exposes to the
// widget, over the same conversations frontend/test-notebook-builder.js uses, and writes the
// results as one JSON file: {name: {notebook, script, stats}}.
//
// Usage: bun build_notebook_fixture.js <output-json-path>

import { writeFileSync } from 'node:fs';
import { buildConversationNotebook } from '../../frontend/osa-notebook-builder.js';
import { NOTEBOOK_CONVERSATIONS } from '../../frontend/test-support/notebook-conversations.js';

const outPath = process.argv[2];
if (!outPath) {
  console.error('usage: bun build_notebook_fixture.js <output-json-path>');
  process.exit(1);
}

const out = {};
for (const [name, make] of Object.entries(NOTEBOOK_CONVERSATIONS)) {
  out[name] = buildConversationNotebook(make());
}
writeFileSync(outPath, JSON.stringify(out));
