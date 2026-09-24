/**
 * The three-icon capsule launcher (#436): chat, notebook and HPC, run against the
 * real widget source in a happy-dom window, the same way test-widget-tools.js runs
 * the browser-execution suite. A separate file rather than adding to that one,
 * since the capsule is a self-contained feature with its own markup, its own
 * `setDataset`/`setConfig({notebookUrl})` API surface, and its own bubble-mode
 * regression assertion (a bubble community's markup and computed styles must stay
 * byte-for-byte what they were before this feature existed).
 *
 * What stands in: `fetch`, answering with HTTP fixtures for the community config
 * endpoint (never a mock of the widget's own logic), and `window.open`, wrapped
 * so a test can observe the URL a click would have opened without a real tab
 * (the pattern the browser-harness README also uses for the same reason).
 *
 * Run with: bun frontend/test-widget-capsule.js
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

function noNetwork(url) {
  return Promise.reject(new Error(`unexpected request in a unit test: ${url}`));
}

// A community config response fixture, shaped like fetchCommunityConfig expects.
function configResponse(widgetOverrides = {}) {
  return {
    default_model: 'm',
    offered_models: [],
    widget: { title: 'NEMAR Assistant', ...widgetOverrides },
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

/** The widget, evaluated in its own window. Mirrors test-widget-tools.js's loadWidget. */
function loadWidget({
  scriptSrc = 'http://localhost/static/osa-chat-widget.js',
  fetch = noNetwork,
  innerWidth,
  innerHeight,
} = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    // Left undefined unless a caller passes them: happy-dom's own default
    // (1024x768, confirmed empirically) is desktop-width, which is what every
    // other test in this file already relies on implicitly. A caller testing
    // the narrow (<=600px) layout passes innerWidth explicitly.
    ...(innerWidth !== undefined ? { innerWidth } : {}),
    ...(innerHeight !== undefined ? { innerHeight } : {}),
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      // The notebook tab's frame (#470) is created with a real src; a unit test
      // checks the address, and never loads it.
      navigation: { disableChildFrameNavigation: true },
    },
  });
  window.__OSA_TEST__ = true;
  const script = window.document.createElement('script');
  if (scriptSrc) script.setAttribute('src', scriptSrc);
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

// Wrap window.open so a click can be observed without opening a real tab (the
// browser-harness README calls this instrumentation, not a business-logic mock:
// what is under test is what URL and args the widget passes to the real API).
function wrapWindowOpen(window) {
  const calls = [];
  window.open = (url, target, features) => {
    calls.push({ url, target, features });
    return null;
  };
  return calls;
}

console.log('='.repeat(60));
console.log('Widget: the three-icon capsule launcher (#436)');
console.log('='.repeat(60));

console.log('\ncapsule markup exists only under launcher: capsule');
{
  // Bubble is the default: no config field at all sets it.
  {
    const { window, widget } = loadWidget({ fetch: fetchReturning(configResponse()) });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-capsule-bubble' });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    assert(!container.querySelector('.osa-launcher-capsule'), 'no capsule wrapper when launcher is bubble (default)');
    assert(!container.classList.contains('osa-capsule'), 'no osa-capsule class when launcher is bubble (default)');
  }

  // The community config names launcher: capsule (NEMAR's real path): the
  // capsule converts once fetchCommunityConfig resolves, after createWidget().
  {
    const config = configResponse({ launcher: 'capsule' });
    const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-capsule-api' });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'the capsule converts once the community config arrives');
    assert(container.classList.contains('osa-capsule'), 'osa-capsule class is added');
    const capsule = container.querySelector('.osa-launcher-capsule');
    assert(!!capsule.querySelector('.osa-notebook-btn'), 'the notebook icon exists');
    assert(!!capsule.querySelector('.osa-hpc-btn'), 'the HPC icon exists');
    assert(!!capsule.querySelector('.osa-chat-button'), 'the chat button is still there');
    const order = Array.from(capsule.children).map((el) => el.className);
    assert(order[0].includes('osa-capsule-indicator'), 'DOM order: the indicator first, drawn behind the circles (#470)');
    assert(order[1].includes('osa-hpc-btn'), 'DOM order: HPC next (top when expanded)');
    assert(order[2].includes('osa-notebook-btn'), 'DOM order: notebook (middle)');
    assert(order[3].includes('osa-chat-button'), 'DOM order: chat last (bottom, anchored, never moves)');

    // The tooltip <span>s repeat the button's own aria-label verbatim; without
    // aria-hidden a screen reader would read each one twice. The pre-existing
    // .osa-chat-tooltip (the collapsed launcher's own) is untouched, since it
    // has no button-owning aria-label to duplicate.
    for (const btn of [capsule.querySelector('.osa-hpc-btn'), capsule.querySelector('.osa-notebook-btn'), capsule.querySelector('.osa-chat-button')]) {
      assertEqual(btn.querySelector('.osa-icon-tooltip').getAttribute('aria-hidden'), 'true', `${btn.className}: its tooltip span is aria-hidden`);
    }
    assert(!container.querySelector('.osa-chat-tooltip').hasAttribute('aria-hidden'), 'the pre-existing launcher tooltip is untouched, no aria-hidden added to it');
  }

  // Set at creation time via setConfig before init(), not just from the API.
  {
    const { window, widget } = loadWidget({ fetch: fetchReturning(configResponse()) });
    widget.setConfig({
      apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-capsule-setconfig',
      launcher: 'capsule',
    });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    assert(!!container.querySelector('.osa-launcher-capsule'), 'setConfig({launcher: "capsule"}) before init() builds the capsule immediately');
  }

  // An embedder that explicitly opts OUT overrides the community config, the
  // same _userSetKeys precedence every other widget key already has.
  {
    const config = configResponse({ launcher: 'capsule' });
    const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
    widget.setConfig({
      apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-capsule-override',
      launcher: 'bubble',
    });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    await waitUntil(() => window.OSAChatWidget.getConfig().communityId === 'test', 'config settles');
    // Give the async fetch a moment to resolve and (if it were going to) convert.
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert(!container.querySelector('.osa-launcher-capsule'), 'an explicit launcher: bubble is never overridden by the community config');
  }
}

