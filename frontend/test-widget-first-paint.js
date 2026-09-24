/**
 * The widget's first paint (#475), run against the real widget source in a happy-dom
 * window, the way test-widget-capsule.js runs the capsule.
 *
 * What it holds the widget to: a first visit keeps the launcher hidden until the
 * community config arrives (or a short cap passes), so it is never drawn in the
 * built-in defaults and then changed; the config's `widget` block is remembered in
 * the page's own storage, per community and API endpoint; a later load applies the
 * remembered look before anything is drawn, and the fresh config still arrives,
 * wins, and replaces what is remembered; an embedder's setConfig still outranks
 * both; a remembered entry that is for another endpoint, unreadable, or blocked is
 * ignored, and the widget behaves as on a first visit.
 *
 * What stands in: `fetch` (HTTP fixtures for the community config, one of which is
 * held until the test releases it, so the state before the config arrives can be
 * seen), and, in one test, a storage object whose every call throws, as a browser's
 * does with storage blocked. A reload is a fresh window whose storage starts with
 * what the previous window left, as a browser's does for the same origin. What the
 * reader sees across real frames, with transitions, is
 * frontend/browser-harness/first-paint-check.mjs's.
 *
 * Run with: bun frontend/test-widget-first-paint.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

let passed = 0;
let failed = 0;

const SUITE_TIMEOUT_MS = 30_000;
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

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');
const API = 'http://localhost/api';
const MEMORY_KEY = 'osa-widget-config-nemar';

const NEMAR_WIDGET = {
  title: 'NEMAR Assistant',
  placeholder: 'Ask about NEMAR',
  launcher: 'capsule',
  launcher_label: 'Explore NEMAR',
  theme_color: '#5bbad5',
  theme_text_color: '#0b1f2a',
  logo_url: '/nemar/logo',
};

function configResponse(widget = NEMAR_WIDGET) {
  return { default_model: 'm', offered_models: [], widget, client_tools: [], runtime: null };
}

// A fetch whose community config is held until release() is called (or answered
// at once, when `held` is false); everything else answers at once.
function configFetch(config, { held = true, status = 200 } = {}) {
  let release;
  const gate = held ? new Promise((resolve) => { release = resolve; }) : Promise.resolve();
  const fetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    await gate;
    return new Response(JSON.stringify(config), { status, headers: { 'content-type': 'application/json' } });
  };
  return { fetch, release: () => release && release() };
}

// A fresh window, as a new page load; `storage` seeds its localStorage with what an
// earlier load left, and `storageOverride` replaces it outright.
// `source` replaces the widget's source, for the one test that injects a fault into it.
function loadWidget({ fetch, storage = {}, storageOverride = null, preset = null, hostScheme, prefersColorScheme = 'light', source = SOURCE }) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true, device: { prefersColorScheme } },
  });
  if (hostScheme) window.__OSA_HOST_COLOR_SCHEME__ = hostScheme;
  window.__OSA_TEST__ = true;
  for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, value);
  if (preset) window.__OSA_CHAT_CONFIG__ = preset;
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  window.fetch = fetch;
  const localStorage = storageOverride || window.localStorage;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', source
  );
  run(window, window.document, localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  return { window, widget: window.OSAChatWidget };
}

function start(options, setConfig = {}) {
  const { window, widget } = loadWidget(options);
  widget.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: 'osa-test-first-paint', ...setConfig });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  return { window, widget, container, q: (s) => container.querySelector(s) };
}

const storageOf = (window) => {
  const out = {};
  for (let i = 0; i < window.localStorage.length; i++) {
    const key = window.localStorage.key(i);
    out[key] = window.localStorage.getItem(key);
  }
  return out;
};

// Collect warnings, and keep the widget's own startup chatter (no offered_models in
// these fixtures) out of what the tests count.
function captureWarnings() {
  const warnings = [];
  const original = console.warn;
  console.warn = (...args) => {
    const text = args.map(String).join(' ');
    if (!text.includes('offered_models')) warnings.push(text);
  };
  return { warnings, restore: () => { console.warn = original; } };
}

console.log('='.repeat(60));
console.log('Widget: the first paint (#475)');
console.log('='.repeat(60));

let afterFirstVisit = {};

console.log('\na first visit waits, hidden, for the config, then shows the community\'s look and remembers it');
{
  const { fetch, release } = configFetch(configResponse());
  const capture = captureWarnings();
  let started;
  try {
    started = start({ fetch });
  } finally {
    capture.restore();
  }
  const { window, widget, container, q } = started;
  assertEqual(capture.warnings, [], 'nothing remembered is the ordinary first visit: no warning');
  assert(container.classList.contains('osa-launcher-waiting'), 'nothing remembered: the launcher waits');
  assert(widget.__firstPaint.waiting(), 'and the widget says so');
  assertEqual(window.getComputedStyle(q('.osa-chat-button')).visibility, 'hidden', 'the chat button is out of sight, not drawn in the defaults');
  assertEqual(window.getComputedStyle(q('.osa-chat-tooltip')).visibility, 'hidden', 'and so is its tooltip');
  assert(!container.style.getPropertyValue('--osa-primary'), 'no theme color yet');
  assertEqual(storageOf(window)[MEMORY_KEY], undefined, 'and nothing remembered yet');

  release();
  await waitUntil(() => !container.classList.contains('osa-launcher-waiting'), 'the config arrives');
  assert(container.classList.contains('osa-capsule'), 'the config arrived: the capsule');
  assertEqual(container.style.getPropertyValue('--osa-primary'), '#5bbad5', 'in the community\'s theme color');
  assert(window.getComputedStyle(q('.osa-launcher-capsule .osa-chat-button')).visibility !== 'hidden', 'and now shown');
  const saved = JSON.parse(storageOf(window)[MEMORY_KEY] || 'null');
  assertEqual(saved, { apiEndpoint: API, widget: NEMAR_WIDGET }, 'the widget block is remembered, with the endpoint it came from');
  assertEqual(q('.osa-feedback-community').textContent, 'NEMAR', 'the feedback dialog names the community its feedback goes to, not the default one it was drawn with');
  afterFirstVisit = storageOf(window);
}

console.log('\na later load is drawn in the remembered look before the config arrives');
{
  const { fetch, release } = configFetch(configResponse({ ...NEMAR_WIDGET, theme_color: '#336699' }));
  const { window, widget, container, q } = start({ fetch, storage: afterFirstVisit });
  assert(!container.classList.contains('osa-launcher-waiting'), 'remembered: no wait');
  assert(!widget.__firstPaint.waiting(), 'and the widget does not wait');
  assert(container.classList.contains('osa-capsule'), 'the capsule, already, with the config still in flight');
  assertEqual(window.getComputedStyle(q('.osa-launcher-capsule .osa-chat-button')).width, '46px', 'at the capsule\'s 46px, never the bubble\'s 56px first');
  assertEqual(container.style.getPropertyValue('--osa-primary'), '#5bbad5', 'in the remembered theme color');
  assertEqual(container.style.getPropertyValue('--osa-on-primary'), '#0b1f2a', 'and its text color');
  assertEqual(q('.osa-chat-title').firstChild.textContent.trim(), 'NEMAR Assistant', 'the remembered title');
  assertEqual(q('.osa-chat-tooltip').textContent, 'Explore NEMAR', 'the remembered launcher label');
  assertEqual(q('.osa-chat-input input').placeholder, 'Ask about NEMAR', 'the remembered placeholder');
  const logo = q('.osa-chat-avatar img');
  assertEqual(logo && logo.getAttribute('src'), `${API}/nemar/logo`, 'and the remembered logo, resolved against the endpoint');

  release();
  await waitUntil(() => container.style.getPropertyValue('--osa-primary') === '#336699', 'the fresh config arrives');
  assert(true, 'the fresh config wins: its theme color replaces the remembered one');
  const saved = JSON.parse(storageOf(window)[MEMORY_KEY]);
  assertEqual(saved.widget.theme_color, '#336699', 'and replaces what is remembered');
  assert(q('.osa-chat-avatar img') === logo, 'the same logo is not rebuilt when the fresh config names it again');
}

console.log('\na field the fresh config no longer sends is not remembered for the next load');
{
  const { launcher_label: _dropped, ...withoutLabel } = NEMAR_WIDGET;
  const { fetch } = configFetch(configResponse(withoutLabel), { held: false });
  const { window, container } = start({ fetch, storage: afterFirstVisit });
  await waitUntil(() => JSON.parse(storageOf(window)[MEMORY_KEY]).widget.launcher_label === undefined, 'the fresh config is remembered');
  assert(!('launcher_label' in JSON.parse(storageOf(window)[MEMORY_KEY]).widget), 'what is remembered is the fresh block as sent, not merged into the old one');
  assert(container.classList.contains('osa-capsule'), 'this load keeps what it was drawn with');
}

console.log('\nthe embedder\'s own setConfig outranks the remembered look');
{
  const { fetch } = configFetch(configResponse());
  const { container, q } = start({ fetch, storage: afterFirstVisit }, { themeColor: '#123456', title: 'Page title' });
  assertEqual(container.style.getPropertyValue('--osa-primary'), '#123456', 'the embedder\'s theme color, from the first frame');
  assertEqual(q('.osa-chat-title').firstChild.textContent.trim(), 'Page title', 'and its title');
}

console.log('\na remembered entry for another endpoint, or one that cannot be read, is ignored');
{
  const other = { [MEMORY_KEY]: JSON.stringify({ apiEndpoint: 'https://elsewhere.example/api', widget: NEMAR_WIDGET }) };
  const a = start({ fetch: configFetch(configResponse()).fetch, storage: other });
  assert(a.container.classList.contains('osa-launcher-waiting'), 'another endpoint\'s look: treated as a first visit');
  assert(!a.container.classList.contains('osa-capsule'), 'and not applied');

  const capture = captureWarnings();
  try {
    const b = start({ fetch: configFetch(configResponse()).fetch, storage: { [MEMORY_KEY]: '{not json' } });
    assert(b.container.classList.contains('osa-launcher-waiting'), 'unreadable: treated as a first visit');
    assert(capture.warnings.some((w) => w.includes('Ignoring the remembered widget config')), 'with a warning');

    for (const junk of ['null', '"text"', '[1,2]', JSON.stringify({ apiEndpoint: API, widget: [1] })]) {
      capture.warnings.length = 0;
      const c = start({ fetch: configFetch(configResponse()).fetch, storage: { [MEMORY_KEY]: junk } });
      assert(c.container.classList.contains('osa-launcher-waiting'), `a stored ${junk}: treated as a first visit, without throwing`);
      assertEqual(capture.warnings, [], `and parsed fine, so without a warning that it could not be read`);
    }
  } finally {
    capture.restore();
  }
}

console.log('\nstorage that throws, as a browser\'s does when it is blocked, leaves the widget working');
{
  const blocked = {
    getItem() { throw new Error('SecurityError: storage is blocked'); },
    setItem() { throw new Error('SecurityError: storage is blocked'); },
    removeItem() { throw new Error('SecurityError: storage is blocked'); },
  };
  const capture = captureWarnings();
  const originalError = console.error;
  console.error = () => {};
  try {
    const { fetch, release } = configFetch(configResponse());
    const { container } = start({ fetch, storageOverride: blocked });
    assert(container.classList.contains('osa-launcher-waiting'), 'nothing can be read: a first visit');
    release();
    await waitUntil(() => container.classList.contains('osa-capsule'), 'the config arrives');
    assert(!container.classList.contains('osa-launcher-waiting'), 'the launcher is shown');
    assert(capture.warnings.some((w) => w.includes('Could not remember the widget config')), 'and the failure to remember reaches the console');
  } finally {
    capture.restore();
    console.error = originalError;
  }
}

console.log('\na config that never comes, or fails, does not hide the launcher for long');
{
  const { fetch } = configFetch(configResponse()); // never released
  const { window, widget, container, q } = start({ fetch });
  // The cap is read when init() runs, so this instance waits the full 1.5 s; the
  // shortened one below shows the cap itself.
  assert(container.classList.contains('osa-launcher-waiting'), 'waiting while the config is in flight');
  await waitUntil(() => !container.classList.contains('osa-launcher-waiting'), 'the cap passes', 6000);
  assert(window.getComputedStyle(q('.osa-chat-button')).visibility !== 'hidden', 'after the cap, the launcher is shown in the defaults');
  assert(!widget.__firstPaint.waiting(), 'and the wait is over');

  const short = loadWidget({ fetch: configFetch(configResponse()).fetch });
  short.widget.__firstPaint.setWait(30);
  short.widget.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: 'osa-test-first-paint-short' });
  const startedAt = Date.now();
  short.widget.init();
  const shortContainer = short.window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => !shortContainer.classList.contains('osa-launcher-waiting'), 'the shortened cap passes');
  assert(Date.now() - startedAt < 1000, 'the cap, not the config, ended the wait');

  const originalError = console.error;
  console.error = () => {};
  try {
    const failing = start({ fetch: configFetch(configResponse(), { held: false, status: 500 }).fetch });
    await waitUntil(() => !failing.container.classList.contains('osa-launcher-waiting'), 'the failed fetch');
    assert(true, 'a config request that fails shows the launcher at once, rather than after the cap');
    assertEqual(storageOf(failing.window)[MEMORY_KEY], undefined, 'and remembers nothing');
  } finally {
    console.error = originalError;
  }
}

console.log('\na remembered value goes through the same checks as a fresh one');
{
  const bad = { [MEMORY_KEY]: JSON.stringify({ apiEndpoint: API, widget: { ...NEMAR_WIDGET, theme_color: 'red', color_scheme: 'purple' } }) };
  const capture = captureWarnings();
  try {
    const { container } = start({ fetch: configFetch(configResponse()).fetch, storage: bad });
    assert(!container.style.getPropertyValue('--osa-primary'), 'an invalid remembered theme color is not applied');
    assert(capture.warnings.some((w) => w.includes('themeColor')), 'and is warned about, as a server one is');
    assert(capture.warnings.some((w) => w.includes('color_scheme')), 'as is an invalid remembered color scheme');
  } finally {
    capture.restore();
  }
}

console.log('\na bubble community waits on a first visit too, then renders exactly as before');
{
  const { title, placeholder } = NEMAR_WIDGET;
  const { fetch, release } = configFetch(configResponse({ title, placeholder, theme_color: '#aa3300' }));
  const { container } = start({ fetch });
  assertEqual(container.className, 'osa-chat-widget osa-launcher-waiting', 'waiting, and nothing else added');
  release();
  await waitUntil(() => !container.classList.contains('osa-launcher-waiting'), 'the config arrives');
  assertEqual(container.className, 'osa-chat-widget', 'then exactly the bubble\'s class');
  assertEqual(container.style.getPropertyValue('--osa-primary'), '#aa3300', 'in its own color');
}

console.log('\nthe pop-out never waits: it has no launcher');
{
  const { container } = start({ fetch: configFetch(configResponse()).fetch, preset: { fullscreen: true } });
  assert(!container.classList.contains('osa-launcher-waiting'), 'fullscreen: no waiting class');
}

// Markup with the whitespace between tags removed: a bubble that was opened once
// has already lost its template's whitespace around the icon.
const markup = (container) => container.innerHTML.replace(/>\s+</g, '><').trim();

console.log('\nthe fresh config wins even for a field it leaves out: the server omits defaults');
{
  // What the last load remembered: NEMAR as it was, a dark-following capsule with a
  // label, a text color and a logo.
  const before = { [MEMORY_KEY]: JSON.stringify({ apiEndpoint: API, widget: { ...NEMAR_WIDGET, color_scheme: 'auto' } }) };
  // What it is now, as WidgetConfig.resolve() sends a bubble in the light scheme
  // with no label, no text color and no logo: those keys absent, not set to a default.
  const now = { title: 'NEMAR Assistant', placeholder: 'Ask about NEMAR', theme_color: '#5bbad5', logo_url: null };

  const { fetch, release } = configFetch(configResponse(now));
  const stale = start({ fetch, storage: before, prefersColorScheme: 'dark' });
  assert(stale.container.classList.contains('osa-capsule') && stale.container.classList.contains('osa-dark'),
    'drawn from memory: the capsule, dark on a dark device');
  release();
  await waitUntil(() => !stale.container.classList.contains('osa-capsule'), 'the fresh config arrives');

  // The baseline: a load that already remembers the new config, so, like the stale
  // one, it is built from a known title (the feedback dialog's hint reads it once,
  // when the widget is built; a first visit builds it from the default).
  const fresh = start({
    fetch: configFetch(configResponse(now), { held: false }).fetch,
    storage: { [MEMORY_KEY]: JSON.stringify({ apiEndpoint: API, widget: now }) },
    prefersColorScheme: 'dark',
  });
  await waitUntil(() => fresh.q('.osa-chat-input input').placeholder === 'Ask about NEMAR', 'a load remembering the new config');
  await new Promise((resolve) => setTimeout(resolve, 20));

  assertEqual(stale.container.className, 'osa-chat-widget', 'the capsule is undone: exactly the bubble\'s class, and not dark');
  assert(markup(stale.container) === markup(fresh.container), 'and exactly the markup of a load that remembers the new config');
  assertEqual(stale.widget.getConfig().colorScheme, 'light', 'color_scheme left out: back to the light default');
  assertEqual(stale.container.style.getPropertyValue('--osa-on-primary'), '', 'theme_text_color left out: its property removed');
  assertEqual(stale.q('.osa-chat-tooltip').textContent, 'Ask me about NEMAR', 'launcher_label left out: the default tooltip');
  assert(!stale.q('.osa-chat-avatar img'), 'logo_url null: the default avatar again');
  assertEqual(stale.container.style.getPropertyValue('--osa-primary'), '#5bbad5', 'and what it does send still applies');
}

console.log('\na dropped theme color goes back to the stylesheet\'s, and a setConfig made meanwhile still wins');
{
  const before = { [MEMORY_KEY]: JSON.stringify({ apiEndpoint: API, widget: NEMAR_WIDGET }) };
  const { theme_color: _dropped, ...withoutColor } = NEMAR_WIDGET;
  const { fetch, release } = configFetch(configResponse({ ...withoutColor, title: 'Renamed Assistant' }));
  const { widget, container, q } = start({ fetch, storage: before });
  assertEqual(container.style.getPropertyValue('--osa-primary'), '#5bbad5', 'drawn in the remembered theme color');
  // The embedder sets its own title while the config is in flight.
  widget.setConfig({ title: 'Page title' });
  release();
  await waitUntil(() => container.style.getPropertyValue('--osa-primary') === '', 'the fresh config arrives');
  assert(true, 'theme_color left out: the property is removed, so the stylesheet\'s own color applies');
  assertEqual(widget.getConfig().title, 'Page title', 'the embedder\'s title, set after init, is not reset to the pre-init default');
  assertEqual(q('.osa-chat-title').firstChild.textContent.trim(), 'Page title', 'and it is what the header shows');
}

console.log('\nthe config is remembered for the community it was requested for');
{
  const { fetch, release } = configFetch(configResponse());
  const { window, widget } = start({ fetch });
  widget.setConfig({ communityId: 'other' }); // while the request is in flight
  release();
  await waitUntil(() => storageOf(window)[MEMORY_KEY] !== undefined, 'the config arrives');
  assertEqual(JSON.parse(storageOf(window)[MEMORY_KEY]).widget.title, 'NEMAR Assistant', 'under nemar, which it was fetched for');
  assertEqual(storageOf(window)['osa-widget-config-other'], undefined, 'and not under the community set since');
}

console.log('\none bad color is one warning, however often the config is applied');
{
  const bad = { ...NEMAR_WIDGET, theme_color: 'red' };
  const capture = captureWarnings();
  try {
    const { fetch, release } = configFetch(configResponse(bad));
    const { container } = start({ fetch, storage: { [MEMORY_KEY]: JSON.stringify({ apiEndpoint: API, widget: bad }) } });
    release();
    await waitUntil(() => !container.classList.contains('osa-launcher-waiting') && capture.warnings.length > 0, 'the fresh config arrives');
    await new Promise((resolve) => setTimeout(resolve, 50));
    assertEqual(capture.warnings.filter((w) => w.includes('themeColor')).length, 1, 'remembered, drawn, and fresh: warned once');
  } finally {
    capture.restore();
  }
}

console.log('\nan exception while the widget is built cannot leave the launcher hidden');
{
  const anchor = '    document.body.appendChild(container);\n';
  assertEqual(SOURCE.split(anchor).length - 1, 1, 'the fault goes in at one place: right after the widget joins the page');
  const faulty = SOURCE.replace(anchor, `${anchor}    throw new Error('injected after the widget joined the page');\n`);
  const { window, widget } = loadWidget({ fetch: configFetch(configResponse()).fetch, source: faulty });
  widget.__firstPaint.setWait(30);
  widget.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: 'osa-test-first-paint-fault' });
  let threw = null;
  try {
    widget.init();
  } catch (e) {
    threw = e;
  }
  assert(threw && threw.message.includes('injected'), 'init() throws the injected error');
  const container = window.document.querySelector('.osa-chat-widget');
  assert(container && container.classList.contains('osa-launcher-waiting'), 'the widget is in the page, waiting');
  await waitUntil(() => !container.classList.contains('osa-launcher-waiting'), 'the cap passes anyway');
  assert(true, 'the cap was scheduled before anything that could throw, so the launcher is shown');
}

console.log('\nthe tooltip waits for its button');
{
  const { fetch, release } = configFetch(configResponse());
  const { window, widget } = loadWidget({ fetch });
  widget.__firstPaint.setWait(4000);
  widget.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: 'osa-test-first-paint-tooltip' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const tooltip = container.querySelector('.osa-chat-tooltip');
  await new Promise((resolve) => setTimeout(resolve, 1700));
  assert(container.classList.contains('osa-launcher-waiting'), 'still waiting, 1.7 s in');
  assert(!tooltip.classList.contains('visible'), 'and its tooltip, due 1.5 s after init before, is not shown');
  release();
  await waitUntil(() => !container.classList.contains('osa-launcher-waiting'), 'the config arrives');
  await waitUntil(() => tooltip.classList.contains('visible'), 'the tooltip', 3000);
  assert(true, 'the tooltip shows 1.5 s after the launcher does');
}

console.log('\nthe pop-out keeps its host\'s scheme over a remembered one');
{
  const remembered = { [MEMORY_KEY]: JSON.stringify({ apiEndpoint: API, widget: { ...NEMAR_WIDGET, color_scheme: 'auto' } }) };
  const { widget, container } = start({ fetch: configFetch(configResponse()).fetch, storage: remembered, preset: { fullscreen: true }, hostScheme: 'dark' });
  assertEqual(widget.getConfig().colorScheme, 'dark', 'the host page chose dark: the remembered auto does not replace it');
  assert(!container.classList.contains('osa-launcher-waiting'), 'and it does not wait');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed ? 1 : 0);
