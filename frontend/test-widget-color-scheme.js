/**
 * The widget's light and dark appearance (#469), run against the real widget
 * source in a happy-dom window, the way test-widget-capsule.js runs the capsule.
 *
 * What it holds the widget to: a community that never names color_scheme renders
 * exactly as before (no class, no inline property, on a dark device too); 'auto'
 * follows the device, including a device that switches while the page is open;
 * the host page's setColorScheme outranks the community's value, before or after
 * init() and in a pop-out; and the dark panel's colors, accent included, are the
 * ones the stylesheet declares, read back as computed styles.
 *
 * What stands in: `fetch`, answering with HTTP fixtures for the community config
 * endpoint (never a mock of the widget's own logic), and the device's color
 * scheme, which is happy-dom's own `device.prefersColorScheme` setting read by
 * the real `matchMedia`.
 *
 * Run with: bun frontend/test-widget-color-scheme.js
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

// WCAG contrast, computed here independently of the widget's own helper, so a
// mistake in that helper cannot also pass its own check.
function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// A community config response fixture, shaped like fetchCommunityConfig expects.
// `placeholder` changes with every response, so a test can wait for the config to
// have been applied even when nothing else about the widget visibly changes.
let configCounter = 0;
function configResponse(widgetOverrides = {}) {
  configCounter += 1;
  return {
    default_model: 'm',
    offered_models: [],
    widget: { title: 'Test Assistant', placeholder: `loaded ${configCounter}`, ...widgetOverrides },
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

/**
 * The widget, evaluated in its own window. Mirrors test-widget-capsule.js's
 * loadWidget, plus the device's color scheme and a pop-out's preset globals.
 */
function loadWidget({ fetch, prefersColorScheme = 'light', preset } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      device: { prefersColorScheme },
    },
  });
  window.__OSA_TEST__ = true;
  // Which media queries the widget asks about, observed on the real matchMedia
  // (the answer is still happy-dom's own).
  const mediaQueries = [];
  const realMatchMedia = window.matchMedia.bind(window);
  window.matchMedia = (query) => { mediaQueries.push(query); return realMatchMedia(query); };
  window.__mediaQueries = mediaQueries;
  if (preset) Object.assign(window, preset);
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

// Start a widget for `config` and wait until that config has been applied.
async function startWidget(config, { prefersColorScheme, preset, before } = {}) {
  const { window, widget } = loadWidget({ fetch: fetchReturning(config), prefersColorScheme, preset });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: `osa-test-scheme-${configCounter}` });
  if (before) before(widget);
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(
    () => container.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder,
    `the community config has loaded (${config.widget.placeholder})`
  );
  return { window, widget, container };
}

// The device switching its own setting while the page is open. happy-dom's
// matchMedia re-evaluates on a window resize and dispatches `change` to the
// widget's real listener when the answer differs.
function switchDevice(window, scheme) {
  window.happyDOM.settings.device.prefersColorScheme = scheme;
  window.dispatchEvent(new window.Event('resize'));
}

function captureWarnings() {
  const warnings = [];
  const original = console.warn;
  console.warn = (...args) => { warnings.push(args.map(String).join(' ')); };
  return { warnings, restore: () => { console.warn = original; } };
}

const isDark = (container) => container.classList.contains('osa-dark');

console.log('='.repeat(60));
console.log('Widget: light and dark appearance (#469)');
console.log('='.repeat(60));

console.log('\na community that never names color_scheme renders exactly as before, on a dark device too');
{
  const { window, widget, container } = await startWidget(configResponse({ theme_color: '#008a79' }), { prefersColorScheme: 'dark' });
  assertEqual(widget.getConfig().colorScheme, 'light', 'its colorScheme is the light default');
  assertEqual(container.className, 'osa-chat-widget', "the container's className is exactly 'osa-chat-widget'");
  assertEqual(container.style.getPropertyValue('--osa-accent-on-dark'), '', 'no dark accent is set inline');
  assert(!/--osa-accent-on-dark/.test(container.getAttribute('style') || ''), 'the style attribute never mentions the dark accent');
  assertEqual(window.__mediaQueries.filter((q) => q.includes('prefers-color-scheme')), [],
    'it never asks the device for its color scheme, so it registers no listener');
}

