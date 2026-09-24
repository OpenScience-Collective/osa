/**
 * The widget's pop-out window (#470), run against the real widget source in
 * happy-dom windows: the host page's own, and the pop-out that page opens.
 *
 * The widget is loaded the way a host page loads it, by a script element with its
 * address (and, as nemar.org pins it, integrity and crossorigin), from a virtual
 * server that reads frontend/ from disk. Clicking the pop-out button opens a real
 * happy-dom window, and the script element the widget adds to it is fetched and run
 * by happy-dom itself, in that window. So what is under test is the pop-out as a
 * browser would build it: a document with no script of its own, presets set on its
 * window by the opener, and the widget's own file arriving by address.
 *
 * What it holds the widget to: the pop-out's document carries no inline script and
 * no preset as text; the presets arrive as window properties, as the pop-out's own
 * data; the script element has the widget's address and the host tag's integrity
 * and crossorigin, and nothing else of the tag's; the widget source is never
 * fetched by the opener; a script that does not load says so in the pop-out; later
 * setColorScheme and setDataset calls reach an open pop-out, and one still loading.
 *
 * What stands in: the community config and health endpoints, answered with HTTP
 * fixtures through happy-dom's fetch interceptor (never a mock of the widget's own
 * logic), and window.alert, recorded. happy-dom does not check Subresource
 * Integrity (SRI) or a Content Security Policy (CSP); that the browser enforces
 * both in the pop-out is frontend/browser-harness/popout-check.mjs's, in Chrome.
 *
 * Run with: bun frontend/test-widget-popout.js
 */

import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

let passed = 0;
let failed = 0;

const SUITE_TIMEOUT_MS = 60_000;
const watchdog = setTimeout(() => {
  console.error(`\n  x FAIL: the suite did not finish within ${SUITE_TIMEOUT_MS / 1000}s.`);
  process.exit(1);
}, SUITE_TIMEOUT_MS);
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

