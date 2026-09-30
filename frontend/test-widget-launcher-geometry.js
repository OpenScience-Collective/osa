/**
 * The launcher's position, size and offsets (#553), run against the real widget
 * source in a happy-dom window, the way test-widget-capsule.js runs the capsule.
 *
 * What stands in: `fetch`, answering with HTTP fixtures for the community config
 * endpoint (never a mock of the widget's own logic). happy-dom substitutes var() but
 * leaves calc() alone, so a length is read through cssNumber, which evaluates it to
 * the number a browser would draw. What a browser really draws, frame by frame, is
 * frontend/browser-harness/launcher-geometry-check.mjs's.
 *
 * Run with: bun frontend/test-widget-launcher-geometry.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { cssNumber, cssNumbers, withVars } from './test-support/css-px.js';

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

function noNetwork(url) {
  return Promise.reject(new Error(`unexpected request in a unit test: ${url}`));
}

function configResponse(widgetOverrides = {}) {
  return {
    default_model: 'm',
    offered_models: [],
    widget: { title: 'Test Assistant', ...widgetOverrides },
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

function loadWidget({ fetch = noNetwork, innerWidth, innerHeight, reducedMotion = false } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    ...(innerWidth !== undefined ? { innerWidth } : {}),
    ...(innerHeight !== undefined ? { innerHeight } : {}),
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

let storageCounter = 0;

/**
 * A widget whose community config is `widget`, initialized and past its first-visit
 * wait, so what the stylesheet says is what is on the page. `page` is what an
 * embedding page sets with setConfig. Warnings are collected, not printed.
 */
async function start(widget = {}, {
  page = {}, innerWidth, innerHeight = 800, beforeInit, fetch, reducedMotion,
} = {}) {
  const { window, widget: api } = loadWidget({
    fetch: fetch || fetchReturning(configResponse(widget)), innerWidth, innerHeight, reducedMotion,
  });
  storageCounter += 1;
  // From before setConfig, which checks what a page passes and warns about a bad value.
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args.map(String).join(' '));
  try {
    api.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: `osa-test-geometry-${storageCounter}`, ...page });
    if (beforeInit) beforeInit(api);
    api.init();
    const container = window.document.querySelector('.osa-chat-widget');
    await waitUntil(() => !container.classList.contains('osa-launcher-waiting'), 'the config arrives');
    if (widget.launcher === 'capsule' && !page.fullscreen) {
      await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'the capsule is built');
    }
    return { window, api, container, warnings, q: (selector) => container.querySelector(selector) };
  } finally {
    console.warn = originalWarn;
  }
}

const px = (window, element, property) => cssNumber(window.getComputedStyle(element)[property]);
const inline = (container, name) => container.style.getPropertyValue(name);
const GEOMETRY_PROPERTIES = [
  '--osa-size-closed', '--osa-size-open', '--osa-closed-scale',
  '--osa-edge-x', '--osa-edge-y', '--osa-edge-x-narrow', '--osa-edge-y-narrow',
];

// The capsule's circles in DOM order, which is the Tab order.
const capsuleOrder = (capsule) => [...capsule.children]
  .map((el) => (el.classList.contains('osa-chat-button') ? 'chat' : el.classList.contains('osa-notebook-btn') ? 'notebook' : el.classList.contains('osa-hpc-btn') ? 'hpc' : null))
  .filter(Boolean);

console.log('='.repeat(60));
console.log('Widget: launcher position, size and offsets (#553)');
console.log('='.repeat(60));

console.log('\nnothing configured: nothing is added, so the widget is drawn as it always was');
for (const launcher of ['bubble', 'capsule']) {
  const { container } = await start(launcher === 'capsule' ? { launcher } : {});
  assertEqual(GEOMETRY_PROPERTIES.filter((name) => inline(container, name) !== ''), [], `${launcher}: no geometry property is set inline`);
  assert(!container.classList.contains('osa-pos-left'), `${launcher}: not on the left`);
  assert(!container.classList.contains('osa-launcher-resizes'), `${launcher}: no grow-and-shrink class`);
}
{
  const { window, q } = await start();
  const button = q('.osa-chat-button');
  assertEqual([px(window, button, 'right'), px(window, button, 'bottom'), px(window, button, 'width')], [20, 20, 56], 'bubble: 56px, 20px from the right and bottom');
  assertEqual(window.getComputedStyle(button).left, '', 'bubble: no left edge of its own');
  assert(!window.getComputedStyle(button).scale, 'bubble: not drawn larger than its box');
}