console.log('\nthe capsule stacks above the chat window (its icon tooltips must not render underneath it)');
{
  // Regression: two fixed-position siblings with EQUAL z-index stack by DOM
  // order, and .osa-chat-window follows .osa-launcher-capsule in the markup,
  // so a tie would paint the window over the icon tooltips even though a
  // tooltip's own z-index is higher than the window's -- that z-index is
  // scoped to the capsule's OWN stacking context and is never compared
  // against the window's. Measured live in Chrome before the fix: the HPC
  // and notebook tooltips rendered mostly hidden behind the chat window.
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-zindex' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists');
  const capsule = container.querySelector('.osa-launcher-capsule');
  const chatWindow = container.querySelector('.osa-chat-window');
  const capsuleZ = Number(window.getComputedStyle(capsule).zIndex);
  const windowZ = Number(window.getComputedStyle(chatWindow).zIndex);
  assert(Number.isFinite(capsuleZ) && Number.isFinite(windowZ), `both z-indexes are real numbers (capsule=${capsuleZ}, window=${windowZ})`);
  assert(capsuleZ > windowZ, `the capsule's z-index (${capsuleZ}) is higher than the chat window's (${windowZ}), so its tooltips always paint on top`);
}

console.log('\nresizing the chat window still works when it is nested in the capsule');
{
  // setupResize computes new width/height purely from mouse deltas against
  // chatWindow's own offsetWidth/offsetHeight; it never reads the capsule or
  // the chat button's position, so nesting the chat button inside the capsule
  // should not affect it. Proven directly rather than assumed.
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-resize' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists');
  const chatWindow = container.querySelector('.osa-chat-window');
  const handle = container.querySelector('.osa-resize-handle');
  assert(!!handle, 'the resize handle still exists inside the (now capsule-nested) chat window');

  // happy-dom has no real layout engine, so offsetWidth/offsetHeight report 0
  // rather than the CSS-declared 440x680; setupResize reads those directly, so
  // seed them the way a real layout would, then drag from that starting point.
  Object.defineProperty(chatWindow, 'offsetWidth', { value: 440, configurable: true });
  Object.defineProperty(chatWindow, 'offsetHeight', { value: 680, configurable: true });

  handle.dispatchEvent(new window.MouseEvent('mousedown', { clientX: 500, clientY: 500, bubbles: true }));
  // Resize from the top-left corner: moving the mouse LEFT and UP grows the
  // window, since it is anchored bottom-right (see setupResize's own comment).
  window.document.dispatchEvent(new window.MouseEvent('mousemove', { clientX: 460, clientY: 470, bubbles: true }));
  window.document.dispatchEvent(new window.MouseEvent('mouseup', { bubbles: true }));

  assertEqual(chatWindow.style.width, '480px', 'dragging the handle resizes the window width');
  assertEqual(chatWindow.style.height, '710px', 'and the height, even nested inside the capsule');
}