// JSON.stringify comparison: never use this on DOM elements, which all serialize
// to {}; compare those with ===.
function assertEqual(actual, expected, msg) {
  const same = JSON.stringify(actual) === JSON.stringify(expected);
  assert(same, `${msg}${same ? '' : ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`}`);
}

async function waitUntil(predicate, label, timeoutMs = 5000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) {
      throw new Error(`waitUntil timed out after ${timeoutMs}ms: ${label}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

const FRONTEND = new URL('.', import.meta.url).pathname;
const WIDGET_URL = 'http://localhost/static/osa-chat-widget.js';
// A real pin of the file on disk, as a versioned embed carries one.
const INTEGRITY = `sha384-${createHash('sha384').update(readFileSync(new URL('./osa-chat-widget.js', import.meta.url))).digest('base64')}`;
const NOTEBOOK_URL = 'https://develop-notebook.osc.earth/osa/';

// A community config response fixture, shaped like fetchCommunityConfig expects.
// `placeholder` changes with every response, so a test can wait for this config to
// have been applied.
let configCounter = 0;
function configResponse(widgetOverrides = {}) {
  configCounter += 1;
  return {
    default_model: 'm',
    offered_models: [],
    widget: {
      title: 'NEMAR Assistant',
      placeholder: `loaded ${configCounter}`,
      launcher: 'capsule',
      suggested_questions: ['How do I cite NEMAR?'],
      dataset_suggested_questions: [{ text: 'What is {dataset_id} about?' }],
      ...widgetOverrides,
    },
    client_tools: [],
    runtime: null,
  };
}

/**
 * A host page with the widget on it, loaded by its script tag and initialized as
 * nemar.org does: setConfig, then setDataset and setColorScheme, then init.
 *
 * `tag` is the widget tag's attributes beyond src and data-no-auto-init; `config`
 * is what the community endpoint answers, for the page and for its pop-out.
 */
async function hostPage({ config = configResponse(), tag = { integrity: INTEGRITY, crossorigin: 'anonymous' }, dataset, colorScheme, setConfig = {} } = {}) {
  const requests = [];
  const window = new Window({
    url: 'http://localhost/dataset/nm000103',
    settings: {
      enableJavaScriptEvaluation: true,
      suppressInsecureJavaScriptEnvironmentWarning: true,
      disableCSSFileLoading: true,
      // The notebook tab's frame is created with a real src; a unit test checks the
      // address, and never loads it.
      navigation: { disableChildFrameNavigation: true },
      fetch: {
        virtualServers: [{ url: 'http://localhost/static/', directory: FRONTEND }],
        interceptor: {
          async beforeAsyncRequest({ request, window: from }) {
            requests.push({ url: request.url, from });
            if (request.url.endsWith('/api/health')) return new from.Response(JSON.stringify({ status: 'healthy' }));
            if (request.url.startsWith('http://localhost/api/')) {
              return new from.Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
            }
            return undefined;
          },
          // happy-dom fetches a script synchronously: an address with nothing there.
          beforeSyncRequest({ request, window: from }) {
            if (!request.url.startsWith('http://localhost/missing/')) return undefined;
            return { status: 404, statusText: 'Not Found', ok: false, url: request.url, redirected: false, headers: new from.Headers(), body: Buffer.from('not found') };
          },
        },
      },
    },
  });
  const alerts = [];
  window.alert = (message) => alerts.push(message);

  const script = window.document.createElement('script');
  for (const [name, value] of Object.entries(tag)) script.setAttribute(name, value);
  script.setAttribute('data-no-auto-init', '');
  const loaded = new Promise((resolve, reject) => {
    script.addEventListener('load', resolve);
    script.addEventListener('error', () => reject(new Error('the host page could not load the widget')));
  });
  script.src = WIDGET_URL;
  window.document.head.appendChild(script);
  await loaded;

  // What the page itself fetches, recorded on the way to happy-dom's own fetch.
  const pageFetches = [];
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    pageFetches.push(String(input?.url ?? input));
    return realFetch(input, init);
  };

  const api = window.OSAChatWidget;
  api.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'nemar', storageKey: `osa-test-popout-${configCounter}`, notebookUrl: NOTEBOOK_URL, ...setConfig });
  if (dataset !== undefined) api.setDataset(dataset);
  if (colorScheme) api.setColorScheme(colorScheme);
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder, 'the page\'s community config has loaded');

  // Every window the widget opens, handed back as the real one.
  const opened = [];
  const realOpen = window.open.bind(window);
  window.open = (...args) => {
    const popup = realOpen(...args);
    opened.push({ args, popup });
    return popup;
  };
  const q = (selector) => container.querySelector(selector);
  const click = (selector) => q(selector).dispatchEvent(new window.Event('click', { bubbles: true }));
  return { window, api, container, q, click, script, opened, alerts, requests, pageFetches, config };
}

// The pop-out's widget, once it is on screen with its own community config applied.
async function popoutReady(page, popup) {
  await waitUntil(() => {
    const input = popup.document.querySelector('.osa-chat-widget .osa-chat-input input');
    return !!input && input.placeholder === page.config.widget.placeholder;
  }, 'the pop-out\'s widget has loaded its community config');
  const container = popup.document.querySelector('.osa-chat-widget');
  return { container, q: (selector) => container.querySelector(selector) };
}

// Open the pop-out from the page's header button, and wait for its widget.
async function openPopout(page) {
  const before = page.opened.length;
  page.click('.osa-popout-btn');
  assertEqual(page.opened.length, before + 1, 'the pop-out button opened one window');
  const { popup } = page.opened.at(-1);
  return { popup, ...(await popoutReady(page, popup)) };
}

// The questions on screen, or null when none are.
const suggestions = (q) => (q('.osa-suggestions').style.display === 'none'
  ? null
  : [...q('.osa-suggestions-list').querySelectorAll('.osa-suggestion')].map((b) => b.textContent));
const isDark = (container) => container.classList.contains('osa-dark');

console.log('='.repeat(60));
console.log('Widget: the pop-out window (#470)');
console.log('='.repeat(60));