console.log('\nlauncher_position: bottom-left moves the launcher, its label and the panel to the other side');
{
  const { window, container, q } = await start({ launcher_position: 'bottom-left' });
  assert(container.classList.contains('osa-pos-left'), 'the widget is marked as anchored on the left');
  const button = window.getComputedStyle(q('.osa-chat-button'));
  assertEqual([px(window, q('.osa-chat-button'), 'left'), px(window, q('.osa-chat-button'), 'bottom')], [20, 20], 'the button is 20px from the left and bottom');
  assertEqual(button.right, 'auto', 'and has let go of the right edge');
  const tooltip = window.getComputedStyle(q('.osa-chat-tooltip'));
  assertEqual([cssNumber(tooltip.left), tooltip.right, cssNumber(tooltip.bottom)], [86, 'auto', 28], "the label is 10px to the button's right, centered on it");
  const panel = window.getComputedStyle(q('.osa-chat-window'));
  assertEqual([cssNumber(panel.left), panel.right, cssNumber(panel.bottom)], [20, 'auto', 90], 'the panel opens above the button on the left edge');
  assertEqual(cssNumber(panel.maxWidth), 1024 - 20 - 20, 'and is no wider than the space to the right of it, less the 20px margin');
  const handle = window.getComputedStyle(q('.osa-resize-handle'));
  assertEqual([handle.left, cssNumber(handle.right), handle.cursor], ['auto', 0, 'nesw-resize'], 'its resize handle is the top-right corner');
}

console.log('\nbottom-right written out is the default, not a change');
{
  const { container } = await start({ launcher_position: 'bottom-right' });
  assert(!container.classList.contains('osa-pos-left'), 'not marked as on the left');
}

console.log('\nlauncher_size: a bubble drawn at its closed size, shrinking to 80% while the panel is open');
{
  const { window, container, q } = await start({ launcher_size: 70 });
  assertEqual(inline(container, '--osa-size-closed'), '70px', 'the closed size');
  assertEqual(inline(container, '--osa-size-open'), '56px', 'the open size is 80% of it, rounded: 56px');
  assertEqual(Number(inline(container, '--osa-closed-scale')), 70 / 56, 'drawn at 70/56 of its box while closed');
  assert(container.classList.contains('osa-launcher-resizes'), 'a bubble that shrinks is marked, to be drawn larger at rest');
  const button = window.getComputedStyle(q('.osa-chat-button'));
  assertEqual([cssNumber(button.width), cssNumber(button.height)], [56, 56], 'its box is the open size, so nothing reflows as it changes');
  assert(Math.abs(cssNumber(button.scale) - 70 / 56) < 1e-9, 'and it is drawn at 70px while closed');
  const grow = 56 * (70 / 56 - 1) / 2;
  const shift = cssNumbers(button.translate);
  assert(shift.length === 2 && shift.every((v) => Math.abs(v + grow) < 1e-9), `with its bottom-right corner held still (translate -${grow}px both ways)`);
  const tooltip = window.getComputedStyle(q('.osa-chat-tooltip'));
  assertEqual([cssNumber(tooltip.right), cssNumber(tooltip.bottom)], [20 + 70 + 10, 20 + (70 - 40) / 2], 'the label sits beside the circle as it is drawn closed');
  assertEqual(cssNumber(window.getComputedStyle(q('.osa-chat-window')).bottom), 20 + 56 + 14, 'the panel sits above the open size');
}

console.log('\nlauncher_open_size: both sizes set');
{
  const { window, container, q } = await start({ launcher_size: 64, launcher_open_size: 50 });
  assertEqual([inline(container, '--osa-size-closed'), inline(container, '--osa-size-open')], ['64px', '50px'], 'each is used as given');
  assertEqual(px(window, q('.osa-chat-button'), 'width'), 50, 'the box is the open size');
  assertEqual(cssNumber(window.getComputedStyle(q('.osa-chat-window')).bottom), 20 + 50 + 14, 'and the panel follows it');
}

console.log('\nequal sizes: no shrink, so a bubble is not marked and not drawn larger');
{
  const { window, container, q } = await start({ launcher_size: 60, launcher_open_size: 60 });
  assert(!container.classList.contains('osa-launcher-resizes'), 'not marked');
  assert(!window.getComputedStyle(q('.osa-chat-button')).scale, 'not scaled');
  assertEqual(px(window, q('.osa-chat-button'), 'width'), 60, 'a 60px button');
}

console.log('\nan open size alone sets the open size, from the launcher\'s own closed size');
{
  const { window, container, q } = await start({ launcher_open_size: 48 });
  assertEqual([inline(container, '--osa-size-closed'), inline(container, '--osa-size-open')], ['56px', '48px'], 'a bubble closes at its 56px');
  assert(container.classList.contains('osa-launcher-resizes'), 'and now shrinks to 48px');
  assertEqual(px(window, q('.osa-chat-button'), 'width'), 48, 'the box is 48px');
}
{
  const { container } = await start({ launcher: 'capsule', launcher_open_size: 50 });
  assertEqual([inline(container, '--osa-size-closed'), inline(container, '--osa-size-open')], ['58px', '50px'], 'a capsule closes at its 58px');
}