console.log('\nan already-open chat panel eases into its capsule position, rather than jumping');
{
  // The community config can still be resolving when the reader opens the
  // chat: launcher: capsule arriving a moment later moves an ALREADY-OPEN
  // panel's right/bottom/max-height, not a closed one. Opens the chat while
  // the widget is still in its bubble default, THEN lets the (mocked, but
  // still asynchronous) config fetch resolve, and checks the final position
  // and that a transition is declared for that move.
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config), innerWidth: 1024, innerHeight: 800 });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-open-then-convert' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');

  // Still bubble at this point: fetchCommunityConfig's mocked fetch is async
  // and has not resolved yet (confirmed by the assertion right after).
  assert(!container.classList.contains('osa-capsule'), 'sanity: still bubble mode immediately after init()');
  container.querySelector('.osa-chat-button').dispatchEvent(new window.Event('click', { bubbles: true }));
  const chatWindow = container.querySelector('.osa-chat-window');
  assert(chatWindow.classList.contains('open'), 'the chat is open, in bubble mode, before the config arrives');

  await waitUntil(() => container.classList.contains('osa-capsule'), 'the config resolves and converts to the capsule');
  assert(chatWindow.classList.contains('open'), 'the chat is STILL open after the conversion (the click was never undone)');
  const windowStyle = window.getComputedStyle(chatWindow);
  assertEqual(windowStyle.right, 'calc(20px + 46px + 7px + 12px)', 'the now-capsule desktop position: right, beside the capsule');
  assertEqual(windowStyle.bottom, '20px', 'the now-capsule desktop position: bottom');
  assertEqual(windowStyle.maxHeight, 'calc(800px - 50px)', 'the now-capsule desktop position: the taller max-height');
  const transition = windowStyle.transition;
  assert(
    transition.includes('right') && transition.includes('bottom') && transition.includes('max-height'),
    `a transition is declared on right/bottom/max-height so the move eases rather than jumps (got ${JSON.stringify(transition)})`
  );
}

console.log('\nbubble mode renders today\'s markup: nothing about it changes');
{
  const { window, widget } = loadWidget({ fetch: fetchReturning(configResponse()) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-bubble-unchanged' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const chatButton = container.querySelector('.osa-chat-button');
  const tooltip = container.querySelector('.osa-chat-tooltip');
  assert(chatButton.parentElement === container, 'the chat button is still a direct child of the widget container');
  assert(tooltip.parentElement === container, 'the tooltip is still a direct child of the widget container');
  // A first visit keeps the launcher hidden until the community config arrives, so
  // it is never drawn in the defaults first (#475); that is the only addition, and it
  // goes once the config is in.
  assert(container.className === 'osa-chat-widget osa-launcher-waiting', "before the config, a first visit adds only 'osa-launcher-waiting'");
  await waitUntil(() => !container.classList.contains('osa-launcher-waiting'), 'the config arrives');
  assert(container.className === 'osa-chat-widget', "the container's className is exactly 'osa-chat-widget', nothing appended");
  assert(!container.querySelector('.osa-launcher-icon'), 'no launcher icon exists anywhere in a bubble-mode widget');
}

console.log('\nbubble mode\'s computed layout is exactly today\'s: outside sites embed it unpinned');
{
  // The markup/className checks above do not catch a selector that widens to
  // match every community (e.g. dropping the ".osa-launcher-capsule " prefix
  // from the chat-button override, so ".osa-chat-button { position: relative;
  // ... }" would apply everywhere): the DOM would still look right, but a
  // bubble-mode widget embedded on an outside site, which never pins a
  // version, would silently lose its fixed position. Computed style is the
  // only check that catches that.
  const { window, widget } = loadWidget({ fetch: fetchReturning(configResponse()), innerHeight: 800 });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-bubble-computed-layout' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const chatButton = container.querySelector('.osa-chat-button');
  const chatWindow = container.querySelector('.osa-chat-window');
  const buttonStyle = window.getComputedStyle(chatButton);
  const windowStyle = window.getComputedStyle(chatWindow);

  assertEqual(buttonStyle.position, 'fixed', "the chat button's position is fixed");
  assertEqual(buttonStyle.bottom, '20px', "the chat button's bottom is 20px");
  assertEqual(buttonStyle.right, '20px', "the chat button's right is 20px");
  assertEqual(buttonStyle.zIndex, '10000', "the chat button's z-index is 10000");
  assertEqual(windowStyle.bottom, '90px', "the chat window's bottom is 90px");
  assertEqual(windowStyle.right, '20px', "the chat window's right is 20px");
  assertEqual(windowStyle.maxHeight, 'calc(800px - 120px)', "the chat window's max-height is calc(100vh - 120px), today's formula");
}

console.log('\nthe capsule\'s layout switches at the 601px breakpoint, at explicit widths');
{
  // happy-dom DOES evaluate @media (min-width: ...) against the Window's own
  // configured innerWidth when resolving getComputedStyle (confirmed
  // empirically), so this is testable here rather than only in the Chrome
  // run: built at two explicit widths, one on each side of the breakpoint,
  // rather than relying on happy-dom's own default (1024px, which is already
  // desktop-width and so would never exercise the narrow branch at all).
  const cases = [
    {
      label: 'narrow (390px, at or under the 600px breakpoint)',
      width: 390,
      flexDirection: 'row',
      windowRight: '20px',
      windowBottom: '90px',
    },
    {
      label: 'desktop (1024px, past the 601px breakpoint)',
      width: 1024,
      flexDirection: 'column',
      windowRight: 'calc(20px + 46px + 7px + 12px)',
      windowBottom: '20px',
    },
  ];
  for (const { label, width, flexDirection, windowRight, windowBottom } of cases) {
    const config = configResponse({ launcher: 'capsule' });
    const { window, widget } = loadWidget({ fetch: fetchReturning(config), innerWidth: width, innerHeight: 800 });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: `osa-test-breakpoint-${width}` });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    await waitUntil(() => container.querySelector('.osa-launcher-capsule'), `capsule exists (${label})`);
    const capsule = container.querySelector('.osa-launcher-capsule');
    const chatWindow = container.querySelector('.osa-chat-window');
    assertEqual(window.getComputedStyle(capsule).flexDirection, flexDirection, `${label}: capsule flex-direction`);
    assertEqual(window.getComputedStyle(chatWindow).right, windowRight, `${label}: chat window right`);
    assertEqual(window.getComputedStyle(chatWindow).bottom, windowBottom, `${label}: chat window bottom`);
  }
}

