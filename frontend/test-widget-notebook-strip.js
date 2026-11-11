/**
 * The strip of named notebooks in the notebook tab (#676), run against the real
 * widget source in a happy-dom window, the way test-widget-notebook-tab.js runs
 * the tab.
 *
 * What it holds the widget to: the strip is drawn from the notebook's `notebooks`
 * messages and nothing else, as a labelled group of buttons (not a tablist: it
 * also holds Rename, Close and the rename input, and there is no tab panel) with
 * aria-current on the open notebook, a roving tab stop, arrow keys, and focus
 * kept on its control when the list is redrawn; the open notebook's tab has Rename (an inline input:
 * Enter commits, Escape cancels) and Close, and the Playground has neither; the
 * "+" menu offers a blank notebook and the closed ones to reopen; each action
 * posts one message, to the notebook's origin only; and messages from another
 * origin or another window change nothing.
 *
 * What stands in: `fetch` (HTTP fixtures for the community config), and the
 * notebook's side of the bridge: messages dispatched as if from the frame, in
 * the shapes notebook/osa-bridge.js posts, and the frame's postMessage wrapped
 * to record what the widget sends it. What the page does with each request, in
 * real Chrome, is notebook/e2e-check.js's.
 *
 * Run with: bun frontend/test-widget-notebook-strip.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { NOTEBOOK_KINDS, TAB_REQUESTS } from '../notebook/osa-notebook-files.js';

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

function loadWidget({ fetch, reducedMotion = false }) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      navigation: { disableChildFrameNavigation: true },
      device: { prefersReducedMotion: reducedMotion ? 'reduce' : 'no-preference' },
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
async function startCapsule({ dataset = { id: 'nm000103', zarr: true }, widget: widgetOverrides, before, reducedMotion } = {}) {
  const config = configResponse(widgetOverrides);
  const { window, widget } = loadWidget({ fetch: fetchReturning(config), reducedMotion });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'nemar', storageKey: `osa-test-nbstrip-${configCounter}` });
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

// Every message type the widget sent a frame in this run, to hold against the page's own list.
const POSTED_TYPES = new Set();

// Record what the widget sends the notebook's frame.
function recordPosts(frame) {
  const posts = [];
  frame.contentWindow.postMessage = (message, targetOrigin) => {
    POSTED_TYPES.add(message.type);
    posts.push({ message, targetOrigin });
  };
  return posts;
}

const isOn = (view) => view.classList.contains('osa-view-on') && !view.hasAttribute('inert');
const isOff = (view) => view.classList.contains('osa-view-off') && view.hasAttribute('inert');

const DATASET = 'on002718';
const PLAYGROUND = `nemar/${DATASET}.ipynb`;
const pathOf = (title) => `nemar/${DATASET} · ${title}.ipynb`;
const item = (title, kind, open = true, extra = {}) => ({
  path: kind === 'playground' ? PLAYGROUND : pathOf(title),
  title: kind === 'playground' ? 'Playground' : title,
  kind,
  open,
  ...extra,
});

// A capsule on `on002718` with its notebook open, ready, and the bridge's side of it.
async function startNotebook(options = {}) {
  const started = await startCapsule({ dataset: { id: DATASET, zarr: true }, ...options });
  started.click('.osa-notebook-btn');
  const frame = started.q('.osa-notebook-frame');
  const posts = recordPosts(frame);
  const source = frame.contentWindow;
  const say = (data, extra = {}) => bridgeMessage(started.window, data, { source, ...extra });
  say({ type: 'ready' });
  const list = (items, active) => say({ type: 'notebooks', items, active });
  const requests = (type) => posts.filter((p) => p.message.type === type);
  const fire = (element, type, init = {}) => element.dispatchEvent(new started.window.Event(type, { bubbles: true, ...init }));
  const key = (element, name) => element.dispatchEvent(new started.window.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
  const tabs = () => [...started.container.querySelectorAll('.osa-nb-strip .osa-nb-tab-label')];
  const tabTitles = () => tabs().map((tab) => tab.textContent);
  return { ...started, frame, posts, source, say, list, requests, fire, key, tabs, tabTitles };
}

const ITEMS = [
  item('', 'playground'),
  item('ERPs', 'chat'),
  item('Spectra', 'chat'),
  item('Scratch', 'blank', false),
  item('Old run', 'chat', false),
];

console.log('='.repeat(60));
console.log('Widget: the strip of named notebooks (#676)');
console.log('='.repeat(60));

console.log('\nno strip until the notebook lists its notebooks');
{
  const { q, list, container } = await startNotebook();
  assert(!!q('.osa-nb-bar') && q('.osa-nb-bar').hidden, 'there is a strip, and it is hidden');
  assert(q('.osa-notebook-banner').hidden, 'and so is the banner');
  list(ITEMS, ITEMS[1].path);
  assert(!q('.osa-nb-bar').hidden, 'a list shows it');
  const bar = q('.osa-nb-bar');
  assert(q('.osa-notebook-banner').previousElementSibling === bar, 'the banner is directly under it');
  assert(q('.osa-notebook-banner').nextElementSibling === q('.osa-notebook-frame'), 'and the frame under the banner');
  assert(container.querySelector('.osa-view-notebook').firstElementChild === bar, 'the strip is the first thing in the notebook view');
  list([], null);
  assert(q('.osa-nb-bar').hidden, 'an empty list hides it again');
}

console.log('\nthe strip is a labelled group of buttons, from the notebooks message and nothing else');
{
  const { q, list, tabs, tabTitles, container } = await startNotebook();
  list(ITEMS, ITEMS[1].path);
  const strip = q('.osa-nb-strip');
  assertEqual(strip.getAttribute('role'), 'group', 'role=group: it holds more than tabs, so it is not a tablist');
  assertEqual(strip.getAttribute('aria-label'), 'Notebooks', 'named for assistive technology');
  assertEqual(tabTitles(), ['Playground', 'ERPs', 'Spectra'], 'the open notebooks are the tabs, in the order listed');
  assert(tabs().every((tab) => tab.tagName === 'BUTTON' && !tab.hasAttribute('role')), 'each is a plain button');
  assertEqual(strip.querySelectorAll('[role="tab"], [role="presentation"]').length, 0, 'and nothing in it claims to be a tab');
  assertEqual(tabs().map((tab) => tab.hasAttribute('aria-current')), [false, true, false], 'aria-current follows the active notebook');
  assertEqual(tabs().map((tab) => tab.tabIndex), [-1, 0, -1], 'only the selected tab is in the tab order');
  assertEqual(tabs().map((tab) => tab.dataset.path), ITEMS.slice(0, 3).map((i) => i.path), 'each tab carries its notebook\'s path');
  assert(tabs().every((tab) => tab.title === tab.textContent), 'a long title can be read in full from the tooltip');

  list(ITEMS, PLAYGROUND);
  assertEqual(tabs().map((tab) => tab.hasAttribute('aria-current')), [true, false, false], 'another active notebook moves the selection');
  assertEqual(tabs().map((tab) => tab.tabIndex), [0, -1, -1], 'and the tab stop');

  list([item('', 'playground'), item('Third', 'chat'), item('ERPs', 'chat')], pathOf('Third'));
  assertEqual(tabTitles(), ['Playground', 'Third', 'ERPs'], 'a notebook opened inside JupyterLite shows up too: the strip follows the list');
  list([item('', 'playground'), item('ERPs', 'chat')], PLAYGROUND);
  assertEqual(tabTitles(), ['Playground', 'ERPs'], 'and one that is gone goes');
  list(ITEMS, 'nemar/not-listed.ipynb');
  assertEqual(tabs().map((tab) => tab.hasAttribute('aria-current')), [false, false, false], 'an active path that is not listed selects nothing');
}

console.log('\nrename and close are on the open notebook\'s tab, and never on the Playground');
{
  const { q, list, container } = await startNotebook();
  list(ITEMS, ITEMS[1].path);
  const groups = [...container.querySelectorAll('.osa-nb-tab')];
  assertEqual(groups.map((g) => g.querySelectorAll('.osa-nb-action').length), [0, 2, 0], 'two actions on the open tab; none elsewhere');
  assertEqual(q('.osa-nb-rename-btn').getAttribute('aria-label'), 'Rename ERPs', 'Rename names its notebook');
  assertEqual(q('.osa-nb-close-btn').getAttribute('aria-label'), 'Close ERPs', 'and so does Close');
  list(ITEMS, PLAYGROUND);
  assertEqual(container.querySelectorAll('.osa-nb-action').length, 0, 'the Playground has neither: it is the starter, and it stays');
}

console.log('\nonly the notebook\'s own messages change the strip');
{
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => { warnings.push(args.map(String).join(' ')); };
  try {
    const { window, q, say, list, source, tabTitles } = await startNotebook();
    list(ITEMS, ITEMS[1].path);
    const notebooks = { source: 'osa-notebook', type: 'notebooks', items: [item('', 'playground')], active: PLAYGROUND };
    window.dispatchEvent(new window.MessageEvent('message', { data: notebooks, origin: 'https://evil.example', source }));
    window.dispatchEvent(new window.MessageEvent('message', { data: notebooks, origin: NOTEBOOK_ORIGIN, source: window }));
    window.dispatchEvent(new window.MessageEvent('message', { data: { ...notebooks, source: 'someone' }, origin: NOTEBOOK_ORIGIN, source }));
    assertEqual(tabTitles(), ['Playground', 'ERPs', 'Spectra'], 'another origin, another source and another sender change nothing');

    warnings.length = 0;
    say({ type: 'notebooks', items: 'nope', active: null });
    assertEqual(tabTitles(), ['Playground', 'ERPs', 'Spectra'], 'a list that is not a list is dropped');
    assert(warnings.some((w) => w.includes('not a list')), 'and the console hears of it');
    say({
      type: 'notebooks',
      active: PLAYGROUND,
      items: [
        item('', 'playground'),
        { path: 7, title: 'x', kind: 'chat', open: true },
        { path: pathOf('A'), title: 7, kind: 'chat', open: true },
        { path: pathOf('B'), title: 'B', kind: 'spreadsheet', open: true },
        { path: pathOf('C'), title: 'C', kind: 'chat', open: 'yes' },
        null,
        item('Good', 'chat')
      ]
    });
    assertEqual(tabTitles(), ['Playground', 'Good'], 'an item that is malformed is dropped, the rest stay');
    assert(!q('.osa-nb-strip').innerHTML.includes('<script'), 'and no title is ever markup');
    say({ type: 'notebooks', active: PLAYGROUND, items: [item('', 'playground'), { ...item('<img src=x onerror=alert(1)>', 'chat') }] });
    assertEqual(q('.osa-nb-strip img'), null, 'a title that is HTML stays text');
  } finally {
    console.warn = originalWarn;
  }
}

console.log('\nclicking a tab opens it; the open one does nothing');
{
  const { window, list, tabs, requests, say } = await startNotebook();
  list(ITEMS, ITEMS[1].path);
  tabs()[1].dispatchEvent(new window.Event('click', { bubbles: true }));
  assertEqual(requests('open-notebook').length, 0, 'the open tab: nothing posted');
  tabs()[2].dispatchEvent(new window.Event('click', { bubbles: true }));
  assertEqual(requests('open-notebook').length, 1, 'another tab: one request');
  const [sent] = requests('open-notebook');
  assertEqual(sent.message.path, ITEMS[2].path, 'for its path');
  assertEqual(sent.targetOrigin, NOTEBOOK_ORIGIN, 'to the notebook\'s origin only');
  assertEqual(sent.message.target, 'osa-notebook', 'for the notebook');
  assert(typeof sent.message.requestId === 'string' && sent.message.requestId !== '', 'under a request id');
  assertEqual(tabs()[1].hasAttribute('aria-current'), true, 'the selection moves only when the notebook says so');
  say({ type: 'result', requestId: sent.message.requestId, ok: true, path: ITEMS[2].path });
  list(ITEMS, ITEMS[2].path);
  assertEqual(tabs().map((tab) => tab.hasAttribute('aria-current')), [false, false, true], 'and then it does');
}

console.log('\narrow keys move between the tabs; Home and End go to the ends');
{
  const { window, list, tabs, key, requests } = await startNotebook();
  list(ITEMS, ITEMS[1].path);
  tabs()[1].focus();
  key(tabs()[1], 'ArrowRight');
  assert(window.document.activeElement === tabs()[2], 'ArrowRight: the next tab has focus');
  assertEqual(tabs().map((tab) => tab.tabIndex), [-1, -1, 0], 'and the tab stop');
  key(tabs()[2], 'ArrowRight');
  assert(window.document.activeElement === tabs()[0], 'ArrowRight at the end wraps to the first');
  key(tabs()[0], 'ArrowLeft');
  assert(window.document.activeElement === tabs()[2], 'ArrowLeft at the start wraps to the last');
  key(tabs()[2], 'ArrowLeft');
  assert(window.document.activeElement === tabs()[1], 'ArrowLeft: the one before');
  key(tabs()[1], 'Home');
  assert(window.document.activeElement === tabs()[0], 'Home: the first');
  key(tabs()[0], 'End');
  assert(window.document.activeElement === tabs()[2], 'End: the last');
  assertEqual(requests('open-notebook').length, 0, 'moving focus opens nothing: Enter or Space does, as on any button');
  const stop = new window.KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true });
  tabs()[2].dispatchEvent(stop);
  assert(!stop.defaultPrevented, 'other keys are left alone');
}

console.log('\nrename: Enter commits, Escape cancels, and the title is what the notebook is told');
{
  const { window, q, list, requests, key, fire, say, tabTitles } = await startNotebook();
  list(ITEMS, ITEMS[1].path);
  fire(q('.osa-nb-rename-btn'), 'click');
  const input = q('.osa-nb-rename');
  assert(!!input, 'Rename turns the title into an input');
  assertEqual(input.value, 'ERPs', 'with the title in it');
  assertEqual(input.getAttribute('aria-label'), 'Notebook name', 'named for assistive technology');
  assert(window.document.activeElement === input, 'and focus');
  assertEqual(tabTitles(), ['Playground', 'Spectra'], 'the tab being renamed is the input, not a tab');

  input.value = 'Face ERPs';
  key(input, 'Escape');
  assertEqual(requests('rename-notebook').length, 0, 'Escape: nothing posted');
  assertEqual(q('.osa-nb-rename'), null, 'and the input goes');
  assertEqual(tabTitles(), ['Playground', 'ERPs', 'Spectra'], 'the title is as it was');

  fire(q('.osa-nb-rename-btn'), 'click');
  q('.osa-nb-rename').value = 'Face ERPs';
  key(q('.osa-nb-rename'), 'Enter');
  assertEqual(requests('rename-notebook').length, 1, 'Enter: one request');
  const [sent] = requests('rename-notebook');
  assertEqual(sent.message.path, ITEMS[1].path, 'for the notebook\'s path');
  assertEqual(sent.message.title, 'Face ERPs', 'with the new title, which the notebook cleans and the file keeps its "<dataset> · " prefix for');
  assertEqual(sent.targetOrigin, NOTEBOOK_ORIGIN, 'to the notebook\'s origin only');
  assertEqual(q('.osa-nb-rename'), null, 'the input goes');
  assertEqual(tabTitles()[1], 'ERPs', 'the title changes only when the notebook lists the new name');
  say({ type: 'result', requestId: sent.message.requestId, ok: true, path: pathOf('Face ERPs') });
  list([ITEMS[0], item('Face ERPs', 'chat'), ITEMS[2]], pathOf('Face ERPs'));
  assertEqual(tabTitles(), ['Playground', 'Face ERPs', 'Spectra'], 'and then it does');

  fire(q('.osa-nb-rename-btn'), 'click');
  key(q('.osa-nb-rename'), 'Enter');
  assertEqual(requests('rename-notebook').length, 1, 'Enter with the title unchanged: nothing posted');
  fire(q('.osa-nb-rename-btn'), 'click');
  q('.osa-nb-rename').value = '   ';
  key(q('.osa-nb-rename'), 'Enter');
  assertEqual(requests('rename-notebook').length, 1, 'Enter with nothing typed: nothing posted');
  fire(q('.osa-nb-rename-btn'), 'click');
  q('.osa-nb-rename').value = '  Padded  ';
  q('.osa-nb-rename').dispatchEvent(new window.Event('blur'));
  assertEqual(requests('rename-notebook').at(-1).message.title, 'Padded', 'leaving the field commits it, trimmed');
  assertEqual(requests('rename-notebook').length, 2, 'once');
}

console.log('\nrename: a list arriving mid-edit keeps what was typed, and a notebook that goes ends the edit');
{
  const { q, list, requests, key, fire } = await startNotebook();
  list(ITEMS, ITEMS[1].path);
  fire(q('.osa-nb-rename-btn'), 'click');
  q('.osa-nb-rename').value = 'Half typed';
  list([...ITEMS.slice(0, 3), item('New', 'blank')], ITEMS[1].path);
  assertEqual(q('.osa-nb-rename').value, 'Half typed', 'a new list keeps the draft');
  assertEqual(requests('rename-notebook').length, 0, 'and the rebuild does not commit it');
  key(q('.osa-nb-rename'), 'Enter');
  assertEqual(requests('rename-notebook')[0].message.title, 'Half typed', 'Enter commits it');

  fire(q('.osa-nb-rename-btn'), 'click');
  list([ITEMS[0], ITEMS[2]], PLAYGROUND);
  assertEqual(q('.osa-nb-rename'), null, 'if the notebook being renamed is gone, the edit ends');
}

console.log('\nclose posts close-notebook for the open notebook');
{
  const { q, list, requests, fire, say } = await startNotebook();
  list(ITEMS, ITEMS[2].path);
  fire(q('.osa-nb-close-btn'), 'click');
  const [sent] = requests('close-notebook');
  assertEqual(sent.message.path, ITEMS[2].path, 'for its path');
  assertEqual(sent.targetOrigin, NOTEBOOK_ORIGIN, 'to the notebook\'s origin only');
  say({ type: 'result', requestId: sent.message.requestId, ok: true, path: ITEMS[2].path });
  list([ITEMS[0], ITEMS[1], { ...ITEMS[2], open: false }, ...ITEMS.slice(3)], ITEMS[1].path);
  assertEqual(q('.osa-nb-bar').querySelectorAll('.osa-nb-tab-label').length, 2, 'once the notebook lists it closed, its tab is gone');
}

console.log('\nthe "+" menu: a blank notebook, and the closed ones to reopen');
{
  const { window, q, list, requests, fire, key, container } = await startNotebook();
  list(ITEMS, ITEMS[1].path);
  const add = q('.osa-nb-add');
  assertEqual(add.getAttribute('aria-label'), 'New notebook', 'the button is named');
  assertEqual(add.getAttribute('aria-haspopup'), 'menu', 'it opens a menu');
  assertEqual(add.getAttribute('aria-expanded'), 'false', 'closed to begin with');
  assert(q('.osa-nb-menu').hidden, 'the menu is hidden');

  fire(add, 'click');
  assertEqual(add.getAttribute('aria-expanded'), 'true', 'a click opens it');
  assertEqual(q('.osa-nb-menu').getAttribute('role'), 'menu', 'role=menu');
  const entries = [...container.querySelectorAll('.osa-nb-menu [role="menuitem"]')].map((e) => e.textContent);
  assertEqual(entries, ['Blank notebook', 'Scratch', 'Old run'], 'a blank notebook, then the closed ones to reopen');
  assertEqual(q('.osa-nb-menu-heading').textContent, 'Reopen', 'under a Reopen heading');
  assert(window.document.activeElement === q('.osa-nb-menu-item'), 'the first entry has focus');

  key(window.document.activeElement, 'ArrowDown');
  assert(window.document.activeElement.textContent === 'Scratch', 'ArrowDown: the next entry');
  key(window.document.activeElement, 'ArrowUp');
  key(window.document.activeElement, 'ArrowUp');
  assert(window.document.activeElement.textContent === 'Old run', 'ArrowUp wraps');

  key(window.document.activeElement, 'Escape');
  assert(q('.osa-nb-menu').hidden && add.getAttribute('aria-expanded') === 'false', 'Escape closes it');
  assert(window.document.activeElement === add, 'and puts focus back on the button');

  fire(add, 'click');
  fire(q('.osa-nb-menu-item'), 'click');
  assertEqual(requests('new-notebook').length, 1, 'Blank notebook posts new-notebook');
  assertEqual(requests('new-notebook')[0].targetOrigin, NOTEBOOK_ORIGIN, 'to the notebook\'s origin only');
  assert(q('.osa-nb-menu').hidden, 'and the menu closes');

  fire(add, 'click');
  const reopen = [...container.querySelectorAll('.osa-nb-menu-item')].find((e) => e.textContent === 'Old run');
  fire(reopen, 'click');
  assertEqual(requests('open-notebook').length, 1, 'a closed notebook posts open-notebook');
  assertEqual(requests('open-notebook')[0].message.path, ITEMS[4].path, 'for its path');

  fire(add, 'click');
  fire(q('.osa-nb-strip'), 'click');
  assert(q('.osa-nb-menu').hidden, 'a click elsewhere in the notebook view closes the menu');
  fire(add, 'click');
  fire(add, 'click');
  assert(q('.osa-nb-menu').hidden, 'a second click on the button closes it');

  list(ITEMS.slice(0, 3), ITEMS[1].path);
  fire(add, 'click');
  assertEqual([...container.querySelectorAll('.osa-nb-menu-item')].map((e) => e.textContent), ['Blank notebook'], 'with nothing closed, only Blank notebook');
  assertEqual(q('.osa-nb-menu-heading'), null, 'and no heading');
}

console.log('\nthe strip goes with its frame');
{
  const { q, list, widget, window } = await startNotebook();
  list(ITEMS, ITEMS[1].path);
  assert(!q('.osa-nb-bar').hidden, 'set up: a strip');
  widget.setDataset({ id: 'on000001', zarr: true });
  assert(q('.osa-nb-bar').hidden, 'another dataset: the strip is gone with the notebook that listed it');
  assertEqual(window.OSAChatWidget.__notebook.state().tabs, { items: [], active: null }, 'and so is what it held');
}

console.log('\nstyles: every color is a token the dark appearance overrides, so light and dark both read');
{
  const rules = [...SOURCE.matchAll(/\n    (\.osa-(?:nb-|notebook-banner)[^{]*)\{([^}]*)\}/g)];
  assert(rules.length >= 15, `the strip and banner have their own rules (${rules.length})`);
  const tokens = new Set();
  let literal = null;
  for (const [, selector, body] of rules) {
    if (/#[0-9a-fA-F]{3,8}\b|rgba?\(/.test(body.replace(/box-shadow:[^;]*;/, ''))) literal = selector.trim();
    for (const [, token] of body.matchAll(/var\((--osa-[a-z-]+)/g)) tokens.add(token);
  }
  assertEqual(literal, null, 'no hex or rgb color in them');
  const dark = SOURCE.slice(SOURCE.indexOf('.osa-chat-widget.osa-dark {'));
  const darkBlock = dark.slice(0, dark.indexOf('}'));
  const light = SOURCE.slice(SOURCE.indexOf('--osa-primary: #2563eb;'), SOURCE.indexOf('--osa-primary: #2563eb;') + 3000);
  for (const token of tokens) {
    const defined = light.includes(`${token}:`);
    const redefined = darkBlock.includes(`${token}:`);
    assert(defined, `${token} is defined for light`);
    // The accent, text on the primary color and the primary itself are the community's own, one per scheme.
    if (!['--osa-primary', '--osa-on-primary', '--osa-shadow'].includes(token)) {
      assert(redefined || token === '--osa-accent', `${token} is redefined for dark`);
    }
  }
}

console.log('\nfocus stays on its control when the list is redrawn');
{
  const { window, q, list, key, fire, tabs } = await startNotebook();
  const active = () => window.document.activeElement;
  list(ITEMS, ITEMS[1].path);

  // A keyboard user arrows to another tab and presses Enter: the notebook answers with a new list.
  tabs()[1].focus();
  key(tabs()[1], 'ArrowRight');
  assert(active() === tabs()[2], 'set up: focus is on the third tab');
  list(ITEMS, ITEMS[2].path);
  assert(active() === tabs()[2] && active().dataset.path === ITEMS[2].path, 'the list moved the selection: focus is still on that tab');
  assertEqual(tabs().map((tab) => tab.tabIndex), [-1, -1, 0], 'and the tab stop is on it');
  list(ITEMS, ITEMS[2].path);
  assert(active() === tabs()[2], 'a redraw with nothing new changes nothing');

  // Rename and Close: focus stays on the button while its notebook is the open one ...
  list(ITEMS, ITEMS[1].path);
  q('.osa-nb-rename-btn').focus();
  list(ITEMS, ITEMS[1].path);
  assert(active() === q('.osa-nb-rename-btn'), 'Rename keeps focus across a redraw');
  q('.osa-nb-close-btn').focus();
  list(ITEMS, ITEMS[1].path);
  assert(active() === q('.osa-nb-close-btn'), 'and so does Close');
  // ... and goes to the notebook now on screen when it is closed.
  list([ITEMS[0], { ...ITEMS[1], open: false }, ITEMS[2], ...ITEMS.slice(3)], ITEMS[2].path);
  assert(active() === tabs()[1] && active().dataset.path === ITEMS[2].path, 'Close: focus goes to the tab now open');

  // A tab that goes while it has focus hands it to the open one.
  tabs()[0].focus();
  list([ITEMS[1], ITEMS[2]], ITEMS[2].path);
  assert(active() === tabs()[1], 'a focused tab that is gone: focus goes to the open tab, not the page');

  // Rename: Enter commits and focus returns to the tab.
  list(ITEMS, ITEMS[1].path);
  fire(q('.osa-nb-rename-btn'), 'click');
  const input = q('.osa-nb-rename');
  assert(active() === input, 'set up: the rename input has focus');
  input.value = 'Face ERPs';
  key(input, 'Enter');
  assert(active() === tabs().find((tab) => tab.dataset.path === ITEMS[1].path), 'after Enter, focus is on the notebook\'s tab');
  list([ITEMS[0], item('Face ERPs', 'chat'), ITEMS[2]], pathOf('Face ERPs'));
  assert(active() === tabs()[1] && active().textContent === 'Face ERPs', 'and on the renamed tab once the list arrives');
}

console.log('\nthe "+" menu: choosing an entry returns focus to the button, and the menu closes when focus leaves');
{
  const { window, q, list, requests, fire, container } = await startNotebook();
  list(ITEMS, ITEMS[1].path);
  const add = q('.osa-nb-add');
  fire(add, 'click');
  fire(q('.osa-nb-menu-item'), 'click');
  assertEqual(requests('new-notebook').length, 1, 'set up: Blank notebook was chosen');
  assert(q('.osa-nb-menu').hidden, 'the menu closed');
  assert(window.document.activeElement === add, 'and focus is on the "+" button, not lost to the page');
  list(ITEMS, ITEMS[2].path);
  assert(window.document.activeElement === add, 'a list that arrives next does not take it');

  // Focus moves to another control in the widget: the menu goes.
  fire(add, 'click');
  assert(!q('.osa-nb-menu').hidden, 'set up: open');
  const outside = container.querySelector('.osa-chat-input input');
  q('.osa-nb-menu-item').dispatchEvent(new window.FocusEvent('focusout', { bubbles: true, relatedTarget: outside }));
  assert(q('.osa-nb-menu').hidden, 'focus moving to something outside the menu closes it');
  fire(add, 'click');
  q('.osa-nb-menu-item').dispatchEvent(new window.FocusEvent('focusout', { bubbles: true, relatedTarget: q('.osa-nb-menu') }));
  assert(!q('.osa-nb-menu').hidden, 'focus staying inside it does not');
  q('.osa-nb-menu-item').dispatchEvent(new window.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
  assert(!q('.osa-nb-menu').hidden, 'and an unknown target is left to the click handler: some browsers do not focus a button on click');

  // A click in the cross-origin frame is no event in this document, but it blurs this window.
  window.dispatchEvent(new window.Event('blur'));
  assert(q('.osa-nb-menu').hidden, 'the window losing focus (a click in the frame) closes it');
  assertEqual(add.getAttribute('aria-expanded'), 'false', 'and the button says so');
}

console.log('\nstyles: tab labels read at AA contrast on the strip, in light and dark');
{
  const luminance = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a, b) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const label = SOURCE.match(/\n    \.osa-nb-tab-label \{([^}]*)\}/)[1];
  const labelToken = label.match(/color:\s*var\((--osa-[a-z-]+)\)/)[1];
  const stripToken = SOURCE.match(/\n    \.osa-nb-bar \{([^}]*)\}/)[1].match(/background:\s*var\((--osa-[a-z-]+)\)/)[1];
  const darkStart = SOURCE.indexOf('.osa-chat-widget.osa-dark {');
  const lightStart = SOURCE.indexOf('--osa-primary: #2563eb;');
  const valueIn = (from, to, token) => SOURCE.slice(from, to).match(new RegExp(`${token}:\\s*(#[0-9a-fA-F]{6})`))?.[1];
  for (const [scheme, from, to] of [['light', lightStart, lightStart + 3000], ['dark', darkStart, darkStart + 3000]]) {
    const text = valueIn(from, to, labelToken);
    const ground = valueIn(from, to, stripToken);
    assert(!!text && !!ground, `${scheme}: ${labelToken} and ${stripToken} are plain colors here`);
    if (text && ground) {
      const ratio = contrast(text, ground);
      assert(ratio >= 4.5, `${scheme}: tab labels (${labelToken} ${text} on ${stripToken} ${ground}) read at ${ratio.toFixed(1)}:1, at least 4.5:1`);
    }
  }
}

console.log('\nthe widget and the notebook page agree on kinds and request types');
{
  const { q, list, tabs } = await startNotebook();
  const all = NOTEBOOK_KINDS.map((kind, index) => ({
    path: kind === 'playground' ? PLAYGROUND : pathOf(`Kind ${index}`),
    title: kind === 'playground' ? 'Playground' : `Kind ${index}`,
    kind,
    open: true,
  }));
  list(all, all[0].path);
  assertEqual(tabs().length, NOTEBOOK_KINDS.length, `every kind the page lists (${NOTEBOOK_KINDS.join(', ')}) is drawn as a tab`);
  list([{ ...all[0], kind: 'not-a-kind' }], null);
  assertEqual(tabs().length, 0, 'and a kind the page does not define is not');
  assert(!q('.osa-nb-bar').hidden || tabs().length === 0, 'with nothing drawn for it');
  const unknown = [...POSTED_TYPES].filter((type) => !TAB_REQUESTS.includes(type) && type !== 'theme');
  assertEqual(unknown, [], 'every message type the widget posted in this run is one the page validates');
  assert(['open-notebook', 'close-notebook', 'rename-notebook', 'new-notebook'].every((type) => POSTED_TYPES.has(type)), 'and the run did post the strip\'s own');
  for (const type of TAB_REQUESTS) assert(SOURCE.includes(`'${type}'`), `the widget knows ${type}, which the page validates`);
}

console.log(`\n${'='.repeat(60)}`);
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