console.log('\nthe smallest launcher: 44px is the floor, and 80% of it is not below it');
{
  const { container } = await start({ launcher_size: 50 });
  assertEqual(inline(container, '--osa-size-open'), '44px', '80% of 50 is 40, held at the 44px floor');
  const small = await start({ launcher_size: 44 });
  assertEqual([inline(small.container, '--osa-size-closed'), inline(small.container, '--osa-size-open')], ['44px', '44px'], '44 closes and opens at 44, and does not shrink');
  assert(!small.container.classList.contains('osa-launcher-resizes'), 'so it is not marked');
}

console.log('\nthe capsule: sizes, the panel and the label follow, and 58/46 written out is the default');
{
  const defaults = await start({ launcher: 'capsule' });
  const explicit = await start({ launcher: 'capsule', launcher_size: 58, launcher_open_size: 46 });
  const measure = ({ window, q }) => {
    const button = window.getComputedStyle(q('.osa-launcher-capsule .osa-chat-button'));
    const panel = window.getComputedStyle(q('.osa-chat-window'));
    const tooltip = window.getComputedStyle(q('.osa-chat-tooltip'));
    return {
      box: [cssNumber(button.width), cssNumber(button.height)],
      scale: cssNumber(button.scale),
      shift: cssNumbers(button.translate),
      panel: [cssNumber(panel.right), cssNumber(panel.bottom), cssNumber(panel.maxWidth), cssNumber(panel.maxHeight)],
      tooltip: [cssNumber(tooltip.right), cssNumber(tooltip.bottom)],
      indicator: px(window, q('.osa-capsule-indicator'), 'width'),
      icons: [px(window, q('.osa-notebook-btn'), 'width'), px(window, q('.osa-hpc-btn'), 'width')],
    };
  };
  const a = measure(defaults);
  const b = measure(explicit);
  assertEqual(b.box, a.box, 'the box is the same');
  assert(a.shift.length === 2 && b.shift.length === 2, 'both are drawn with a translate at rest');
  assert(Math.abs(b.scale - a.scale) < 1e-9 && b.shift.every((v, i) => Math.abs(v - a.shift[i]) < 1e-9), 'drawn the same at rest');
  assertEqual(b.panel, a.panel, 'the panel is in the same place');
  assertEqual(b.tooltip, a.tooltip, 'and so is the label');
  assertEqual([a.box, a.panel, a.tooltip, a.indicator, a.icons], [[46, 46], [85, 20, 1024 - 85 - 20, 750], [88, 29], 46, [46, 46]], 'and both are today\'s numbers');
}
{
  const { window, container, q } = await start({ launcher: 'capsule', launcher_size: 80 });
  assertEqual([inline(container, '--osa-size-closed'), inline(container, '--osa-size-open')], ['80px', '64px'], 'a capsule of 80px opens at 64px');
  assert(!container.classList.contains('osa-launcher-resizes'), 'the capsule always grows and shrinks; its own rules do it, not the bubble\'s class');
  assertEqual([px(window, q('.osa-launcher-capsule .osa-chat-button'), 'width'), px(window, q('.osa-notebook-btn'), 'width'), px(window, q('.osa-hpc-btn'), 'width'), px(window, q('.osa-capsule-indicator'), 'width')], [64, 64, 64, 64], 'every circle, and the indicator behind the open tab, is the open size');
  const panel = window.getComputedStyle(q('.osa-chat-window'));
  assertEqual([cssNumber(panel.right), cssNumber(panel.bottom)], [20 + 64 + 19, 20], 'the panel opens beside the wider capsule with the same gaps');
  const tooltip = window.getComputedStyle(q('.osa-chat-tooltip'));
  assertEqual([cssNumber(tooltip.right), cssNumber(tooltip.bottom)], [20 + 80 + 10, 20 + (80 - 40) / 2], 'and the label beside the closed circle');
  const svg = window.getComputedStyle(q('.osa-notebook-btn svg'));
  assert(Math.abs(cssNumber(svg.width) - 64 * 21 / 46) < 1e-9, 'icons scale with the circle');
}