console.log('\nthe four notebook states from setDataset');
{
  const cases = [
    {
      label: 'no dataset (never set)',
      apply: () => {},
      active: false,
      tooltip: 'Open a dataset page to start a notebook',
      ariaLabel: 'Notebook: open a dataset page to start a notebook',
    },
    {
      label: 'no dataset (explicit null)',
      apply: (widget) => widget.setDataset(null),
      active: false,
      tooltip: 'Open a dataset page to start a notebook',
      ariaLabel: 'Notebook: open a dataset page to start a notebook',
    },
    {
      label: 'dataset, zarr unknown',
      apply: (widget) => widget.setDataset({ id: 'nm000103' }),
      active: false,
      tooltip: 'Checking whether this dataset has a Zarr copy',
      ariaLabel: 'Notebook: checking for a Zarr copy',
    },
    {
      label: 'dataset, zarr: false',
      apply: (widget) => widget.setDataset({ id: 'nm000103', zarr: false }),
      active: false,
      tooltip: 'This dataset has no Zarr copy yet, so there is nothing to open in a notebook',
      ariaLabel: 'Notebook: this dataset has no Zarr copy',
    },
    {
      label: 'dataset, zarr: true',
      apply: (widget) => widget.setDataset({ id: 'nm000103', zarr: true }),
      active: true,
      tooltip: 'Open nm000103 in a Python notebook',
      ariaLabel: 'Open nm000103 in a Python notebook',
    },
  ];
  for (const { label, apply, active, tooltip, ariaLabel } of cases) {
    const config = configResponse({ launcher: 'capsule' });
    const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: `osa-test-notebook-${label}` });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    await waitUntil(() => container.querySelector('.osa-launcher-capsule'), `capsule exists (${label})`);
    apply(widget);
    const notebookBtn = container.querySelector('.osa-notebook-btn');
    assertEqual(notebookBtn.getAttribute('aria-disabled'), active ? 'false' : 'true', `${label}: aria-disabled`);
    assert(notebookBtn.classList.contains('osa-icon-available') === active, `${label}: osa-icon-available class matches`);
    assertEqual(notebookBtn.querySelector('.osa-icon-tooltip').textContent, tooltip, `${label}: tooltip text`);
    assertEqual(notebookBtn.getAttribute('aria-label'), ariaLabel, `${label}: exact aria-label`);
  }
}

console.log('\nsetDataset before init() is applied once the capsule exists');
{
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-dataset-before-init' });
  widget.setDataset({ id: 'nm000103', zarr: true });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists');
  const notebookBtn = container.querySelector('.osa-notebook-btn');
  assertEqual(notebookBtn.getAttribute('aria-disabled'), 'false', 'the pre-init setDataset value is applied once the capsule is built');
  assert(notebookBtn.classList.contains('osa-icon-available'), 'and the notebook icon renders available');
}