console.log('\na light community keeps the light panel\'s own colors on its inputs (the fix every community gets)');
{
  const { window, container } = await startWidget(configResponse());
  const style = (selector) => window.getComputedStyle(container.querySelector(selector));
  assertEqual(style('.osa-chat-window').color, '#1f2937', 'the panel sets its own text color, so nothing inherits the host page\'s');
  assertEqual(style('.osa-chat-input input').backgroundColor, '#ffffff', 'the chat input has its own white background');
  assertEqual(style('.osa-chat-input input').color, '#1f2937', 'and its own text color');
  assertEqual(style('.osa-settings-input').backgroundColor, '#ffffff', 'the Settings key field has its own background');
  assertEqual(style('.osa-settings-input').color, '#1f2937', 'and its own text color');
  assertEqual(style('.osa-settings-select').color, '#1f2937', 'the Settings model menu has its own text color');
  assertEqual(style('.osa-feedback-textarea').color, '#1f2937', 'the feedback form\'s text box does too');
  // A real browser's own stylesheet gives form controls their own color rather
  // than letting them inherit the panel's; happy-dom's does not, so the panel's
  // color would hide a missing rule. Under a parent with a foreign color, each
  // control's own rule is the only way to get the widget's.
  container.insertAdjacentHTML('beforeend', `<div class="osa-inherit-probe" style="color: #ff0000">
    <div class="osa-chat-input"><input></div>
    <input class="osa-settings-input"><select class="osa-settings-select"></select>
    <textarea class="osa-settings-input osa-feedback-textarea"></textarea></div>`);
  for (const selector of ['.osa-chat-input input', 'input.osa-settings-input', '.osa-settings-select', '.osa-feedback-textarea']) {
    const probe = container.querySelector(`.osa-inherit-probe ${selector}`);
    assertEqual(window.getComputedStyle(probe).color, '#1f2937', `${selector} sets its own text color, not an inherited one`);
  }
  assert(/\.osa-chat-widget \{[^}]*color-scheme: light;/.test(SOURCE),
    'the container declares color-scheme: light, so a dark host page does not darken the browser\'s own controls');
}

console.log('\n"auto" follows the device: dark on a dark device, light on a light one');
{
  const dark = await startWidget(configResponse({ color_scheme: 'auto' }), { prefersColorScheme: 'dark' });
  assertEqual(dark.widget.getConfig().colorScheme, 'auto', 'the community\'s auto is taken');
  assert(isDark(dark.container), 'a dark device gets the dark panel');
  const light = await startWidget(configResponse({ color_scheme: 'auto' }), { prefersColorScheme: 'light' });
  assert(!isDark(light.container), 'a light device keeps the light panel');
}

console.log('\n"auto" follows a device that switches while the page is open');
{
  const { window, container } = await startWidget(configResponse({ color_scheme: 'auto' }), { prefersColorScheme: 'light' });
  assert(!isDark(container), 'light to begin with');
  switchDevice(window, 'dark');
  assert(isDark(container), 'the device switching to dark darkens the panel');
  switchDevice(window, 'light');
  assert(!isDark(container), 'and switching back lightens it again');
}