console.log('\nthe capsule on the left: the pill and panel grow to the right, and the corner held still is the left one');
{
  const wide = await start({ launcher: 'capsule', launcher_position: 'bottom-left' }, { innerWidth: 1024 });
  const { window, container, q } = wide;
  assert(container.classList.contains('osa-pos-left'), 'anchored on the left');
  const capsule = window.getComputedStyle(q('.osa-launcher-capsule'));
  assertEqual([cssNumber(capsule.left), capsule.right, cssNumber(capsule.bottom), capsule.flexDirection], [20, 'auto', 20, 'column'], 'the capsule is on the left edge and, on a desktop, a column');
  const panel = window.getComputedStyle(q('.osa-chat-window'));
  assertEqual([cssNumber(panel.left), panel.right, cssNumber(panel.bottom)], [85, 'auto', 20], 'the panel opens to the capsule\'s right');
  const button = window.getComputedStyle(q('.osa-launcher-capsule .osa-chat-button'));
  const leftShift = cssNumbers(button.translate);
  assert(leftShift.length === 2 && leftShift[0] > 0 && leftShift[1] < 0, 'the resting circle grows up and to the right, so its bottom-left corner holds still');
  const tooltip = window.getComputedStyle(q('.osa-icon-tooltip'));
  // happy-dom cannot resolve a percentage, so this rule, which is relative to its own icon, is pinned by its spelling.
  assertEqual([tooltip.left, tooltip.right], ['calc(100% + 12px)', 'auto'], 'an icon\'s tooltip opens to its right');
}
{
  const narrow = await start({ launcher: 'capsule', launcher_position: 'bottom-left' }, { innerWidth: 390 });
  const capsule = narrow.window.getComputedStyle(narrow.q('.osa-launcher-capsule'));
  assertEqual(capsule.flexDirection, 'row', 'on a phone the capsule is a row');
  assertEqual(capsuleOrder(narrow.q('.osa-launcher-capsule')), ['chat', 'notebook', 'hpc'], 'with the chat circle first, on the anchor, and the others growing to its right');
  const panel = narrow.window.getComputedStyle(narrow.q('.osa-chat-window'));
  assertEqual([cssNumber(panel.left), panel.right, cssNumber(panel.bottom)], [20, 'auto', 90], 'and the panel opens above it on the left edge');
}

console.log('\noffsets: how far from the side and the bottom edge');
{
  const { window, q } = await start({ launcher_offset_x: 32, launcher_offset_y: 96 });
  const button = q('.osa-chat-button');
  assertEqual([px(window, button, 'right'), px(window, button, 'bottom')], [32, 96], 'the button is 32px from the right and 96px up');
  const panel = window.getComputedStyle(q('.osa-chat-window'));
  assertEqual([cssNumber(panel.right), cssNumber(panel.bottom)], [32, 96 + 56 + 14], 'the panel keeps the same gap above it');
  assertEqual(cssNumber(panel.maxHeight), 800 - 96 - 56 - 14 - 30, 'and stops 30px short of the top of the window');
  assertEqual(cssNumber(panel.maxWidth), 1024 - 32 - 20, 'and 20px short of the far side');
  const tooltip = window.getComputedStyle(q('.osa-chat-tooltip'));
  assertEqual([cssNumber(tooltip.right), cssNumber(tooltip.bottom)], [32 + 56 + 10, 96 + 8], 'the label follows');
}
{
  const { window, q } = await start({ launcher_position: 'bottom-left', launcher_offset_x: 12, launcher_offset_y: 0 });
  const button = q('.osa-chat-button');
  assertEqual([px(window, button, 'left'), px(window, button, 'bottom')], [12, 0], 'on the left, 12px from the left edge, and 0 is flush with the bottom');
}
{
  const { window, q } = await start({ launcher: 'capsule', launcher_offset_x: 40, launcher_offset_y: 60 });
  const capsule = q('.osa-launcher-capsule');
  assertEqual([px(window, capsule, 'right'), px(window, capsule, 'bottom')], [40, 60], 'the capsule moves with the offsets');
  const panel = window.getComputedStyle(q('.osa-chat-window'));
  assertEqual([cssNumber(panel.right), cssNumber(panel.bottom)], [40 + 46 + 19, 60], 'and its panel sits beside it');
}

console.log('\nmobile offsets: replace the offsets at 600px wide and narrower, each falling back to its own');
{
  const config = { launcher_offset_x: 32, launcher_offset_y: 24, launcher_mobile_offset_y: 120 };
  const phone = await start(config, { innerWidth: 390 });
  const button = phone.q('.osa-chat-button');
  assertEqual([px(phone.window, button, 'right'), px(phone.window, button, 'bottom')], [32, 120], 'a phone: the mobile bottom offset, and the desktop side offset it has no mobile value for');
  assertEqual(cssNumber(phone.window.getComputedStyle(phone.q('.osa-chat-window')).bottom), 120 + 56 + 14, 'the panel follows it');
  const edge = await start(config, { innerWidth: 600 });
  assertEqual(px(edge.window, edge.q('.osa-chat-button'), 'bottom'), 120, '600px wide still counts as a phone');
  const desktop = await start(config, { innerWidth: 601 });
  assertEqual([px(desktop.window, desktop.q('.osa-chat-button'), 'right'), px(desktop.window, desktop.q('.osa-chat-button'), 'bottom')], [32, 24], '601px is a desktop: the desktop offsets');
}
{
  const phone = await start({ launcher_mobile_offset_x: 8, launcher_mobile_offset_y: 0 }, { innerWidth: 390 });
  assertEqual([px(phone.window, phone.q('.osa-chat-button'), 'right'), px(phone.window, phone.q('.osa-chat-button'), 'bottom')], [8, 0], 'mobile offsets alone move a phone from today\'s 20px, and 0 is a value');
  const desktop = await start({ launcher_mobile_offset_x: 8, launcher_mobile_offset_y: 0 }, { innerWidth: 1024 });
  assertEqual([px(desktop.window, desktop.q('.osa-chat-button'), 'right'), px(desktop.window, desktop.q('.osa-chat-button'), 'bottom')], [20, 20], 'and leave a desktop alone');
}