console.log('\nthe pop-out\'s document runs no inline script, and holds no preset as text');
{
  const page = await hostPage({ dataset: { id: 'nm000103', zarr: true } });
  page.click('.osa-chat-button');
  const { popup, container } = await openPopout(page);
  assert(container.classList.contains('fullscreen'), 'the pop-out\'s widget is on screen, fullscreen');
  assert(container.querySelector('.osa-chat-window').classList.contains('open'), 'with its panel open');
  assertEqual(popup.document.title, 'NEMAR Assistant', 'the window is titled with the widget\'s title');
  const scripts = [...popup.document.querySelectorAll('script')];
  assertEqual(scripts.filter((s) => !s.hasAttribute('src')).length, 0, 'no script element without a src');
  assertEqual(scripts.length, 1, 'one script element, the widget\'s');
  const handlers = [...popup.document.querySelectorAll('*')].flatMap((el) => [...el.attributes].map((a) => a.name)).filter((name) => name.startsWith('on'));
  assertEqual(handlers, [], 'no inline event handler attribute anywhere in the document');
  assert(!popup.document.documentElement.outerHTML.includes('__OSA_'), 'no preset written into the document as text');
  assert(!page.pageFetches.includes(WIDGET_URL), 'the page never fetched the widget source to write it inline');
}

console.log('\nthe presets arrive as properties of the pop-out\'s window, as its own data');
{
  const page = await hostPage({ dataset: { id: 'nm000103', zarr: true, subject: '001', task: 'N170' }, colorScheme: 'dark' });
  const { popup } = await openPopout(page);
  const preset = popup.__OSA_CHAT_CONFIG__;
  assert(!!preset && preset.fullscreen === true, 'the config, fullscreen');
  assertEqual(preset.communityId, 'nemar', 'for the page\'s community');
  assertEqual(preset.notebookUrl, NOTEBOOK_URL, 'with the page\'s own setConfig settings');
  assertEqual(preset.widgetScriptUrl, WIDGET_URL, 'and told where the widget\'s script lives');
  assertEqual(popup.__OSA_HOST_COLOR_SCHEME__, 'dark', 'the host\'s color scheme');
  assertEqual(popup.__OSA_DATASET__, { id: 'nm000103', zarr: true, subject: '001', task: 'N170' }, 'the dataset on screen, as {id, zarr, subject, task}');
  assert(preset instanceof popup.Object && !(preset instanceof page.window.Object), 'the config is the pop-out\'s own object, not one of the page\'s');
  assert(popup.__OSA_DATASET__ instanceof popup.Object, 'and so is the dataset');
  assert(Array.isArray(preset.suggestedQuestions) && preset.suggestedQuestions instanceof popup.Array, 'down to its arrays');

  const silent = await hostPage();
  const quiet = await openPopout(silent);
  assertEqual(quiet.popup.__OSA_HOST_COLOR_SCHEME__, null, 'a host that chose no scheme: the pop-out is told none');
  assertEqual(quiet.popup.__OSA_DATASET__, null, 'a page that named no dataset: none');
}

console.log('\nthe widget arrives by its address, with the host tag\'s integrity and crossorigin');
{
  const page = await hostPage();
  const { popup } = await openPopout(page);
  const script = popup.document.querySelector('script');
  assertEqual(script.getAttribute('src'), WIDGET_URL, 'the script\'s src is the widget\'s own address');
  assertEqual(script.getAttribute('integrity'), INTEGRITY, 'the host tag\'s integrity');
  assertEqual(script.getAttribute('crossorigin'), 'anonymous', 'and its crossorigin');
  assert(!script.hasAttribute('data-no-auto-init'), 'but not data-no-auto-init: the pop-out starts itself');
  assert(!!popup.OSAChatWidget && popup.OSAChatWidget !== page.window.OSAChatWidget, 'and it ran in the pop-out, as the pop-out\'s own copy');

  const bare = await hostPage({ tag: {} });
  const plain = (await openPopout(bare)).popup.document.querySelector('script');
  assert(!plain.hasAttribute('integrity') && !plain.hasAttribute('crossorigin'), 'a tag with neither: the pop-out\'s script has neither');

  const empty = await hostPage({ tag: { crossorigin: '' } });
  const bareCors = (await openPopout(empty)).popup.document.querySelector('script');
  assertEqual(bareCors.getAttribute('crossorigin'), '', 'a bare crossorigin attribute is carried as it is');
}

