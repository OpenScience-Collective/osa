/**
 * Classify tool names with the widget's own activity-label code (#538).
 *
 * Reads the block between `BEGIN activity-labels` and `END activity-labels` out of
 * frontend/osa-chat-widget.js and evaluates it on its own: that block is pure by
 * contract (the widget's comment says so, and this is what holds it to it), so
 * nothing of the page is needed, and no DOM library either, which the Python job
 * that runs this does not install.
 *
 * Input on stdin: a JSON list of {name, community}. Output on stdout: the same list,
 * each entry with the `writing` and `running` classifications added.
 *
 * Run by tests/test_frontend/test_widget_activity_labels.py.
 */

import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../frontend/osa-chat-widget.js', import.meta.url), 'utf8');
const begin = source.indexOf('// BEGIN activity-labels');
const end = source.indexOf('// END activity-labels');
if (begin === -1 || end === -1 || end < begin) {
  console.error('the activity-labels block is missing from frontend/osa-chat-widget.js');
  process.exit(2);
}
// eslint-disable-next-line no-new-func
const classify = new Function(`${source.slice(begin, end)}\nreturn classifyToolActivity;`)();

const entries = JSON.parse(readFileSync(0, 'utf8'));
const out = entries.map(({ name, community }) => ({
  name,
  community,
  writing: classify(name, 'writing', community),
  running: classify(name, 'running', community),
}));
process.stdout.write(JSON.stringify(out));