console.log('\nthe page outranks the community: setConfig keys are the embedder\'s');
{
  const { window, container, q } = await start(
    { launcher_position: 'bottom-right', launcher_size: 70 },
    { page: { launcherPosition: 'bottom-left', launcherSize: 60 } }
  );
  assert(container.classList.contains('osa-pos-left'), 'the page\'s position wins');
  assertEqual(inline(container, '--osa-size-closed'), '60px', 'and its size');
  assertEqual(px(window, q('.osa-chat-button'), 'left'), 20, 'the button is on the left');
}
{
  const { window, api, container, q } = await start({});
  assert(!container.classList.contains('osa-pos-left'), 'sanity: on the right');
  api.setConfig({ launcherPosition: 'bottom-left', launcherOffsetY: 48, launcherSize: 72 });
  assert(container.classList.contains('osa-pos-left'), 'setConfig after init() moves it at once');
  assertEqual([px(window, q('.osa-chat-button'), 'left'), px(window, q('.osa-chat-button'), 'bottom')], [20, 48], 'to where it says');
  assert(container.classList.contains('osa-launcher-resizes'), 'and resizes it');
  api.setConfig({ launcherPosition: 'bottom-right', launcherOffsetY: null, launcherSize: null });
  assert(!container.classList.contains('osa-pos-left') && !container.classList.contains('osa-launcher-resizes'), 'clearing a value puts it back');
  assertEqual(GEOMETRY_PROPERTIES.filter((name) => inline(container, name) !== ''), [], 'with nothing left inline');
}

console.log('\na value the server would refuse is ignored, once, with a warning');
{
  const { container, warnings } = await start({}, {
    page: { launcherSize: 30, launcherOpenSize: 200, launcherOffsetX: -5, launcherOffsetY: 60.5, launcherMobileOffsetX: '8', launcherPosition: 'top-right' },
  });
  assertEqual(GEOMETRY_PROPERTIES.filter((name) => inline(container, name) !== ''), [], 'none of them is applied');
  assert(!container.classList.contains('osa-pos-left'), 'and the launcher stays where it was');
  for (const field of ['launcherSize', 'launcherOpenSize', 'launcherOffsetX', 'launcherOffsetY', 'launcherMobileOffsetX', 'launcherPosition']) {
    assertEqual(warnings.filter((w) => w.includes(field)).length, 1, `${field}: one warning, naming it`);
  }
}
{
  const { container, warnings } = await start({ launcher_size: 60, launcher_open_size: 80 });
  assertEqual([inline(container, '--osa-size-closed'), inline(container, '--osa-size-open')], ['60px', '60px'], 'an open size above the closed size is brought down to it');
  assertEqual(warnings.filter((w) => w.includes('launcherOpenSize')).length, 1, 'with a warning');
}

console.log('\nthe panel\'s resize handle drags away from its anchor');
{
  const drag = async (widget) => {
    const { window, q } = await start(widget, { innerWidth: 1600, innerHeight: 1000 });
    const panel = q('.osa-chat-window');
    panel.style.width = '440px';
    Object.defineProperty(panel, 'offsetWidth', { value: 440, configurable: true });
    Object.defineProperty(panel, 'offsetHeight', { value: 680, configurable: true });
    q('.osa-resize-handle').dispatchEvent(new window.MouseEvent('mousedown', { clientX: 500, clientY: 500, bubbles: true }));
    window.document.dispatchEvent(new window.MouseEvent('mousemove', { clientX: 540, clientY: 500, bubbles: true }));
    window.document.dispatchEvent(new window.MouseEvent('mouseup', { bubbles: true }));
    return panel.style.width;
  };
  assertEqual(await drag({}), '400px', 'anchored right, dragging the handle 40px right narrows the panel');
  assertEqual(await drag({ launcher_position: 'bottom-left' }), '480px', 'anchored left, the same drag widens it');
}