console.log('\nthe pre-init value renders even when the community config never arrives');
{
  // Isolates applyLauncherMode's OWN render call (at capsule-creation time) from
  // the one applyWidgetConfig also does once fetchCommunityConfig resolves: with
  // a real (fast-resolving) fetch mock, both fire close enough together that a
  // test cannot tell which one actually painted the initial state. A rejecting
  // fetch never reaches applyWidgetConfig at all (confirmed: its catch block only
  // calls disableWidget), so this can only pass if capsule creation itself renders
  // the value setDataset queued before init() -- explicit setConfig(launcher)
  // stands in for the community config that will never arrive.
  const failingFetch = async () => { throw new Error('network unreachable in this test'); };
  const { window, widget } = loadWidget({ fetch: failingFetch });
  widget.setConfig({
    apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-dataset-no-config',
    launcher: 'capsule',
  });
  widget.setDataset({ id: 'nm000103', zarr: true });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists, from setConfig alone');
  const notebookBtn = container.querySelector('.osa-notebook-btn');
  assertEqual(notebookBtn.getAttribute('aria-disabled'), 'false', 'the pre-init setDataset value still rendered, with no community config ever arriving to do it');
}

console.log('\nthe pop-out (fullscreen) never gets the launcher, even when launcher: capsule is set');
{
  // The pop-out window has no launcher at all (.fullscreen .osa-chat-button is
  // display:none !important); applyLauncherMode's own "if (!CONFIG.fullscreen)"
  // is what stops it from still building the wrapper and its two icons behind
  // that hidden button. It gets the panel's tabs instead, as a strip (#470), which
  // frontend/test-widget-popout.js covers.
  const { window, widget } = loadWidget({ fetch: fetchReturning(configResponse()) });
  widget.setConfig({
    apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-fullscreen-no-capsule',
    fullscreen: true, launcher: 'capsule',
  });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  assert(container.classList.contains('fullscreen'), 'sanity: fullscreen mode is on');
  assert(!container.querySelector('.osa-launcher-capsule'), 'no capsule wrapper in fullscreen mode, even with launcher: capsule');
  for (const selector of ['.osa-capsule-indicator', '.osa-notebook-btn', '.osa-hpc-btn']) {
    assert(!container.querySelector(selector), `no ${selector} either`);
  }
  assert(container.classList.contains('osa-capsule') && !!container.querySelector('.osa-tab-strip'),
    'but the panel\'s tabs, as a strip');
}

console.log('\nevery later setDataset call re-renders');
{
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-dataset-rerender' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists');
  const notebookBtn = container.querySelector('.osa-notebook-btn');

  widget.setDataset({ id: 'nm000103', zarr: false });
  assertEqual(notebookBtn.getAttribute('aria-disabled'), 'true', 'zarr: false renders inactive');

  widget.setDataset({ id: 'nm000103', zarr: true });
  assertEqual(notebookBtn.getAttribute('aria-disabled'), 'false', 'a later call updates it to active');

  widget.setDataset(null);
  assertEqual(notebookBtn.getAttribute('aria-disabled'), 'true', 'and clearing it back to null updates it again');
}

console.log('\ninvalid setDataset input is ignored, leaving the prior state alone');
{
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-dataset-invalid' });
  widget.setDataset({ id: 'nm000103', zarr: true });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists');
  const notebookBtn = container.querySelector('.osa-notebook-btn');
  // Check the FULL prior state, not just active/inactive: an invalid id that
  // still reads as "active" (e.g. an object whose id fails validation but
  // whose zarr is still true) would leave aria-disabled matching "before" by
  // coincidence, while silently replacing the stored id. The tooltip text
  // embeds the id for the active state, so it is what actually proves nothing
  // changed underneath.
  const before = {
    ariaDisabled: notebookBtn.getAttribute('aria-disabled'),
    tooltip: notebookBtn.querySelector('.osa-icon-tooltip').textContent,
  };

  const invalidInputs = [
    { id: 'has a space', zarr: true },
    { id: 'has/slash', zarr: true },
    { id: 'x'.repeat(65), zarr: true },
    { id: 'nm000103', zarr: 'yes' },
    { id: 'nm000103', zarr: 1 },
    'not-an-object',
    42,
    ['nm000103'],
  ];
  for (const bad of invalidInputs) {
    widget.setDataset(bad);
    const after = {
      ariaDisabled: notebookBtn.getAttribute('aria-disabled'),
      tooltip: notebookBtn.querySelector('.osa-icon-tooltip').textContent,
    };
    assertEqual(after, before, `invalid setDataset(${JSON.stringify(bad)}) leaves state unchanged`);
  }
}

console.log('\nthe HPC icon is coming soon everywhere, with a badge and no click action');
{
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-hpc' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists');
  const hpcBtn = container.querySelector('.osa-hpc-btn');
  assertEqual(hpcBtn.getAttribute('aria-disabled'), 'true', 'HPC is always aria-disabled');
  assertEqual(hpcBtn.getAttribute('aria-label'), 'HPC submission, coming soon', 'HPC exact aria-label');
  assert(!hpcBtn.classList.contains('osa-icon-available'), 'HPC never gets the available look');
  assertEqual(hpcBtn.querySelector('.osa-icon-tooltip').textContent, 'HPC submission is coming soon', 'HPC tooltip text');
  const badge = hpcBtn.querySelector('.osa-icon-badge');
  assert(!!badge && badge.textContent === 'Soon' && badge.style.display !== 'none', 'HPC carries a visible "Soon" badge, not just a muted icon');

  const opens = wrapWindowOpen(window);
  hpcBtn.dispatchEvent(new window.Event('click', { bubbles: true }));
  assertEqual(opens.length, 0, 'clicking the coming-soon HPC icon opens nothing');
}