console.log('\nthe host page\'s choice outranks the community\'s, before init() and after');
{
  // Before init(): no light flash, and the community's later 'auto' does not undo it.
  const early = await startWidget(configResponse({ color_scheme: 'auto' }), {
    prefersColorScheme: 'light',
    before: (widget) => widget.setColorScheme('dark'),
  });
  assert(isDark(early.container), 'setColorScheme(\'dark\') before init(): dark, on a light device, after the community\'s auto arrived');
  assertEqual(early.widget.getConfig().colorScheme, 'dark', 'and CONFIG still holds the host\'s value');

  // After init(): applied at once, and a device switch no longer moves it.
  const { window, widget, container } = await startWidget(configResponse({ color_scheme: 'auto' }), { prefersColorScheme: 'dark' });
  assert(isDark(container), 'auto on a dark device: dark');
  widget.setColorScheme('light');
  assert(!isDark(container), 'setColorScheme(\'light\') after init(): light at once');
  switchDevice(window, 'light');
  switchDevice(window, 'dark');
  assert(!isDark(container), 'a device switch after the host chose light leaves it light');
  widget.setColorScheme('auto');
  assert(isDark(container), 'handing the choice back to the device (auto) on a dark device: dark again');
}

console.log('\na host\'s choice before init() is on the panel from the start, before any community config arrives');
{
  // A config request that never answers: whatever the panel shows is what
  // createWidget itself drew.
  const { window, widget } = loadWidget({ fetch: () => new Promise(() => {}) });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-scheme-noflash' });
  widget.setColorScheme('dark');
  widget.init();
  assert(isDark(window.document.querySelector('.osa-chat-widget')), 'dark the moment the widget exists');
}

console.log('\nsetColorScheme(\'dark\') reaches a community that never opted in');
{
  // color_scheme is the community's default; a host page with a dark theme of its
  // own may still ask for dark, which is the one way a light community gets it.
  const { widget, container } = await startWidget(configResponse(), { prefersColorScheme: 'light' });
  widget.setColorScheme('dark');
  assert(isDark(container), 'the host\'s dark applies to a light community');
}

console.log('\ninvalid values are ignored, with a warning, and change nothing');
{
  const { widget, container } = await startWidget(configResponse({ color_scheme: 'auto' }), { prefersColorScheme: 'dark' });
  const { warnings, restore } = captureWarnings();
  try {
    widget.setColorScheme('purple');
    widget.setColorScheme(undefined);
    widget.setConfig({ colorScheme: 'Dark' });
  } finally {
    restore();
  }
  assertEqual(widget.getConfig().colorScheme, 'auto', 'CONFIG still holds the community\'s value');
  assert(isDark(container), 'and the panel is still dark');
  assertEqual(warnings.filter((w) => w.includes('Invalid colorScheme')).length, 3, 'each of the three was warned about');

  // An invalid value from the server is ignored the same way.
  const serverWarnings = captureWarnings();
  let fromServer;
  try {
    fromServer = await startWidget(configResponse({ color_scheme: 'midnight' }), { prefersColorScheme: 'dark' });
  } finally {
    serverWarnings.restore();
  }
  assertEqual(fromServer.widget.getConfig().colorScheme, 'light', 'a server color_scheme that is not light or auto leaves the light default');
  assert(!isDark(fromServer.container), 'and the panel light');
  assert(serverWarnings.warnings.some((w) => w.includes('invalid color_scheme') && w.includes('midnight')),
    'with a warning naming the rejected value');
}

console.log('\nsetConfig({colorScheme}) applies too, before or after init()');
{
  const { widget, container } = await startWidget(configResponse(), {
    prefersColorScheme: 'light',
    before: (w) => w.setConfig({ colorScheme: 'dark' }),
  });
  assert(isDark(container), 'set before init(): dark');
  widget.setConfig({ colorScheme: 'light' });
  assert(!isDark(container), 'set after init(): applied at once');
}