console.log('\na wrong or missing value from the page does not lock out the community\'s own');
{
  const community = { launcher_position: 'bottom-left', launcher_size: 70, launcher_offset_y: 96 };
  const wrong = await start(community, {
    page: { launcherSize: '70', launcherOffsetY: undefined, launcherPosition: undefined, launcherOffsetX: 12.5 },
  });
  assert(wrong.container.classList.contains('osa-pos-left'), 'launcherPosition: undefined is not a value: the community\'s bottom-left applies');
  assertEqual(inline(wrong.container, '--osa-size-closed'), '70px', 'a wrong launcherSize (the string "70") is refused, so the community\'s 70 applies');
  assertEqual(inline(wrong.container, '--osa-edge-y'), '96px', 'launcherOffsetY: undefined is not a value: the community\'s 96 applies');
  assertEqual(inline(wrong.container, '--osa-edge-x'), '', 'and a wrong launcherOffsetX is refused, with no community value to fall back to: the default');
  assertEqual(wrong.warnings.filter((w) => w.includes('setConfig')).length, 2, 'the two wrong values are warned about, in setConfig, and the undefined ones are not');
  assert(wrong.warnings.every((w) => !w.includes('undefined')), 'no warning is about an undefined value');
}
{
  const pinned = await start({ launcher_position: 'bottom-left', launcher_size: 70 }, { page: { launcherPosition: null, launcherSize: null } });
  assert(!pinned.container.classList.contains('osa-pos-left'), 'null is a value: the page puts the position back to its default over the community');
  assertEqual(inline(pinned.container, '--osa-size-closed'), '', 'and the size');
  assertEqual(pinned.warnings.filter((w) => w.includes('launcherPosition')), [], 'a null position is unset, not invalid: no warning');
}

console.log('\nthe launcher is sized as what is drawn, not as what a later setConfig asks for');
{
  const { window, api, container, q } = await start({});
  api.setConfig({ launcher: 'capsule', launcherSize: 70 });
  assert(!container.classList.contains('osa-capsule'), 'sanity: setConfig({launcher}) after init() converts nothing, so it is still a bubble');
  assertEqual([inline(container, '--osa-size-closed'), inline(container, '--osa-size-open')], ['70px', '56px'], 'its 70px is opened at 80% of the bubble\'s own 56px default, not the capsule\'s 58px');
  assert(container.classList.contains('osa-launcher-resizes'), 'and it shrinks, as a bubble does');
  assertEqual(px(window, q('.osa-chat-button'), 'width'), 56, 'from a 56px box');
}

console.log('\na bad value never stops the widget from starting, and its warning always says what it was');
{
  const { container, warnings } = await start({}, {
    page: { launcherSize: 10n, launcherOffsetX: NaN, launcherOffsetY: Infinity, launcherMobileOffsetX: { nested: true } },
  });
  assert(!!container.querySelector('.osa-chat-button'), 'a BigInt and a NaN in the settings do not stop init()');
  assert(warnings.some((w) => w.includes('launcherSize') && w.includes('10')), 'a BigInt is named as what it is, not thrown over');
  assert(warnings.some((w) => w.includes('launcherOffsetX') && w.includes('NaN')), 'NaN is shown as NaN, not as null');
  assert(warnings.some((w) => w.includes('launcherOffsetY') && w.includes('Infinity')), 'and Infinity as Infinity, with a warning of its own');
}

console.log('\na page\'s settings apply when the community config never arrives');
{
  const offline = async () => { throw new Error('offline'); };
  const { window, container, q } = await start({}, {
    fetch: offline,
    page: { launcherPosition: 'bottom-left', launcherSize: 70, launcherOffsetY: 40 },
  });
  assert(container.classList.contains('osa-pos-left'), 'with the community config unreachable, the page\'s position still applies');
  assertEqual(inline(container, '--osa-size-closed'), '70px', 'and its size');
  assertEqual([px(window, q('.osa-chat-button'), 'left'), px(window, q('.osa-chat-button'), 'bottom')], [20, 40], 'the button is where the page put it');
}

console.log('\nthe pop-out has no launcher, so the position and sizes leave it alone');
for (const launcher of ['bubble', 'capsule']) {
  const { window, q } = await start({ launcher }, { page: { fullscreen: true, launcherPosition: 'bottom-left', launcherSize: 70, launcherOffsetX: 60, launcherOffsetY: 60 } });
  const panel = window.getComputedStyle(q('.osa-chat-window'));
  assertEqual([panel.left, panel.right, panel.top, panel.bottom, panel.width], ['0px', '0px', '0px', '0px', '100%'], `${launcher}: the pop-out's panel still fills its window`);
  assertEqual(window.getComputedStyle(q('.osa-resize-handle')).display, 'none', `${launcher}: with no resize handle`);
}

console.log('\nunder reduced motion the launcher goes between its sizes at once');
{
  const { window, q } = await start({ launcher_size: 70 }, { reducedMotion: true });
  assert(window.matchMedia('(prefers-reduced-motion: reduce)').matches, 'sanity: the device asks for reduced motion');
  const style = window.getComputedStyle(q('.osa-chat-button'));
  assertEqual([style.transitionDuration, style.transitionDelay], ['1ms', '0ms'], 'every transition on a bubble that shrinks, the resize included, is immediate');
}

