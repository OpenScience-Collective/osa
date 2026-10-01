/**
 * Drives the real `launch` from frontend/browser-harness/chrome.js against a stand-in
 * executable, and prints what it did as one JSON line. Run by
 * tests/test_frontend/test_chrome_launch.py, which writes the stand-in.
 *
 * Usage: bun chrome_launch_fixture.js <executable> <profile dir> <wait ms>...
 *
 * The waits replace the harness's own (30 s, then 90 s) so a test does not spend them.
 */

import { launch } from '../../frontend/browser-harness/chrome.js';

const [executable, profileDir, ...waits] = process.argv.slice(2);

try {
  const { chrome, wsUrl } = await launch(executable, profileDir, waits.map(Number));
  console.log(JSON.stringify({ ok: true, wsUrl }));
  chrome.kill();
} catch (error) {
  console.log(JSON.stringify({ ok: false, message: error.message }));
}
process.exit(0);