console.log('\nthe dark panel\'s colors are the ones the stylesheet declares, read back as computed styles');
{
  const { window, container } = await startWidget(
    configResponse({ color_scheme: 'auto', disclaimer_color: '#9a3412', disclaimer_background: '#fff7ed' }),
    { prefersColorScheme: 'dark' }
  );
  // Real markup the widget renders for these, so a selector that does not match
  // the widget's own structure fails here.
  const messages = container.querySelector('.osa-chat-messages');
  messages.insertAdjacentHTML('beforeend',
    '<div class="osa-message assistant"><div class="osa-message-content"><pre><code>x</code></pre><code>y</code><a href="#">link</a></div></div>');
  const style = (selector) => window.getComputedStyle(container.querySelector(selector));
  const darkBg = SOURCE.match(/const DARK_PANEL_BG = '(#[0-9a-f]{6})';/)[1];
  assertEqual(style('.osa-chat-window').backgroundColor, darkBg, 'the panel is DARK_PANEL_BG, the value the accent is measured against');
  assertEqual(style('.osa-chat-window').color, '#e5e7eb', 'its text is light');
  assertEqual(style('.osa-chat-input input').backgroundColor, darkBg, 'the chat input is dark');
  assertEqual(style('.osa-chat-input input').color, '#e5e7eb', 'with light text');
  assertEqual(style('.osa-message.assistant .osa-message-content').backgroundColor, '#1f2937', 'the assistant\'s bubble is one step lighter than the panel');
  assertEqual(style('.osa-message-content pre').backgroundColor, '#030712', 'a code block is one step darker');
  assertEqual(style('.osa-settings-modal').backgroundColor, darkBg, 'Settings is dark');
  assertEqual(style('.osa-settings-input').color, '#e5e7eb', 'and its fields have light text');
  // The configured disclaimer colors were chosen for the light panel and are
  // replaced here, even though they are set inline.
  assertEqual(container.style.getPropertyValue('--osa-disclaimer-color'), '#9a3412', 'the configured disclaimer color is still set inline');
  assertEqual(style('.osa-ai-disclaimer').color, '#fdba74', 'but the dark panel\'s disclaimer uses its own amber');
  const textRatio = contrast('#e5e7eb', darkBg);
  assert(textRatio >= 7, `panel text reads at AAA contrast (${textRatio.toFixed(1)}:1)`);
  const mutedRatio = contrast('#9ca3af', '#1f2937');
  assert(mutedRatio >= 4.5, `muted text reads on the assistant's bubble (${mutedRatio.toFixed(1)}:1)`);
}

