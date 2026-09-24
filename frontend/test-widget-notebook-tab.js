/**
 * The notebook as a tab of the widget's panel (#470), run against the real widget
 * source in a happy-dom window, the way test-widget-capsule.js runs the capsule.
 *
 * What it holds the widget to: the capsule's circles switch tabs and close the
 * panel from the open one; the notebook's frame is made once, at the notebook
 * site's contract address, and kept while chat is open; the header, the chat-only
 * buttons and the views follow the tab; the notebook's messages are heard only
 * from that frame and that origin, and drive the header line, the loading and
 * fallback overlays, and the busy ring; the widget's color scheme reaches the
 * notebook; a dataset change replaces or drops the frame; and a bubble widget
 * gets none of it.
 *
 * What stands in: `fetch` (HTTP fixtures for the community config), and the
 * notebook's side of the bridge: messages dispatched as if from the frame, in
 * the shapes notebook/osa-bridge.js posts, and the frame's postMessage wrapped
 * to record what the widget sends it. The frame itself never loads (happy-dom's
 * child-frame navigation is off); frames that load a real notebook are
 * frontend/browser-harness/notebook-tab-check.mjs's.
 *
 * Run with: bun frontend/test-widget-notebook-tab.js
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
const NOTEBOOK_ORIGIN = 'https://notebook.osc.earth';

let configCounter = 0;
function configResponse(widgetOverrides = {}) {
  configCounter += 1;
  return {
    default_model: 'm',
    offered_models: [],
    widget: { title: 'NEMAR Assistant', placeholder: `loaded ${configCounter}`, launcher: 'capsule', ...widgetOverrides },
    client_tools: [],
    runtime: null,
  };
}

function fetchReturning(config) {
  return async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
}

function loadWidget({ fetch }) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      navigation: { disableChildFrameNavigation: true },
    },
  });
  window.__OSA_TEST__ = true;
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  return { window, widget: window.OSAChatWidget };
}

// A NEMAR-like capsule widget with a dataset that has a Zarr copy, its config loaded.
async function startCapsule({ dataset = { id: 'nm000103', zarr: true }, widget: widgetOverrides, before } = {}) {
  const config = configResponse(widgetOverrides);
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'nemar', storageKey: `osa-test-nbtab-${configCounter}` });
  if (dataset) widget.setDataset(dataset);
  if (before) before(widget, window);
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder, 'config loaded');
  const q = (selector) => container.querySelector(selector);
  const click = (selector) => q(selector).dispatchEvent(new window.Event('click', { bubbles: true }));
  return { window, widget, container, q, click };
}

// A message as notebook/osa-bridge.js posts it, from `source` and `origin`.
function bridgeMessage(window, data, { source, origin = NOTEBOOK_ORIGIN }) {
  window.dispatchEvent(new window.MessageEvent('message', { data: { source: 'osa-notebook', ...data }, origin, source }));
}

// Record what the widget sends the notebook's frame.
function recordPosts(frame) {
  const posts = [];
  frame.contentWindow.postMessage = (message, targetOrigin) => posts.push({ message, targetOrigin });
  return posts;
}

const isOn = (view) => view.classList.contains('osa-view-on') && !view.hasAttribute('inert');
const isOff = (view) => view.classList.contains('osa-view-off') && view.hasAttribute('inert');

console.log('='.repeat(60));
console.log('Widget: the notebook as a tab of the panel (#470)');
console.log('='.repeat(60));

console.log('\na bubble widget gets none of it');
{
  const config = configResponse({ launcher: undefined });
  delete config.widget.launcher;
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-nbtab-bubble' });
  widget.setDataset({ id: 'nm000103', zarr: true });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder, 'config loaded');
  assert(!container.querySelector('.osa-views'), 'no views wrapper');
  assert(!container.querySelector('.osa-view-notebook'), 'no notebook view');
  assert(container.querySelector('.osa-chat-messages').parentElement === container.querySelector('.osa-chat-window'),
    'the chat\'s elements stay direct children of the panel, as before');
  assert(!container.querySelector('.osa-ttl'), 'the header title is not wrapped');
  container.querySelector('.osa-chat-button').dispatchEvent(new window.Event('click', { bubbles: true }));
  assertEqual(container.querySelector('.osa-chat-button').getAttribute('aria-label'), 'Close chat', 'the bubble still swaps to its close label');
}

console.log('\nthe capsule\'s panel has a chat view and a notebook view, and chat is open to begin with');
{
  const { q, container } = await startCapsule();
  const chatView = q('.osa-view-chat');
  const notebookView = q('.osa-view-notebook');
  assert(!!chatView && !!notebookView, 'both views exist');
  assert(chatView.parentElement === notebookView.parentElement && chatView.parentElement.classList.contains('osa-views'), 'side by side in one views element');
  for (const selector of ['.osa-chat-messages', '.osa-suggestions', '.osa-chat-input', '.osa-ai-disclaimer', '.osa-combined-footer', '.osa-error']) {
    assert(q(selector).closest('.osa-view-chat') === chatView, `${selector} is in the chat view`);
  }
  assert(q('.osa-settings-overlay').parentElement === q('.osa-chat-window'), 'the Settings overlay stays over the whole panel');
  assert(isOn(chatView) && isOff(notebookView), 'chat on; the notebook view off and inert');
  assert(container.classList.contains('osa-tab-chat'), 'the container says chat is the open tab');
  assert(!q('.osa-notebook-frame'), 'no notebook frame until the notebook tab opens');
}

console.log('\nthe notebook circle opens the panel on the notebook tab, with its frame at the contract address');
{
  const { window, q, click, container } = await startCapsule();
  const opens = [];
  window.open = (...args) => { opens.push(args); return null; };
  click('.osa-notebook-btn');
  assert(q('.osa-chat-window').classList.contains('open'), 'the panel opens');
  assertEqual(opens.length, 0, 'no browser tab');
  assert(container.classList.contains('osa-tab-notebook') && !container.classList.contains('osa-tab-chat'), 'the notebook is the open tab');
  assert(isOn(q('.osa-view-notebook')) && isOff(q('.osa-view-chat')), 'the notebook view on; chat off and inert');
  const frame = q('.osa-view-notebook .osa-notebook-frame');
  assertEqual(frame && frame.getAttribute('src'), 'https://notebook.osc.earth/osa/open.html?community=nemar&dataset=nm000103', 'the frame\'s address is the notebook site\'s contract');
  assertEqual(frame && frame.title, 'Python notebook for nm000103', 'the frame has a title for assistive technology');
  assert(!frame.hasAttribute('sandbox'), 'no sandbox: the notebook needs its storage and its service worker');
  assertEqual(q('.osa-notebook-btn').getAttribute('aria-pressed'), 'true', 'the notebook circle is pressed');
  assert(q('.osa-notebook-btn').classList.contains('osa-tab-current'), 'and drawn as the open tab');
  assertEqual(q('.osa-launcher-capsule .osa-chat-button').getAttribute('aria-pressed'), 'false', 'the chat circle is not');
  assertEqual(q('.osa-launcher-capsule .osa-chat-button').getAttribute('aria-label'), 'Chat with NEMAR Assistant', 'the chat circle says it goes back to chat');
  assertEqual(q('.osa-notebook-btn').getAttribute('aria-label'), 'Close NEMAR Assistant', 'the notebook circle says it closes the panel now');

  // The header follows the tab.
  assert(q('.osa-ttl-notebook') && !q('.osa-ttl-notebook').classList.contains('osa-ttl-off'), 'the header shows the notebook title');
  assert(q('.osa-ttl-chat').classList.contains('osa-ttl-off'), 'and not the chat title');
  assertEqual(q('.osa-ttl-chat').getAttribute('aria-hidden'), 'true', 'the hidden title is hidden from assistive technology');
  assertEqual(q('.osa-notebook-status-text').textContent, 'nm000103 · Opening the notebook…', 'the status line names the dataset and says it is opening');
  for (const selector of ['.osa-settings-btn-open', '.osa-reset-btn', '.osa-popout-btn']) {
    assertEqual(window.getComputedStyle(q(selector)).visibility, 'hidden', `${selector} is hidden on the notebook tab`);
  }
  assert(window.getComputedStyle(q('.osa-close-btn')).visibility !== 'hidden', 'the close button stays');
  assert(!q('.osa-notebook-loading').classList.contains('osa-overlay-hidden'), 'the loading overlay covers the frame while it loads');
  assert(q('.osa-notebook-fallback').classList.contains('osa-overlay-hidden'), 'the fallback does not');
}

console.log('\nswitching back to chat keeps the frame; the open tab\'s circle closes the panel');
{
  const { q, click, container } = await startCapsule();
  click('.osa-notebook-btn');
  const frame = q('.osa-notebook-frame');
  click('.osa-launcher-capsule .osa-chat-button');
  assert(container.classList.contains('osa-tab-chat'), 'the chat circle goes back to chat');
  assert(q('.osa-chat-window').classList.contains('open'), 'without closing the panel');
  assert(isOn(q('.osa-view-chat')) && isOff(q('.osa-view-notebook')), 'the chat view on; the notebook off and inert');
  assert(q('.osa-notebook-frame') === frame && frame.isConnected, 'the same frame, still in the page, so its Python keeps running');
  for (const selector of ['.osa-settings-btn-open', '.osa-reset-btn', '.osa-popout-btn']) {
    assert(q(selector).ownerDocument.defaultView.getComputedStyle(q(selector)).visibility !== 'hidden', `${selector} is back on the chat tab`);
  }

  click('.osa-notebook-btn');
  assert(q('.osa-notebook-frame') === frame, 'reopening the notebook tab reuses the frame');
  click('.osa-notebook-btn');
  assert(!q('.osa-chat-window').classList.contains('open'), 'the notebook circle, on the notebook tab, closes the panel');
  assert(frame.isConnected, 'and the frame survives the panel closing');

  click('.osa-launcher-capsule .osa-chat-button');
  assert(q('.osa-chat-window').classList.contains('open') && container.classList.contains('osa-tab-chat'), 'the chat circle reopens the panel on chat');
  click('.osa-launcher-capsule .osa-chat-button');
  assert(!q('.osa-chat-window').classList.contains('open'), 'and, on chat, closes it');

  click('.osa-notebook-btn');
  click('.osa-close-btn');
  assert(!q('.osa-chat-window').classList.contains('open'), 'the header\'s close button closes the panel from the notebook tab too');
  assertEqual(q('.osa-launcher-capsule .osa-chat-button').innerHTML.includes('<svg'), true, 'the chat circle still shows an icon after closing');
}

console.log('\nthe notebook\'s messages drive the header, the overlays and the busy ring');
{
  const { window, q, click } = await startCapsule();
  click('.osa-notebook-btn');
  const frame = q('.osa-notebook-frame');
  const posts = recordPosts(frame);
  const source = frame.contentWindow;

  bridgeMessage(window, { type: 'ready' }, { source });
  assert(q('.osa-notebook-loading').classList.contains('osa-overlay-hidden'), 'ready: the loading overlay goes');
  assertEqual(q('.osa-notebook-status-text').textContent, 'nm000103 · Starting Python…', 'ready, no setup word yet: Python is starting');
  assertEqual(posts.map((p) => [p.message, p.targetOrigin]), [[{ target: 'osa-notebook', type: 'theme', scheme: 'light' }, NOTEBOOK_ORIGIN]],
    'ready: the widget sends its scheme, addressed to the notebook\'s origin only');

  bridgeMessage(window, { type: 'setup', status: 'running' }, { source });
  click('.osa-launcher-capsule .osa-chat-button');
  assert(q('.osa-notebook-btn').classList.contains('osa-notebook-busy'), 'setup running, on chat: the notebook circle\'s ring turns');
  click('.osa-notebook-btn');
  assert(!q('.osa-notebook-btn').classList.contains('osa-notebook-busy'), 'on the notebook tab, no ring: the header says it');

  bridgeMessage(window, { type: 'setup', status: 'done' }, { source });
  assertEqual(q('.osa-notebook-status-text').textContent, 'nm000103 · Python ready', 'setup done: Python ready');
  click('.osa-launcher-capsule .osa-chat-button');
  assert(!q('.osa-notebook-btn').classList.contains('osa-notebook-busy'), 'and no ring on chat any more');

  bridgeMessage(window, { type: 'setup', status: 'running' }, { source });
  assert(q('.osa-notebook-btn').classList.contains('osa-notebook-busy'), 'a restart runs setup again, and the ring turns again');
  bridgeMessage(window, { type: 'setup', status: 'error' }, { source });
  assertEqual(q('.osa-notebook-status-text').textContent, 'nm000103 · Setup did not finish; see the notebook', 'setup error: the header says to look');
  bridgeMessage(window, { type: 'setup', status: 'none' }, { source });
  assertEqual(q('.osa-notebook-status-text').textContent, 'nm000103 · Ready', 'no setup cell: ready');
}

console.log('\nonly the frame the widget made, at the notebook\'s origin, is heard');
{
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => { warnings.push(args.map(String).join(' ')); };
  try {
    const { window, q, click } = await startCapsule();
    click('.osa-notebook-btn');
    const frame = q('.osa-notebook-frame');
    warnings.length = 0; // startup's own, about the test's config, are not this test's
    bridgeMessage(window, { type: 'ready' }, { source: frame.contentWindow, origin: 'https://evil.example' });
    assert(!q('.osa-notebook-loading').classList.contains('osa-overlay-hidden'), 'another origin, even from the frame: ignored');
    bridgeMessage(window, { type: 'ready' }, { source: window, origin: NOTEBOOK_ORIGIN });
    assert(!q('.osa-notebook-loading').classList.contains('osa-overlay-hidden'), 'the notebook\'s origin, but not from the frame: ignored');
    window.dispatchEvent(new window.MessageEvent('message', { data: { type: 'ready' }, origin: NOTEBOOK_ORIGIN, source: frame.contentWindow }));
    assert(!q('.osa-notebook-loading').classList.contains('osa-overlay-hidden'), 'from the frame and its origin, but not the bridge\'s shape: ignored');
    assertEqual(warnings.length, 0, 'none of those is worth a warning: they are not the notebook speaking');
    bridgeMessage(window, { type: 'setup', status: 'exploded' }, { source: frame.contentWindow });
    assertEqual(window.OSAChatWidget.__notebook.state().setup, null, 'a setup status the bridge never sends: ignored');
    assert(warnings.some((w) => w.includes('does not recognize')), 'but the notebook sent it, so the console hears of it');
    bridgeMessage(window, { type: 'ready' }, { source: frame.contentWindow });
    assert(q('.osa-notebook-loading').classList.contains('osa-overlay-hidden'), 'the real thing is heard');

    warnings.length = 0;
    bridgeMessage(window, { type: 'theme', scheme: 'dark', applied: true }, { source: frame.contentWindow });
    assertEqual(warnings.length, 0, 'a theme the notebook applied: nothing to say');
    bridgeMessage(window, { type: 'theme', scheme: 'dark', applied: false }, { source: frame.contentWindow });
    assert(warnings.some((w) => w.includes('could not apply the dark theme')), 'a theme it could not apply reaches the console');
    assertEqual(window.OSAChatWidget.__notebook.state().state, 'ready', 'and changes nothing else');
  } finally {
    console.warn = originalWarn;
  }
}

console.log('\nthe capsule listens for the notebook; a bubble does not');
{
  // A bubble has no frame, so nothing reaches it either way; this checks it does
  // not even listen, since only a capsule has a notebook to hear from.
  const config = configResponse({ launcher: undefined });
  delete config.widget.launcher;
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  const types = [];
  const originalAdd = window.addEventListener.bind(window);
  window.addEventListener = (type, ...rest) => { types.push(type); return originalAdd(type, ...rest); };
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-nbtab-bubble-listen' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder, 'config loaded');
  assert(!types.includes('message'), 'a bubble adds no message listener');

  const capsule = loadWidget({ fetch: fetchReturning(configResponse()) });
  const capsuleTypes = [];
  const capsuleAdd = capsule.window.addEventListener.bind(capsule.window);
  capsule.window.addEventListener = (type, ...rest) => { capsuleTypes.push(type); return capsuleAdd(type, ...rest); };
  capsule.widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'nemar', storageKey: 'osa-test-nbtab-capsule-listen' });
  capsule.widget.init();
  const capsuleContainer = capsule.window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => capsuleContainer.classList.contains('osa-capsule'), 'the capsule');
  assertEqual(capsuleTypes.filter((t) => t === 'message').length, 1, 'a capsule adds exactly one');
}

console.log('\nthe pre-set config\'s notebook address is held to setConfig\'s rule');
{
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => { warnings.push(args.map(String).join(' ')); };
  try {
    const { q, click } = await startCapsule({
      before: (_widget, window) => {
        window.__OSA_CHAT_CONFIG__ = { notebookUrl: 'javascript:alert(1)//' };
      },
    });
    click('.osa-notebook-btn');
    assertEqual(q('.osa-notebook-frame').getAttribute('src'), 'https://notebook.osc.earth/osa/open.html?community=nemar&dataset=nm000103',
      'an invalid pre-set notebookUrl is dropped: the frame opens the default');
    assert(warnings.some((w) => w.includes('Invalid notebookUrl')), 'with a warning');

    const custom = await startCapsule({
      before: (_widget, window) => {
        window.__OSA_CHAT_CONFIG__ = { notebookUrl: 'https://develop-notebook.osc.earth/osa' };
      },
    });
    custom.click('.osa-notebook-btn');
    assertEqual(custom.q('.osa-notebook-frame').getAttribute('src'), 'https://develop-notebook.osc.earth/osa/open.html?community=nemar&dataset=nm000103',
      'a valid one is normalized, trailing slash and all');
  } finally {
    console.warn = originalWarn;
  }
}

console.log('\nthe widget\'s color scheme reaches the notebook, once per change');
{
  const { window, widget, q, click } = await startCapsule({ widget: { color_scheme: 'auto' } });
  click('.osa-notebook-btn');
  const frame = q('.osa-notebook-frame');
  const posts = recordPosts(frame);
  bridgeMessage(window, { type: 'ready' }, { source: frame.contentWindow });
  widget.setColorScheme('dark');
  widget.setColorScheme('dark');
  widget.setColorScheme('light');
  assertEqual(posts.map((p) => p.message.scheme), ['light', 'dark', 'light'], 'ready (light), then dark, then light: a repeat is not sent again');
  assert(posts.every((p) => p.targetOrigin === NOTEBOOK_ORIGIN), 'every one addressed to the notebook\'s origin');
}

console.log('\na notebook that never speaks, or fails to start, falls back to a tab of its own');
{
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => { warnings.push(args.join(' ')); };
  try {
    const { window, widget, q, click } = await startCapsule();
    widget.__notebook.setTimeouts(20, 40);
    click('.osa-notebook-btn');
    await waitUntil(() => widget.__notebook.state().state === 'failed', 'the notebook times out');
    const fallback = q('.osa-notebook-fallback');
    assert(!fallback.classList.contains('osa-overlay-hidden'), 'the fallback shows');
    assert(q('.osa-notebook-loading').classList.contains('osa-overlay-hidden'), 'and the loading overlay goes');
    assertEqual(q('.osa-notebook-open-tab').getAttribute('href'), 'https://notebook.osc.earth/osa/open.html?community=nemar&dataset=nm000103', 'its link opens the same notebook in a tab of its own');
    assertEqual(q('.osa-notebook-open-tab').getAttribute('target'), '_blank', 'a new tab');
    assertEqual(q('.osa-notebook-status-text').textContent, 'nm000103 · The notebook did not open here', 'the header says so');
    assert(warnings.some((w) => w.includes('did not load in time')), 'and the console, once');
    const first = q('.osa-notebook-frame');
    widget.__notebook.setTimeouts(12000, 45000);
    click('.osa-notebook-retry');
    assert(q('.osa-notebook-frame') && q('.osa-notebook-frame') !== first, 'Try again makes a fresh frame');
    assert(!first.isConnected, 'and removes the old one');
    assert(q('.osa-notebook-fallback').classList.contains('osa-overlay-hidden'), 'the fallback goes while it loads');

    bridgeMessage(window, { type: 'error', phase: 'startup' }, { source: q('.osa-notebook-frame').contentWindow });
    assert(!q('.osa-notebook-fallback').classList.contains('osa-overlay-hidden'), 'a startup error from the notebook shows the fallback at once');
    assertEqual(q('.osa-notebook-fallback .osa-notebook-overlay-text').textContent, 'The notebook loaded but could not start here.', 'saying it loaded but could not start');
  } finally {
    console.warn = originalWarn;
  }
}

console.log('\neach timeout falls back on its own: after the frame loads, and from its creation');
{
  const originalWarn = console.warn;
  console.warn = () => {};
  try {
    // A frame that loads (an error page, or a page refused by the host's policy)
    // and never speaks: the shorter after-load timeout, with the total one far off.
    const loaded = await startCapsule();
    loaded.widget.__notebook.setTimeouts(20, 60000);
    loaded.click('.osa-notebook-btn');
    const frame = loaded.q('.osa-notebook-frame');
    const posts = recordPosts(frame);
    frame.dispatchEvent(new loaded.window.Event('load'));
    assertEqual(posts.map((p) => [p.message, p.targetOrigin]), [[{ target: 'osa-notebook', type: 'theme', scheme: 'light' }, NOTEBOOK_ORIGIN]],
      'the frame\'s load sends the scheme, before the notebook is ready, to its origin only');
    await waitUntil(() => loaded.widget.__notebook.state().state === 'failed', 'the after-load timeout');
    assert(!loaded.q('.osa-notebook-fallback').classList.contains('osa-overlay-hidden'), 'a loaded frame that never speaks falls back');

    // A frame that never even loads: the total timeout, with the after-load one far off.
    const silent = await startCapsule();
    silent.widget.__notebook.setTimeouts(60000, 40);
    silent.click('.osa-notebook-btn');
    await waitUntil(() => silent.widget.__notebook.state().state === 'failed', 'the total timeout');
    assert(!silent.q('.osa-notebook-fallback').classList.contains('osa-overlay-hidden'), 'a frame that never loads falls back too');
  } finally {
    console.warn = originalWarn;
  }
}

console.log('\na replaced frame\'s timeout never fails the frame that replaced it');
{
  const { widget, q, click } = await startCapsule();
  widget.__notebook.setTimeouts(20, 40);
  click('.osa-notebook-btn');
  const first = q('.osa-notebook-frame');
  widget.__notebook.setTimeouts(60000, 60000);
  widget.setDataset({ id: 'nm000104', zarr: true });
  first.dispatchEvent(new first.ownerDocument.defaultView.Event('load'));
  await new Promise((resolve) => setTimeout(resolve, 120));
  assertEqual(widget.__notebook.state().state, 'loading', 'the new dataset\'s notebook is still loading, 120ms after the old one\'s 40ms timeout');
  assert(q('.osa-notebook-fallback').classList.contains('osa-overlay-hidden'), 'no fallback');
}

console.log('\na startup error after ready does not cover a working notebook');
{
  const { window, widget, q, click } = await startCapsule();
  click('.osa-notebook-btn');
  const source = q('.osa-notebook-frame').contentWindow;
  bridgeMessage(window, { type: 'ready' }, { source });
  bridgeMessage(window, { type: 'setup', status: 'done' }, { source });
  // The bridge's catch also covers what it does after ready (its restart hooks),
  // so this can arrive while the notebook itself works.
  bridgeMessage(window, { type: 'error', phase: 'startup' }, { source });
  assertEqual(widget.__notebook.state().state, 'ready', 'still ready');
  assert(q('.osa-notebook-fallback').classList.contains('osa-overlay-hidden'), 'no fallback over the notebook');
  assertEqual(q('.osa-notebook-status-text').textContent, 'nm000103 · Python ready', 'and the header still says Python is ready');
}

console.log('\na dataset change replaces the frame, and one with no Zarr copy takes the panel back to chat');
{
  const { widget, q, click, container } = await startCapsule();
  click('.osa-notebook-btn');
  const first = q('.osa-notebook-frame');
  widget.setDataset({ id: 'nm000104', zarr: true });
  const second = q('.osa-notebook-frame');
  assert(second && second !== first && !first.isConnected, 'on the notebook tab, a new dataset gets a new frame');
  assertEqual(second.getAttribute('src'), 'https://notebook.osc.earth/osa/open.html?community=nemar&dataset=nm000104', 'at the new dataset\'s address');
  widget.setDataset({ id: 'nm000105', zarr: false });
  assert(container.classList.contains('osa-tab-chat'), 'no Zarr copy: back to chat');
  assert(!q('.osa-notebook-frame'), 'and the frame is dropped');
  assertEqual(q('.osa-notebook-btn').getAttribute('aria-disabled'), 'true', 'the notebook circle is unavailable');
  click('.osa-notebook-btn');
  assert(container.classList.contains('osa-tab-chat') && !q('.osa-notebook-frame'), 'and does nothing');
}

console.log('\nthe chat circle keeps its icon, and focus follows the tab');
{
  const { window, q, click, container } = await startCapsule();
  const icon = () => q('.osa-launcher-capsule .osa-chat-button svg').outerHTML;
  const chatIcon = icon();
  const input = q('.osa-chat-input input');
  const isOpenOn = (tab) => q('.osa-chat-window').classList.contains('open') && container.classList.contains(`osa-tab-${tab}`);

  click('.osa-launcher-capsule .osa-chat-button');
  assertEqual(icon(), chatIcon, 'open on chat, the chat circle keeps the chat icon (a bubble swaps to a close icon)');
  // Identity, not assertEqual: every element serializes to {}, so any two compare equal.
  assert(window.document.activeElement === input, 'opening on chat puts focus in the chat input');

  click('.osa-notebook-btn');
  assertEqual(icon(), chatIcon, 'on the notebook tab, too');
  input.blur();
  click('.osa-launcher-capsule .osa-chat-button');
  assert(window.document.activeElement === input, 'going back to chat puts focus back in the chat input');

  click('.osa-launcher-capsule .osa-chat-button');
  assertEqual(icon(), chatIcon, 'closed: still the chat icon');
  input.blur();
  click('.osa-notebook-btn');
  assert(isOpenOn('notebook'), 'the notebook circle opens a closed panel on the notebook tab');
  assert(window.document.activeElement !== input, 'without putting focus in the hidden chat input');

  const frame = q('.osa-notebook-frame');
  click('.osa-notebook-btn');
  assert(!q('.osa-chat-window').classList.contains('open'), 'closed from the notebook tab');
  click('.osa-notebook-btn');
  assert(isOpenOn('notebook'), 'the notebook circle reopens it on the notebook tab');
  assert(q('.osa-notebook-frame') === frame, 'with the same frame');
  assert(window.document.activeElement !== input, 'and focus still not in the chat input');
}

console.log('\nleaving a dataset page with the notebook open takes the panel back to chat');
{
  const { widget, q, click, container } = await startCapsule();
  click('.osa-notebook-btn');
  assert(!!q('.osa-notebook-frame'), 'the notebook tab has its frame');
  let threw = null;
  try {
    widget.setDataset(null);
  } catch (e) {
    threw = e;
  }
  assertEqual(threw, null, 'setDataset(null) with a frame open does not throw');
  assert(container.classList.contains('osa-tab-chat'), 'the panel is back on chat');
  assert(!q('.osa-notebook-frame'), 'the frame is dropped');
  assertEqual(q('.osa-notebook-btn').getAttribute('aria-disabled'), 'true', 'and the notebook circle is unavailable');
}

console.log('\na failed notebook stays failed until Try again, says so on its circle, and recovers if it was only slow');
{
  const originalWarn = console.warn;
  console.warn = () => {};
  try {
    const { window, widget, q, click } = await startCapsule();
    widget.__notebook.setTimeouts(20, 40);
    click('.osa-notebook-btn');
    const frame = q('.osa-notebook-frame');
    await waitUntil(() => widget.__notebook.state().state === 'failed', 'the notebook times out');
    widget.__notebook.setTimeouts(12000, 45000);
    const notebookButton = q('.osa-notebook-btn');
    assert(!notebookButton.classList.contains('osa-notebook-attention'), 'on its own tab, the circle needs no cue: the fallback is on screen');

    click('.osa-launcher-capsule .osa-chat-button');
    assert(notebookButton.classList.contains('osa-notebook-attention'), 'on chat, the notebook circle carries the attention cue');
    assertEqual(notebookButton.querySelector('.osa-icon-tooltip').textContent, 'The nm000103 notebook did not open here', 'its tooltip says what happened');
    assertEqual(notebookButton.getAttribute('aria-label'), 'Notebook: The nm000103 notebook did not open here', 'and so does its label');

    click('.osa-notebook-btn');
    assert(q('.osa-notebook-frame') === frame, 'coming back to the tab keeps the failed frame rather than starting over');
    assert(!q('.osa-notebook-fallback').classList.contains('osa-overlay-hidden'), 'and still shows the fallback');
    assertEqual(widget.__notebook.state().state, 'failed', 'still failed');

    bridgeMessage(window, { type: 'ready' }, { source: frame.contentWindow });
    assertEqual(widget.__notebook.state().state, 'ready', 'a notebook that was only slow recovers when it reports ready');
    assert(q('.osa-notebook-fallback').classList.contains('osa-overlay-hidden'), 'and the fallback goes');

    bridgeMessage(window, { type: 'setup', status: 'error' }, { source: frame.contentWindow });
    click('.osa-launcher-capsule .osa-chat-button');
    assert(notebookButton.classList.contains('osa-notebook-attention'), 'setup failed, on chat: the cue');
    assertEqual(notebookButton.querySelector('.osa-icon-tooltip').textContent, 'Setup did not finish in the nm000103 notebook', 'saying setup did not finish');
    bridgeMessage(window, { type: 'setup', status: 'done' }, { source: frame.contentWindow });
    assert(!notebookButton.classList.contains('osa-notebook-attention'), 'a rerun that finishes clears it');
    assertEqual(notebookButton.querySelector('.osa-icon-tooltip').textContent, 'Open nm000103 in a Python notebook', 'and the tooltip is the ordinary one');
  } finally {
    console.warn = originalWarn;
  }
}

console.log('\nresizing turns the frame\'s pointer off for the drag');
{
  const { window, q, click } = await startCapsule();
  click('.osa-notebook-btn');
  const chatWindow = q('.osa-chat-window');
  q('.osa-resize-handle').dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true, clientX: 500, clientY: 500 }));
  assert(chatWindow.classList.contains('osa-resizing'), 'during the drag');
  assertEqual(window.getComputedStyle(q('.osa-notebook-frame')).pointerEvents, 'none', 'the frame takes no pointer events');
  window.document.dispatchEvent(new window.MouseEvent('mouseup', { bubbles: true }));
  assert(!chatWindow.classList.contains('osa-resizing'), 'and after it, they are back');
}

console.log('\nthe circles are 46px in the capsule, and a bubble\'s chat button stays 56px');
{
  const { window, q } = await startCapsule();
  for (const selector of ['.osa-launcher-capsule .osa-chat-button', '.osa-notebook-btn', '.osa-hpc-btn', '.osa-capsule-indicator']) {
    const style = window.getComputedStyle(q(selector));
    assertEqual([style.width, style.height], ['46px', '46px'], `${selector} is 46px`);
  }
  assertEqual(window.getComputedStyle(q('.osa-send-btn')).width, '40px', 'the Send button, for scale, is 40px');

  const config = configResponse({ launcher: undefined });
  delete config.widget.launcher;
  const bubble = loadWidget({ fetch: fetchReturning(config) });
  bubble.widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-nbtab-bubble-size' });
  bubble.widget.init();
  const bubbleContainer = bubble.window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => bubbleContainer.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder, 'config loaded');
  const bubbleStyle = bubble.window.getComputedStyle(bubbleContainer.querySelector('.osa-chat-button'));
  assertEqual([bubbleStyle.width, bubbleStyle.height], ['56px', '56px'], 'a bubble\'s chat button stays 56px');
}

console.log('\nreduced motion: the stylesheet replaces every animation with a short fade');
{
  const { window } = await startCapsule();
  let reduced = null;
  for (const sheet of window.document.styleSheets) {
    for (const rule of sheet.cssRules) {
      if (rule.media && /\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)/.test(String(rule.media.mediaText))) reduced = rule;
    }
  }
  assert(!!reduced, 'there is a (prefers-reduced-motion: reduce) block');
  const selectors = reduced ? [...reduced.cssRules].map((r) => r.selectorText).join(' | ') : '';
  for (const selector of ['.osa-capsule .osa-view', '.osa-capsule-indicator', '.osa-notebook-bar', '.osa-notebook-ring']) {
    assert(selectors.includes(selector), `it covers ${selector}`);
  }
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed ? 1 : 0);