console.log('\na pop-out whose script does not load says so, in the pop-out');
{
  const page = await hostPage();
  // The tag now names an address with nothing there: its own script has run, and
  // the pop-out reads the address when it opens.
  page.script.setAttribute('src', 'http://localhost/missing/osa-chat-widget.js');
  const errors = [];
  const realError = page.window.console.error;
  page.window.console.error = (...args) => { errors.push(args.join(' ')); };
  let popup;
  try {
    page.click('.osa-popout-btn');
    ({ popup } = page.opened.at(-1));
    await waitUntil(() => popup.document.querySelector('.osa-popout-failure'), 'the failure message');
  } finally {
    page.window.console.error = realError;
  }
  const message = popup.document.querySelector('.osa-popout-failure');
  assertEqual(message.getAttribute('role'), 'alert', 'the pop-out shows a message, as an alert');
  assert(message.textContent.includes('could not load in this window'), 'saying the assistant could not load there');
  assert(!popup.document.querySelector('.osa-chat-widget'), 'and no widget');
  assert(errors.some((e) => e.includes('http://localhost/missing/osa-chat-widget.js')), 'the page\'s console names the address that failed');
}

console.log('\nthe pop-out is opened once, and a blocked one is reported');
{
  const page = await hostPage();
  const { popup } = await openPopout(page);
  page.click('.osa-popout-btn');
  assertEqual(page.opened.length, 1, 'a second click while it is open opens no second window');
  assert(!popup.closed, 'and leaves the first open');

  const blocked = await hostPage();
  blocked.window.open = () => null;
  blocked.click('.osa-popout-btn');
  assertEqual(blocked.alerts, ['Please allow popups to open the chat in a new window.'], 'a browser that blocks it: the reader is asked to allow popups');
}

console.log('\nlater setColorScheme and setDataset calls reach an open pop-out');
{
  const page = await hostPage({ config: configResponse({ color_scheme: 'auto' }), dataset: { id: 'nm000103', zarr: true }, colorScheme: 'dark' });
  const pop = await openPopout(page);
  assert(isDark(pop.container), 'the host chose dark: the pop-out is dark, after its own community config (auto, on a light device)');
  assertEqual(suggestions(pop.q)[0], 'What is nm000103 about?', 'the pop-out\'s questions are about the page\'s dataset');
  page.api.setColorScheme('light');
  assert(!isDark(pop.container), 'the host switching to light reaches it');
  page.api.setDataset({ id: 'nm000132', zarr: true });
  assertEqual(suggestions(pop.q)[0], 'What is nm000132 about?', 'the page naming another dataset reaches it');
  page.api.setDataset(null);
  assertEqual(suggestions(pop.q), ['How do I cite NEMAR?'], 'and so does clearing it: the general list');
}

console.log('\n... and one whose widget has not started yet');
{
  // happy-dom runs the pop-out's script as soon as it is added, and the widget
  // starts (init) a task later: calls in between reach its API, and then init reads
  // its presets, so those must be current too. In a browser the script also takes a
  // while to arrive, when the pop-out has no API at all and the presets are all.
  const page = await hostPage({ config: configResponse({ color_scheme: 'auto' }), dataset: { id: 'nm000103', zarr: true } });
  page.click('.osa-popout-btn');
  const { popup } = page.opened.at(-1);
  assert(!popup.document.querySelector('.osa-chat-widget'), 'sanity: the pop-out\'s widget has not started');
  page.api.setColorScheme('dark');
  page.api.setDataset({ id: 'nm000132', zarr: true });
  const pop = await popoutReady(page, popup);
  assert(isDark(pop.container), 'a scheme chosen before it started is the one it starts in');
  assertEqual(suggestions(pop.q)[0], 'What is nm000132 about?', 'and so is a dataset named before it started');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed ? 1 : 0);