console.log('\nthe open size is rounded, and the ends of every range are valid');
{
  const { container } = await start({ launcher_size: 72 });
  assertEqual(inline(container, '--osa-size-open'), '58px', '80% of 72 is 57.6, rounded to 58, not cut to 57');
  const capsule = await start({ launcher: 'capsule', launcher_size: 58 });
  assertEqual([inline(capsule.container, '--osa-size-closed'), inline(capsule.container, '--osa-size-open')], ['58px', '46px'], 'a capsule\'s 58px alone opens at 46px, so naming it changes nothing');
  const bubble = await start({ launcher_size: 56 });
  assertEqual(inline(bubble.container, '--osa-size-open'), '45px', 'but a bubble\'s 56px alone opens at 45px: writing today\'s size makes it shrink');
}
{
  const largest = await start({}, { page: { launcherSize: 96, launcherOffsetX: 200, launcherOffsetY: 200, launcherMobileOffsetX: 200, launcherMobileOffsetY: 200 } });
  assertEqual([inline(largest.container, '--osa-size-closed'), inline(largest.container, '--osa-edge-x'), inline(largest.container, '--osa-edge-y-narrow')], ['96px', '200px', '200px'], '96px and 200px are valid');
  assertEqual(largest.warnings.filter((w) => w.includes('launcher')), [], 'without a warning');
  const smallest = await start({}, { page: { launcherSize: 44, launcherOpenSize: 44, launcherOffsetX: 0, launcherOffsetY: 0 } });
  assertEqual([inline(smallest.container, '--osa-size-closed'), inline(smallest.container, '--osa-edge-x'), inline(smallest.container, '--osa-edge-y')], ['44px', '0px', '0px'], '44px and 0px are valid');
  assertEqual(smallest.warnings.filter((w) => w.includes('launcher')), [], 'without a warning');
}

console.log('\nthe panel keeps inside the window however far the launcher is moved in');
{
  // happy-dom leaves min() and calc() alone, so this reads the numbers each minimum
  // works out to; the Chrome check measures the panel itself.
  const cases = [
    { label: 'bubble, 100px in, on a 390px-wide window', widget: { launcher_offset_x: 100 }, width: 390, height: 800, minWidth: 290, minHeight: 350 },
    { label: 'bubble, 200px up, on a 560px-tall window', widget: { launcher_offset_y: 200 }, width: 390, height: 560, minWidth: 300, minHeight: 560 - 200 - 56 - 14 },
    { label: 'default, on a 320px-wide window', widget: {}, width: 320, height: 800, minWidth: 300, minHeight: 350 },
    { label: 'default, on a 440px-tall window', widget: {}, width: 1024, height: 440, minWidth: 300, minHeight: 350 },
    { label: 'capsule, 200px in, 96px wide, on a 601px-wide window', widget: { launcher: 'capsule', launcher_size: 96, launcher_open_size: 96, launcher_offset_x: 200 }, width: 601, height: 800, minWidth: 601 - 200 - 96 - 19, minHeight: 350 },
  ];
  for (const { label, widget, width, height, minWidth, minHeight } of cases) {
    const { window, q } = await start(widget, { innerWidth: width, innerHeight: height });
    const panel = window.getComputedStyle(q('.osa-chat-window'));
    assertEqual([cssNumber(panel.minWidth), cssNumber(panel.minHeight)], [minWidth, minHeight], `${label}: the panel's minimums are ${minWidth} by ${minHeight}`);
  }
}