console.log('\nan inactive notebook button opens nothing on click (the aria-disabled guard)');
{
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-inactive-click' });
  widget.setDataset({ id: 'nm000103', zarr: false });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists');
  const notebookBtn = container.querySelector('.osa-notebook-btn');
  assertEqual(notebookBtn.getAttribute('aria-disabled'), 'true', 'sanity: the button is inactive');

  const opens = wrapWindowOpen(window);
  notebookBtn.dispatchEvent(new window.Event('click', { bubbles: true }));
  assertEqual(opens.length, 0, 'clicking an inactive notebook button opens no browser tab');
  assert(!container.querySelector('.osa-notebook-frame'), 'nor a notebook frame');
  assert(!container.querySelector('.osa-chat-window').classList.contains('open'), 'nor the panel');
}

console.log('\nthe aria-disabled attribute is its own guard, independent of currentDataset');
{
  // Under the normal render path, aria-disabled and currentDataset.zarr always
  // agree (both come from the same notebookIconState() call), so the test above
  // cannot tell the aria-disabled check apart from the currentDataset re-check
  // beside it. This forces them apart directly on the DOM: currentDataset.zarr
  // stays true, so the handler's OTHER guard would let the click through on
  // its own, isolating whether the aria-disabled check is doing real work
  // rather than riding along on an always-agreeing currentDataset check.
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-aria-guard-alone' });
  widget.setDataset({ id: 'nm000103', zarr: true });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists');
  const notebookBtn = container.querySelector('.osa-notebook-btn');
  assertEqual(notebookBtn.getAttribute('aria-disabled'), 'false', 'sanity: active to start');
  notebookBtn.setAttribute('aria-disabled', 'true'); // forced out of sync with currentDataset

  const opens = wrapWindowOpen(window);
  notebookBtn.dispatchEvent(new window.Event('click', { bubbles: true }));
  assertEqual(opens.length, 0, 'aria-disabled="true" alone blocks the click, even with an active dataset behind it');
  assert(!container.querySelector('.osa-notebook-frame'), 'and makes no notebook frame');
}

console.log('\nthe available notebook button opens the notebook in the panel, at the exact contract URL');
{
  // NOT a test that encodeURIComponent runs: isValidCommunityId and
  // isValidDatasetId only ever admit unreserved characters (letters, digits,
  // '.', '_', '-'), so nothing that reaches this point ever needs escaping,
  // and this test cannot tell an encoded '.' or '-' apart from an unescaped
  // one (removing both encodeURIComponent calls still passes it). What it
  // DOES prove is the literal contract string, byte for byte, for values that
  // include the punctuation the format allows. encodeURIComponent stays in
  // the source as defense in depth against a future caller of
  // handleNotebookClick that is not gated by those validators, not because
  // this test exercises it.
  const config = configResponse({ launcher: 'capsule' });
  const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
  widget.setConfig({
    apiEndpoint: 'http://localhost/api', communityId: 'nemar-test', storageKey: 'osa-test-open-url',
    // notebookUrl left at its default (https://notebook.osc.earth/osa/), so this
    // also proves the default itself builds the right URL, not just an override.
  });
  widget.setDataset({ id: 'nm.000103', zarr: true });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-launcher-capsule'), 'capsule exists');
  const notebookBtn = container.querySelector('.osa-notebook-btn');
  assertEqual(notebookBtn.getAttribute('aria-disabled'), 'false', 'sanity: the button is active');

  const opens = wrapWindowOpen(window);
  notebookBtn.dispatchEvent(new window.Event('click', { bubbles: true }));
  assertEqual(opens.length, 0, 'no browser tab is opened: the notebook is a tab of the panel (#470)');
  assert(container.querySelector('.osa-chat-window').classList.contains('open'), 'the panel opens');
  const frame = container.querySelector('.osa-view-notebook .osa-notebook-frame');
  assert(!!frame, 'with the notebook frame in the notebook view');
  assertEqual(
    frame && frame.getAttribute('src'),
    'https://notebook.osc.earth/osa/open.html?community=nemar-test&dataset=nm.000103',
    'the frame\'s address matches the notebook site\'s contract exactly (a punctuation-carrying id and community, which need no escaping under the validators\' own unreserved-character rule)'
  );
}