console.log('\nthe dark accent: NEMAR\'s teal as it is; a theme color too dark to read is lifted');
{
  // NEMAR's own colors: accent_color #257a92 was chosen for white (3.6:1 on the
  // dark panel, too dark); the teal theme color itself reads there.
  const nemar = {
    color_scheme: 'auto', theme_color: '#5bbad5', theme_text_color: '#04121f', accent_color: '#257a92',
  };
  const darkBg = SOURCE.match(/const DARK_PANEL_BG = '(#[0-9a-f]{6})';/)[1];
  const addLink = (container) => {
    container.querySelector('.osa-chat-messages').insertAdjacentHTML('beforeend',
      '<div class="osa-message assistant"><div class="osa-message-content"><a href="#">link</a></div></div>');
    return container.querySelector('.osa-message-content a');
  };

  assert(contrast('#257a92', darkBg) < 4.5,
    `NEMAR's accent_color would not read on the dark panel (${contrast('#257a92', darkBg).toFixed(1)}:1), which is why it gives way`);

  const onDark = await startWidget(configResponse(nemar), { prefersColorScheme: 'dark' });
  assertEqual(onDark.container.style.getPropertyValue('--osa-accent-on-dark'), '', 'NEMAR\'s teal needs no lifting');
  assertEqual(onDark.window.getComputedStyle(addLink(onDark.container)).color, '#5bbad5', 'links on the dark panel are the teal itself');
  assert(contrast('#5bbad5', darkBg) >= 4.5, `which reads there (${contrast('#5bbad5', darkBg).toFixed(1)}:1)`);

  const onLight = await startWidget(configResponse(nemar), { prefersColorScheme: 'light' });
  assertEqual(onLight.window.getComputedStyle(addLink(onLight.container)).color, '#257a92', 'on the light panel, links are still accent_color');

  // No theme_color: the platform blue, 3.4:1 on the dark panel, is lifted.
  const platform = await startWidget(configResponse({ color_scheme: 'auto' }), { prefersColorScheme: 'dark' });
  const lifted = platform.container.style.getPropertyValue('--osa-accent-on-dark');
  assert(/^#[0-9a-f]{6}$/.test(lifted), `the platform blue gets a lifted dark accent (${lifted})`);
  assert(contrast(lifted, darkBg) >= 4.5, `which reads on the dark panel (${contrast(lifted, darkBg).toFixed(1)}:1)`);
  assert(contrast(lifted, darkBg) < 5.5, 'and is lifted only as far as it needs to be, not to white');
  assertEqual(platform.window.getComputedStyle(addLink(platform.container)).color, lifted, 'links use it');

  // A theme color that changes after the lift clears the stale value.
  platform.widget.setConfig({ themeColor: '#5bbad5' });
  platform.widget.setColorScheme('auto');
  assertEqual(platform.container.style.getPropertyValue('--osa-accent-on-dark'), '', 'a theme color that reads clears the lifted value');
}

console.log('\na pop-out keeps the scheme its host page chose; otherwise it follows the community');
{
  // What openPopout writes before the widget script runs in the new window.
  const hostChose = await startWidget(configResponse({ color_scheme: 'auto' }), {
    prefersColorScheme: 'light',
    preset: {
      __OSA_CHAT_CONFIG__: { fullscreen: true, colorScheme: 'dark' },
      __OSA_HOST_COLOR_SCHEME__: 'dark',
    },
  });
  assert(isDark(hostChose.container), 'a pop-out whose host chose dark stays dark after the community\'s auto arrives, on a light device');

  const hostSilent = await startWidget(configResponse({ color_scheme: 'auto' }), {
    prefersColorScheme: 'light',
    preset: {
      __OSA_CHAT_CONFIG__: { fullscreen: true, colorScheme: 'dark' },
      __OSA_HOST_COLOR_SCHEME__: null,
    },
  });
  assert(!isDark(hostSilent.container), 'a pop-out whose host chose nothing follows the community (auto, light device)');

  const warnings = captureWarnings();
  let bogus;
  try {
    bogus = await startWidget(configResponse(), {
      preset: { __OSA_CHAT_CONFIG__: { fullscreen: true }, __OSA_HOST_COLOR_SCHEME__: 'dark;background:red' },
    });
  } finally {
    warnings.restore();
  }
  assert(!isDark(bogus.container), 'a preset scheme that is not light, dark or auto is ignored');
  assertEqual(bogus.widget.getConfig().colorScheme, 'light', 'leaving the default');
}

console.log('\nopenPopout writes the host\'s choice into the pop-out, and only the host\'s');
{
  // window.open is wrapped to hand back a document whose write() is recorded,
  // the way test-widget-capsule.js observes window.open's URL: what is under
  // test is the HTML the widget writes, not a pop-out's behavior.
  async function popoutHtml({ hostScheme }) {
    const config = configResponse({ color_scheme: 'auto' });
    const fetch = async (url) => {
      if (String(url).endsWith('osa-chat-widget.js')) return new Response(SOURCE);
      return fetchReturning(config)(url);
    };
    const { window, widget } = loadWidget({ fetch });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: `osa-test-popout-${hostScheme}` });
    if (hostScheme) widget.setColorScheme(hostScheme);
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    await waitUntil(() => container.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder, 'config loaded');
    const written = [];
    window.open = () => ({ closed: false, close() {}, document: { write: (html) => written.push(html), close() {} } });
    container.querySelector('.osa-popout-btn').click();
    await waitUntil(() => written.length > 0, 'the pop-out was written');
    return written.join('');
  }
  const chosen = await popoutHtml({ hostScheme: 'dark' });
  assert(chosen.includes('window.__OSA_HOST_COLOR_SCHEME__ = "dark";'), 'a host that chose dark: the pop-out is told so');
  const silent = await popoutHtml({ hostScheme: null });
  assert(silent.includes('window.__OSA_HOST_COLOR_SCHEME__ = null;'), 'a host that chose nothing: the pop-out is told nothing');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed ? 1 : 0);