console.log('\nthe capsule\'s circles are in the order they are seen in, so Tab follows the eye');
{
  const at = async (position, width) => {
    const { window, q } = await start({ launcher: 'capsule', launcher_position: position }, { innerWidth: width });
    return { window, capsule: q('.osa-launcher-capsule') };
  };
  assertEqual(capsuleOrder((await at('bottom-right', 1024)).capsule), ['hpc', 'notebook', 'chat'], 'a column on the right: top to bottom');
  assertEqual(capsuleOrder((await at('bottom-left', 1024)).capsule), ['hpc', 'notebook', 'chat'], 'a column on the left: top to bottom, the chat circle at the bottom');
  assertEqual(capsuleOrder((await at('bottom-right', 390)).capsule), ['hpc', 'notebook', 'chat'], 'a row on the right: left to right, ending at the chat circle on the anchor');
  assertEqual(capsuleOrder((await at('bottom-left', 390)).capsule), ['chat', 'notebook', 'hpc'], 'a row on the left: left to right, starting at the chat circle on the anchor');
  assertEqual(capsuleOrder((await at('bottom-left', 600)).capsule), ['chat', 'notebook', 'hpc'], '600px wide is still the row');
  assertEqual(capsuleOrder((await at('bottom-left', 601)).capsule), ['hpc', 'notebook', 'chat'], '601px wide is the column');

  // Crossing the breakpoint with the window open puts them in the other order.
  const { window, q } = await start({ launcher: 'capsule', launcher_position: 'bottom-left' }, { innerWidth: 1024 });
  const capsule = q('.osa-launcher-capsule');
  const chat = q('.osa-launcher-capsule .osa-chat-button');
  const listeners = [];
  window.happyDOM.setViewport({ width: 390, height: 800 });
  assertEqual(capsuleOrder(capsule), ['chat', 'notebook', 'hpc'], 'narrowing the window puts the chat circle first');
  window.happyDOM.setViewport({ width: 1024, height: 800 });
  assertEqual(capsuleOrder(capsule), ['hpc', 'notebook', 'chat'], 'and widening it puts it last again');
  assert(capsule.contains(chat) && capsule.firstElementChild.classList.contains('osa-capsule-indicator') && listeners.length === 0, 'the indicator stays first, behind the circles, and nothing was rebuilt');

  // Moving a node takes keyboard focus from it (happy-dom does this as a browser does),
  // and a resize is not the reader's doing.
  const notebook = q('.osa-launcher-capsule .osa-notebook-btn');
  for (const [circle, name] of [[chat, 'the chat circle'], [notebook, 'the notebook circle']]) {
    circle.focus();
    assert(window.document.activeElement === circle, `${name} has keyboard focus`);
    window.happyDOM.setViewport({ width: 390, height: 800 });
    assert(window.document.activeElement === circle, `${name} keeps it when the window narrows and the circles reorder`);
    assertEqual(capsuleOrder(capsule), ['chat', 'notebook', 'hpc'], `${name}: and the circles did reorder`);
    window.happyDOM.setViewport({ width: 1024, height: 800 });
    assert(window.document.activeElement === circle, `${name} keeps it when the window widens again`);
  }
  window.document.activeElement?.blur();
  window.happyDOM.setViewport({ width: 390, height: 800 });
  assert(window.document.activeElement === window.document.body, 'with nothing focused, nothing is given focus by a reorder');
  window.happyDOM.setViewport({ width: 1024, height: 800 });
}

console.log('\nhovered at rest, the anchored corner holds still on the left and for the bubble');
{
  const hoverRule = (window, selector) => {
    for (const sheet of window.document.styleSheets) {
      for (const rule of sheet.cssRules) if (rule.selectorText === selector) return rule.style;
    }
    return null;
  };
  const cases = [
    { label: 'a bubble on the right', widget: { launcher_size: 80 }, rule: '.osa-chat-widget.osa-launcher-resizes:not(.chat-open) > .osa-chat-button:hover', open: 64, closed: 80, side: -1 },
    { label: 'a bubble on the left', widget: { launcher_size: 80, launcher_position: 'bottom-left' }, rule: '.osa-chat-widget.osa-launcher-resizes:not(.chat-open) > .osa-chat-button:hover', open: 64, closed: 80, side: 1 },
    { label: 'a capsule on the left', widget: { launcher: 'capsule', launcher_position: 'bottom-left' }, rule: '.osa-chat-widget:not(.chat-open) .osa-launcher-capsule .osa-chat-button:hover', open: 46, closed: 58, side: 1 },
  ];
  for (const { label, widget, rule, open, closed, side } of cases) {
    const { window, container } = await start(widget);
    const hover = hoverRule(window, rule);
    assert(!!hover, `${label}: there is a rule for the resting circle hovered`);
    if (!hover) continue;
    const widgetStyle = window.getComputedStyle(container);
    const scale = cssNumber(withVars(hover.getPropertyValue('scale'), widgetStyle));
    const shift = cssNumbers(withVars(hover.getPropertyValue('translate'), widgetStyle));
    assertEqual(hover.getPropertyValue('transform'), 'none', `${label}: the shared scale(1.05) around the center gives way`);
    assert(Math.abs(scale - (closed / open) * 1.05) < 1e-9, `${label}: it grows to 5% more of its resting size (${scale})`);
    const growth = open * (scale - 1) / 2;
    assert(shift.length === 2 && Math.abs(shift[0] - side * growth) < 1e-9 && Math.abs(shift[1] + growth) < 1e-9,
      `${label}: with the translate that holds its corner still, ${side < 0 ? 'right' : 'left'} and bottom (${shift.join(' ')})`);
  }
}

console.log('\nthe reduced-motion block covers the bubble\'s grow-and-shrink');
{
  const { window } = await start({});
  let reduced = null;
  for (const sheet of window.document.styleSheets) {
    for (const rule of sheet.cssRules) {
      if (rule.media && /\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)/.test(String(rule.media.mediaText))) reduced = rule;
    }
  }
  const selectors = reduced ? [...reduced.cssRules].map((r) => r.selectorText).join(' | ') : '';
  assert(selectors.includes('.osa-chat-widget.osa-launcher-resizes > .osa-chat-button'), 'its transitions are immediate under reduced motion');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed > 0 ? 1 : 0);