console.log('\nnotebookUrl defaults to the /osa/ project path on the shared plane');
{
  const { widget } = loadWidget({ fetch: fetchReturning(configResponse()) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-nburl-default' });
  assertEqual(widget.getConfig().notebookUrl, 'https://notebook.osc.earth/osa/', 'the default names the /osa/ project path, not the host root');
}

console.log('\nnotebookUrl: valid values are accepted and normalized, invalid ones are ignored');
{
  const cases = [
    { input: 'https://notebook.osc.earth/osa', expected: 'https://notebook.osc.earth/osa/', label: 'https, project path, no trailing slash' },
    { input: 'https://notebook.osc.earth/osa/', expected: 'https://notebook.osc.earth/osa/', label: 'https, project path, already slashed' },
    { input: 'https://notebook.osc.earth', expected: 'https://notebook.osc.earth/', label: 'https, host root, no trailing slash' },
    { input: 'http://localhost:8080/nb', expected: 'http://localhost:8080/nb/', label: 'http on localhost, for tests' },
    { input: 'http://127.0.0.1:8080', expected: 'http://127.0.0.1:8080/', label: 'http on 127.0.0.1, for tests' },
  ];
  for (const { input, expected, label } of cases) {
    const { widget } = loadWidget({ fetch: fetchReturning(configResponse()) });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-nburl-valid', notebookUrl: input });
    assertEqual(widget.getConfig().notebookUrl, expected, `valid (${label}): normalized to a trailing slash`);
  }

  const invalid = [
    'http://example.com/',      // http on a non-local host
    'ftp://notebook.osc.earth/', // wrong protocol
    'not a url',
    '',
    42,
    null,
    // A base URL carries no query or fragment: handleNotebookClick appends
    // its own '?community=...&dataset=...' after it, so accepting one here
    // would build a URL with two '?', and the trailing-slash fix would land
    // the slash inside the query/fragment rather than at the end of the path
    // ('https://notebook.osc.earth/osa/?env=staging' would become
    // '.../osa/?env=staging/', not '.../osa/?env=staging').
    'https://notebook.osc.earth/osa/?env=staging',
    'https://notebook.osc.earth/osa/#section',
  ];
  for (const bad of invalid) {
    const { widget } = loadWidget({ fetch: fetchReturning(configResponse()) });
    const before = widget.getConfig().notebookUrl;
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-nburl-invalid', notebookUrl: bad });
    assertEqual(widget.getConfig().notebookUrl, before, `invalid (${JSON.stringify(bad)}) is ignored, default kept`);
  }
}

console.log('\nlauncher_label sets the collapsed tooltip; unset keeps today\'s text');
{
  // From the community config (NEMAR's real path).
  {
    const config = configResponse({ launcher: 'capsule', launcher_label: 'Explore NEMAR' });
    const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-label-api' });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    const tooltip = container.querySelector('.osa-chat-tooltip');
    await waitUntil(() => tooltip.textContent === 'Explore NEMAR', 'the API-provided launcher_label replaces the tooltip text');
  }

  // Unset: the exact hardcoded text, from the title.
  {
    const config = configResponse({});
    const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-label-unset' });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    const tooltip = container.querySelector('.osa-chat-tooltip');
    // The community config sets no launcher_label, so once its title (NEMAR
    // Assistant) loads, the tooltip falls through to the unchanged hardcoded
    // "Ask me about <title>" text, exactly as it always has.
    await waitUntil(() => tooltip.textContent === 'Ask me about NEMAR', "unset keeps today's hardcoded text, once the title itself loads");
  }

  // Explicit setConfig overrides the API value, same _userSetKeys precedence as
  // every other widget key.
  {
    const config = configResponse({ launcher_label: 'From the API' });
    const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-label-override', launcherLabel: 'From the embedder' });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    const tooltip = container.querySelector('.osa-chat-tooltip');
    assertEqual(tooltip.textContent, 'From the embedder', 'an explicit launcherLabel is set immediately');
    await new Promise((resolve) => setTimeout(resolve, 50));
    assertEqual(tooltip.textContent, 'From the embedder', 'and the API value never overwrites it');
  }
}

console.log('\nthe capsule\'s available, open-tab and neutral surfaces resolve to the colors assigned them, against the REAL stylesheet');
{
  // Same technique test-widget-tools.js's own table-driven surface/foreground
  // audit uses (probe a throwaway element against the real, unmodified
  // injectStyles() output, with custom properties set directly rather than
  // round-tripping a community config): kept here, not there, so every piece
  // of capsule classification lives in one file. The open tab's fill (the
  // indicator) is a THEMED surface (same pair the launcher button and header are);
  // the neutral icon (inactive OR coming-soon: they share a look, see #436) is
  // NOT, and must read identically whether or not a community themes anything,
  // which is the property that keeps a disabled button from ever looking active.
  const { window, widget } = loadWidget();
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-capsule-surfaces' });
  widget.init(); // injects the real STYLES block; nothing here awaits its (unused) fetch

  function probe(customProps, html, selector) {
    const container = window.document.createElement('div');
    container.className = 'osa-chat-widget';
    for (const [prop, value] of Object.entries(customProps)) {
      container.style.setProperty(prop, value);
    }
    container.innerHTML = html;
    window.document.body.appendChild(container);
    return container.querySelector(selector);
  }

  const NEMAR_PROPS = {
    '--osa-primary': '#5bbad5',
    '--osa-primary-dark': '#42a1bc',
    '--osa-on-primary': '#04121f',
    '--osa-accent-on-light': '#257a92',
    '--osa-user-bg': '#5bbad5',
    '--osa-user-text': '#04121f',
  };
  const UNSET_PROPS = {};

  // Available but not the open tab (#470): an outlined circle whose icon is the
  // accent, the theme's color as a foreground; the same role links have.
  const AVAILABLE_HTML = '<div class="osa-launcher-capsule"><button class="osa-launcher-icon osa-notebook-btn osa-icon-available">x</button></div>';
  const nemarAvailable = probe(NEMAR_PROPS, AVAILABLE_HTML, '.osa-notebook-btn');
  const unsetAvailable = probe(UNSET_PROPS, AVAILABLE_HTML, '.osa-notebook-btn');
  assertEqual(window.getComputedStyle(nemarAvailable).backgroundColor, 'transparent', 'available notebook icon: no fill of its own');
  assertEqual(window.getComputedStyle(nemarAvailable).color, '#257a92', 'available notebook icon: NEMAR-style icon is accent_color (#257a92)');
  assertEqual(window.getComputedStyle(unsetAvailable).color, '#2563eb', 'available notebook icon: unset, the accent is the platform blue');

  // The open tab: the indicator behind it is the theme's surface, and the icon on
  // it is the text-on-surface color, the same pair the launcher button uses.
  const CURRENT_HTML = '<div class="osa-launcher-capsule"><div class="osa-capsule-indicator"></div><button class="osa-launcher-icon osa-notebook-btn osa-icon-available osa-tab-current">x</button></div>';
  const nemarCurrent = probe(NEMAR_PROPS, CURRENT_HTML, '.osa-notebook-btn');
  const unsetCurrent = probe(UNSET_PROPS, CURRENT_HTML, '.osa-notebook-btn');
  assertEqual(window.getComputedStyle(nemarCurrent).color, '#04121f', 'the open tab\'s icon: NEMAR-style, theme_text_color (#04121f)');
  assertEqual(window.getComputedStyle(unsetCurrent).color, '#ffffff', 'the open tab\'s icon: unset, white');
  const nemarIndicator = probe(NEMAR_PROPS, CURRENT_HTML, '.osa-capsule-indicator');
  const unsetIndicator = probe(UNSET_PROPS, CURRENT_HTML, '.osa-capsule-indicator');
  assertEqual(window.getComputedStyle(nemarIndicator).backgroundColor, '#5bbad5', 'the indicator: NEMAR-style, theme_color (#5bbad5)');
  assertEqual(window.getComputedStyle(unsetIndicator).backgroundColor, '#2563eb', 'the indicator: unset, the platform blue');

  // Neither icon carries osa-icon-available: this is the shared inactive/coming-soon
  // look (#436 says the two must still read differently from EACH OTHER via the
  // badge, not via this surface, which both use).
  const NEUTRAL_HTML = '<div class="osa-launcher-capsule"><button class="osa-launcher-icon osa-hpc-btn">x</button></div>';
  const nemarNeutral = probe(NEMAR_PROPS, NEUTRAL_HTML, '.osa-hpc-btn');
  const unsetNeutral = probe(UNSET_PROPS, NEUTRAL_HTML, '.osa-hpc-btn');
  assertEqual(window.getComputedStyle(nemarNeutral).backgroundColor, '#f3f4f6', 'neutral icon: background is the neutral --osa-assistant-bg, not theme_color, even when NEMAR-styled');
  assertEqual(window.getComputedStyle(nemarNeutral).color, '#6b7280', 'neutral icon: icon color is the neutral --osa-text-light, not theme_text_color, even when NEMAR-styled');
  assertEqual(window.getComputedStyle(unsetNeutral).backgroundColor, window.getComputedStyle(nemarNeutral).backgroundColor, 'neutral icon: identical whether or not a community themes anything');
  assertEqual(window.getComputedStyle(unsetNeutral).color, window.getComputedStyle(nemarNeutral).color, 'neutral icon: identical whether or not a community themes anything');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed > 0 ? 1 : 0);
