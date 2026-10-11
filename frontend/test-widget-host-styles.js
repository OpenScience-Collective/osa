/**
 * A host page's rules on bare elements (#597) against the widget's replies and
 * code cards, run against the real widget source in a happy-dom window, the way
 * test-widget-color-scheme.js runs it.
 *
 * The widget lives in the host page's document, so a docs theme's `code`, `pre`,
 * `p`, `li` or `h2` rules reach what it renders wherever the widget's own stylesheet
 * leaves a property unset. The host rules below are the ones the Read the Docs and
 * PyData themes set; each is read back as the widget's computed style, with a plain
 * element outside the widget as the control that the host rule is live.
 *
 * What happy-dom cannot resolve is `inherit` and `revert`, so those properties are
 * held to the widget's stylesheet text. Comparing every computed style of every
 * reply and card element against a page with no host styles, under the real theme
 * stylesheets, needs a real browser and is not part of this suite.
 *
 * What stands in: `fetch`, answering with HTTP fixtures for the community config
 * endpoint (never a mock of the widget's own logic).
 *
 * Run with: bun frontend/test-widget-host-styles.js
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

// Rules a docs theme puts on bare elements, taken from sphinx_rtd_theme and the
// PyData theme (the real stylesheets are in the browser harness's comparison).
const HOST_CSS = `
  code { white-space: nowrap; max-width: 100%; overflow-x: auto; color: #e74c3c;
         border: 1px solid #e1e4e5; padding: 2px 5px; line-height: 12px; font-size: 75%; }
  pre { font-size: 9px; line-height: 12px; border: 1px solid #e1e4e5; clear: both; font-family: serif; }
  p { font-size: 16px; line-height: 24px; }
  h1, h2, h3 { font-family: Georgia, serif; color: #404040; }
  ul, ol, li { list-style: none; }
  td { vertical-align: top; }
  hr { opacity: 0.25; }
  sup { position: relative; vertical-align: baseline; top: -0.5em; }
`;

function configResponse() {
  return {
    default_model: 'm',
    offered_models: [],
    widget: { title: 'Test Assistant', placeholder: 'loaded' },
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

// The widget, evaluated in a window whose page already carries `hostCss`.
function loadWidget({ fetch, hostCss }) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  if (hostCss) {
    const host = window.document.createElement('style');
    host.textContent = hostCss;
    window.document.head.appendChild(host);
  }
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

async function startWidget(hostCss) {
  const config = configResponse();
  const { window, widget } = loadWidget({ fetch: fetchReturning(config), hostCss });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-host-styles' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(
    () => container.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder,
    'the community config has loaded'
  );
  return { window, container };
}

// Markup shaped like a rendered reply and the code cards, in the widget and, as the
// control, the same inline code outside it.
const REPLY = `<p>Text with <code class="t-inline">inline()</code> in it.</p>
  <h2 class="t-heading">Heading</h2>
  <pre class="t-pre"><code class="t-pre-code">x = 1</code></pre>
  <ul class="t-ul"><li class="t-li">item</li></ul>`;
const CARD = `<div class="osa-tool-panel"><pre class="osa-tool-code t-card-pre"><code class="t-card-code">print(1)</code></pre></div>`;

console.log('='.repeat(60));
console.log('Widget: host page styles do not reach replies or code cards (#597)');
console.log('='.repeat(60));

console.log('\na host rule on bare elements is live outside the widget');
{
  const { window, container } = await startWidget(HOST_CSS);
  window.document.body.insertAdjacentHTML('beforeend', '<code class="outside">x</code><p class="outside-p">y</p>');
  const outside = window.getComputedStyle(window.document.querySelector('.outside'));
  assertEqual(outside.whiteSpace, 'nowrap', 'a plain <code> outside the widget reads the host rule (nowrap)');
  assertEqual(outside.color, '#e74c3c', 'and its color');
  assert(container.isConnected, 'the widget is in the same document as the host rule');
}

console.log('\ninline code and code blocks in a reply');
{
  const { window, container } = await startWidget(HOST_CSS);
  container.querySelector('.osa-chat-messages').insertAdjacentHTML('beforeend',
    `<div class="osa-message assistant"><div class="osa-message-content">${REPLY}</div></div>`);
  const style = (selector) => window.getComputedStyle(container.querySelector(selector));
  const inline = style('.t-inline');
  assertEqual(inline.whiteSpace, 'normal', 'inline code wraps, whatever the host sets (white-space: normal)');
  assertEqual(inline.borderTopWidth, '0px', 'inline code has no host border');
  assert(inline.borderTopStyle !== 'solid', 'and no solid border style');
  assertEqual(inline.maxWidth, 'none', 'no host max-width');
  assertEqual(inline.overflow, 'visible', 'no host overflow');
  assertEqual(inline.paddingLeft, '6px', 'the widget\'s own padding, not the host\'s');
  assertEqual(inline.fontSize, '13px', 'the widget\'s own font size');
  assertEqual(style('.t-pre-code').whiteSpace, 'pre', 'code in a block keeps its line breaks (white-space: pre)');
  assertEqual(style('.t-pre').borderTopWidth, '0px', 'a code block has no host border');
  assertEqual(style('.t-pre').clear, 'none', 'and no host clear');
}

console.log('\nthe code cards, rendered beside the reply');
{
  const { window, container } = await startWidget(HOST_CSS);
  container.querySelector('.osa-chat-messages').insertAdjacentHTML('beforeend',
    `<div class="osa-message assistant">${CARD}</div>`);
  const style = (selector) => window.getComputedStyle(container.querySelector(selector));
  assertEqual(style('.t-card-pre').borderTopWidth, '0px', 'a card\'s code block has no host border');
  assertEqual(style('.t-card-pre').clear, 'none', 'and no host clear');
}

// happy-dom does not resolve `inherit`, `revert` or `all`, so what these rules say is
// held to the stylesheet's text. Each is a property a docs theme sets on the element,
// declared so the host's value cannot win, with the value that gives a page with no
// host styles exactly what it had before.
console.log('\nthe stylesheet declares what happy-dom cannot resolve');
{
  // Every rule of the widget's stylesheet as [selectors, body], read from its source.
  const styles = SOURCE.slice(SOURCE.indexOf('const STYLES = `'), SOURCE.indexOf('\n  `;', SOURCE.indexOf('const STYLES = `')));
  const rules = [...styles.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map(([, selectors, body]) => [selectors.split(',').map((one) => one.trim()), body]);
  const declares = (selector, declaration) => rules.some(([selectors, body]) =>
    selectors.includes(selector) && body.includes(declaration));
  const cases = [
    ['.osa-message-content p', 'font-size: inherit'],
    ['.osa-message-content p', 'line-height: inherit'],
    ['.osa-message-content code', 'color: inherit'],
    ['.osa-message-content code', 'line-height: inherit'],
    ['.osa-message-content pre', 'font-size: inherit'],
    ['.osa-message-content pre', 'line-height: inherit'],
    ['.osa-message-content pre code', 'white-space: pre'],
    ['.osa-message-content li', 'font-size: inherit'],
    ['.osa-message-content li', 'line-height: inherit'],
    ['.osa-message-content strong', 'color: inherit'],
    ['.osa-message-content em', 'color: inherit'],
    ['.osa-message-content a:hover', 'color: var(--osa-accent)'],
    ['.osa-message-content hr', 'opacity: 1'],
    ['.osa-table th', 'font-size: inherit'],
    ['.osa-table td', 'font-size: inherit'],
    ['.osa-table td', 'vertical-align: revert'],
    ['.osa-table', 'caption-side: top'],
    ['.osa-tool-code', 'line-height: inherit'],
    ['.osa-tool-code code', 'all: revert'],
    ['.osa-copy-btn', 'font: revert'],
    ['.osa-code-action', 'font: revert'],
  ];
  for (const [selector, declaration] of cases) {
    assert(declares(selector, declaration), `${selector} declares ${declaration}`);
  }
  const lists = SOURCE.slice(SOURCE.indexOf('.osa-message-content ul, .osa-message-content ol,'));
  assert(/\.osa-message-content li \{\s*list-style-type: revert;/.test(lists),
    'list markers revert to the browser default, so a bare page keeps its disc, circle and decimal');
  assert(!/list-style-type: (disc|decimal)/.test(SOURCE.slice(SOURCE.indexOf('/* Markdown styling'), SOURCE.indexOf('/* Table styling'))),
    'and are not pinned to disc or decimal, which would flatten nested lists');
  assert(!/\.osa-message-content code \{[^}]*line-height: 1\.5/.test(SOURCE),
    'inline code does not pin a line height, so a heading containing code keeps the heading\'s');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed ? 1 : 0);
