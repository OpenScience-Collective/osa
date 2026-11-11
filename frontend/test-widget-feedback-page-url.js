/**
 * General feedback carries the page's address only when the reader shares it.
 *
 * The footer's page-context choice ("Share page URL to help answer questions") decides
 * whether a question carries the page's address; the "Send feedback" form used to send
 * `page_url` regardless. This runs the real widget source in happy-dom windows, with the
 * choice saved on and off, sends general feedback through the form, and reads the body
 * the widget posted to `/feedback`.
 *
 * What stands in: `fetch` (the community config, `/health`, and a `/feedback` that
 * answers 200).
 *
 * Run with: bun frontend/test-widget-feedback-page-url.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

let passed = 0;
let failed = 0;

const watchdog = setTimeout(() => {
  console.error('\n  x FAIL: the suite did not finish within 20s.');
  process.exit(1);
}, 20_000);
watchdog.unref?.();

function assert(cond, msg) {
  if (cond) {
    console.log(`  ok ${msg}`);
    passed++;
  } else {
    console.error(`  x FAIL: ${msg}`);
    failed++;
  }
}

async function waitUntil(predicate, label, timeoutMs = 5000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) throw new Error(`waitUntil timed out: ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');
const PAGE = 'http://localhost/dataset/xx099904?tab=files';

function start({ shared, allowPageContext = true }) {
  const window = new Window({
    url: PAGE,
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  if (shared !== undefined) window.localStorage.setItem('osa-page-context-enabled', String(shared));
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const posted = [];
  const fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.endsWith('/feedback')) {
      posted.push(JSON.parse(init.body));
      return new Response('{}', { headers: { 'content-type': 'application/json' } });
    }
    if (u.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    return new Response(JSON.stringify({
      default_model: 'm', offered_models: [], widget: { title: 'Feedback Tests' }, client_tools: [], runtime: null,
    }), { headers: { 'content-type': 'application/json' } });
  };
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE,
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  api.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'nemar', storageKey: 'osa-test-feedback', allowPageContext });
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  return { container, posted };
}

async function sendGeneralFeedback(ctx, text) {
  ctx.container.querySelector('.osa-feedback-link').click();
  ctx.container.querySelector('#osa-feedback-text').value = text;
  ctx.container.querySelector('.osa-feedback-send-btn').click();
  await waitUntil(() => ctx.posted.length > 0, 'the feedback is posted');
  return ctx.posted[0];
}

console.log('\nGeneral feedback and the page-context choice');

{
  const body = await sendGeneralFeedback(start({ shared: true }), 'Shared page');
  assert(body.feedback_type === 'general' && body.comment === 'Shared page', 'the form posts general feedback with the comment');
  assert(body.page_url === PAGE, 'with the page shared, the page address goes with the feedback');
}
{
  const body = await sendGeneralFeedback(start({ shared: false }), 'Private page');
  assert(body.comment === 'Private page', 'feedback is still sent with the page kept private');
  assert(body.page_url === null, 'with the page kept private, no page address goes with the feedback');
}
{
  const body = await sendGeneralFeedback(start({}), 'Default');
  assert(body.page_url === PAGE, 'with no saved choice, the default (shared) applies');
}
{
  const body = await sendGeneralFeedback(start({ allowPageContext: false, shared: true }), 'No toggle');
  assert(body.page_url === null, 'a page that turns page context off sends no page address, whatever was saved');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
