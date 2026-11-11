/**
 * The widget's one copy path (#665), run for real.
 *
 * Reply code blocks, the whole-reply copy and the run card all copy through the same
 * function: the Clipboard API, then the older copy command, then the text selected in
 * place with the keys named. Each shows a visible "Copied" and says it in a polite
 * live region. The widget source runs unmodified inside a happy-dom window; what stands
 * in is the platform only: the clipboard permission (happy-dom's own clipboard, denied or
 * allowed) and the copy command, which happy-dom does not implement and so is recorded
 * here with the text the textarea held when it was called.
 *
 * Run with: bun frontend/test-widget-copy.js
 */

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

function assertEqual(actual, expected, msg) {
  const same = JSON.stringify(actual) === JSON.stringify(expected);
  assert(same, `${msg}${same ? '' : ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`}`);
}

async function waitUntil(predicate, label, timeoutMs = 5000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) throw new Error(`waitUntil timed out after ${timeoutMs}ms: ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');

function noNetwork(url) {
  return Promise.reject(new Error(`unexpected request in a unit test: ${url}`));
}

/** The widget, evaluated in its own window, as test-widget-run-card.js loads it. */
function loadWidget() {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  window.fetch = noNetwork;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, noNetwork, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  return { window, api: window.OSAChatWidget.__browser };
}

function mountedContainer(window) {
  const container = window.document.createElement('div');
  container.className = 'osa-chat-widget';
  // The widget's own markup has these two; a message's copy is said by the announcer.
  container.innerHTML = '<div class="osa-chat-messages"></div>' +
    '<div class="osa-status-announcer osa-sr-only" role="status" aria-live="polite" aria-atomic="true"></div>';
  window.document.body.appendChild(container);
  return container;
}

function click(window, el) {
  el.dispatchEvent(new window.Event('click', { bubbles: true }));
}

const CODE = 'import numpy as np\nprint("<b>&amp;</b>")\nx = np.arange(3)';
const REPLY = `Here is the script:\n\n\`\`\`python\n${CODE}\n\`\`\`\n\nIt prints **three** values.`;
const RUN_CODE = 'import mne\nraw = mne.io.read_raw_edf("a.edf")\n';

/** One reply that holds a code block and ran code, so all three buttons are on the page. */
function replyWithRun() {
  return [
    { role: 'assistant', content: 'Hi' },
    { role: 'user', content: 'show me', dataset: 'nm000132' },
    {
      role: 'assistant',
      content: REPLY,
      executions: [{
        callId: 'c-1', tool: 'execute_code', description: 'Read the file', code: RUN_CODE, status: 'ok',
        stdout: 'ok\n', stderr: '', traceback: '', images: [], local: false, workspaceNote: '',
      }],
    },
  ];
}

/**
 * A page where the clipboard works (`clipboard: 'api'`), where only the copy command
 * does (`'command'`), or where neither does (`'none'`). The copy command is recorded with
 * the text of the textarea that held the selection when it was called.
 */
async function pageWith(clipboard) {
  const { window, api } = loadWidget();
  const commandCalls = [];
  if (clipboard !== 'api') (await window.navigator.permissions.query({ name: 'clipboard-write' })).state = 'denied';
  window.document.execCommand = (name) => {
    const area = window.document.activeElement;
    commandCalls.push({ name, text: area && area.tagName === 'TEXTAREA' ? area.value : null });
    return clipboard === 'command';
  };
  api.setMessages(replyWithRun());
  const container = mountedContainer(window);
  api.renderMessages(container);
  return { window, api, container, commandCalls };
}

/** The three buttons: a reply code block's, the whole reply's, and the run card's. */
const BUTTONS = {
  'a reply code block': { find: (c) => c.querySelector('.osa-copy-btn[data-copy-target]'), text: CODE, worded: false },
  'the whole-reply copy': { find: (c) => c.querySelector('.osa-message-copy-btn[data-msg-index="2"]'), text: REPLY, worded: false },
  'the run card': { find: (c) => c.querySelector('.osa-code-copy'), text: RUN_CODE, worded: true },
};

/** What a screen reader is told: the block's own status line, else the widget's announcer. */
const spokenFor = (el) => {
  const block = el.closest('.osa-code-block');
  return block ? block.querySelector('.osa-code-status') : el.closest('.osa-chat-widget').querySelector('.osa-status-announcer');
};

const quietly = async (fn) => {
  const realWarn = console.warn;
  console.warn = () => {};
  try {
    return await fn();
  } finally {
    console.warn = realWarn;
  }
};

console.log('='.repeat(60));
console.log('Widget: one copy path');
console.log('='.repeat(60));

console.log('\nthe widget has one copy implementation');
{
  assert(!/function copyToClipboard\b/.test(SOURCE), 'the second implementation is gone');
  const start = SOURCE.indexOf('// BEGIN copy-text');
  const end = SOURCE.indexOf('// END copy-text');
  assert(start > 0 && end > start, 'the copy path is one block between BEGIN and END markers');
  const block = SOURCE.slice(start, end);
  assert(/navigator\.clipboard\.writeText/.test(block) && /execCommand\('copy'\)/.test(block), 'the block holds the Clipboard API and the copy command');
  const outside = SOURCE.slice(0, start) + SOURCE.slice(end);
  assert(!/navigator\.clipboard\.writeText|execCommand\('copy'\)/.test(outside), 'and nothing outside it touches either');
}

for (const [name, button] of Object.entries(BUTTONS)) {
  console.log(`\n${name}: the Clipboard API, then Copied, then back`);
  {
    const { window, container, commandCalls } = await pageWith('api');
    const el = button.find(container);
    assert(el, 'the button is on the page');
    const before = el.innerHTML;
    click(window, el);
    await waitUntil(() => el.classList.contains('copied'), 'the button marks the copy');
    assertEqual(await window.navigator.clipboard.readText(), button.text, 'the clipboard holds the exact text');
    assertEqual(commandCalls, [], 'the copy command is not used when the API works');
    assert(/Copied/.test(el.textContent), `the button says Copied (${JSON.stringify(el.textContent)})`);
    if (!button.worded) assert(el.querySelector('svg') && el.querySelector('.osa-copied-text'), 'with the check icon beside the word');
    const status = spokenFor(el);
    assertEqual([status.textContent, status.getAttribute('aria-live'), status.getAttribute('role')], ['Copied', 'polite', 'status'], 'a polite live region says it too');
    await waitUntil(() => !el.classList.contains('copied'), 'the button goes back', 4000);
    assertEqual(el.innerHTML, before, 'and the button is as it was');
    assertEqual(status.textContent, '', 'and the live region is cleared');
  }

  console.log(`\n${name}: the copy command where the API is refused`);
  {
    const { window, container, commandCalls } = await pageWith('command');
    const el = button.find(container);
    await quietly(async () => {
      click(window, el);
      await waitUntil(() => el.classList.contains('copied'), 'the button marks the copy');
    });
    assertEqual(commandCalls, [{ name: 'copy', text: button.text }], 'the copy command ran once, on a textarea holding the exact text');
    assert(!window.document.querySelector('body > textarea'), 'and the textarea is gone');
    assert(/Copied/.test(el.textContent), 'the button says Copied');
    assert(!container.querySelector('.osa-message-copy-fallback'), 'no fallback block was drawn');
  }

  console.log(`\n${name}: where neither works, the text is selected and the keys are named`);
  {
    const { window, container, commandCalls } = await pageWith('none');
    const el = button.find(container);
    const before = el.innerHTML;
    await quietly(async () => {
      click(window, el);
      await waitUntil(() => commandCalls.length === 1 && /cannot copy/.test(spokenFor(el).textContent), 'the copy gives up');
    });
    const selected = window.getSelection().toString();
    if (name === 'the run card') {
      // The card opens its code and selects it as drawn.
      assertEqual(selected.trim(), RUN_CODE.trim(), 'the run\'s code is what is selected');
    } else {
      assertEqual(selected, button.text, 'exactly the text the button promised is selected');
    }
    const spoken = spokenFor(el);
    const prompt = /^This page cannot copy for you: the (text|code) is selected, so press (Cmd|Ctrl)\+C$/;
    assert(prompt.test(spoken.textContent), `the live region says which keys copy (${JSON.stringify(spoken.textContent)})`);
    const visible = el.closest('.osa-code-block')
      ? el.closest('.osa-code-block').querySelector('.osa-code-status')
      : el.closest('.osa-message').querySelector('.osa-copy-status');
    assert(visible && prompt.test(visible.textContent), 'and so does a visible line next to the text');
    assert(!visible.classList.contains('osa-code-status-quiet'), 'in view, not hidden');
    if (name === 'a reply code block') {
      assert(el.closest('pre').nextElementSibling === visible, 'the line sits right after the code block that was selected');
    } else if (name === 'the whole-reply copy') {
      assert(el.closest('.osa-message-header').nextElementSibling === visible, 'the line sits right after the message header, for the whole reply');
    }
    assert(!el.classList.contains('copied') && el.innerHTML === before, 'the button does not claim it copied');
    if (name === 'the whole-reply copy') {
      const blocks = container.querySelectorAll('.osa-message-copy-fallback');
      assertEqual([blocks.length, blocks[0] && blocks[0].textContent], [1, REPLY], 'the reply source is selected in a block that holds it once');
    }
  }
}

console.log('\nthe run card keeps its labels: Copy code to Copied and back');
{
  const { window, container } = await pageWith('api');
  const el = BUTTONS['the run card'].find(container);
  assertEqual(el.textContent, 'Copy code', 'it reads Copy code');
  click(window, el);
  await waitUntil(() => el.textContent === 'Copied', 'it says Copied');
  await waitUntil(() => el.textContent === 'Copy code', 'and Copy code again', 4000);
}

console.log('\nthe reply copy buttons are named for a screen reader');
{
  const { container } = await pageWith('api');
  assertEqual(BUTTONS['the whole-reply copy'].find(container).getAttribute('aria-label'), 'Copy this reply as markdown', 'the whole-reply copy');
  assertEqual(BUTTONS['a reply code block'].find(container).getAttribute('aria-label'), 'Copy code', 'a code block\'s copy');
  assert([...container.querySelectorAll('.osa-message [role="status"], .osa-message [aria-live]')].every((el) => el.closest('.osa-run-card')), 'the replies add no live region of their own to the conversation (the run card has its own)');
}

console.log('\nthe error reference copies through the same path');
{
  const { window, api, container } = await pageWith('none');
  const errorEl = window.document.createElement('div');
  container.appendChild(errorEl);
  api.fillPersistentError(container, errorEl, 'The request failed.', 'err-abc123');
  const copy = errorEl.querySelector('.osa-error-copy');
  assert(copy, 'the reference has its copy button');
  await quietly(async () => {
    click(window, copy);
    await waitUntil(() => /cannot copy/.test(errorEl.textContent), 'the copy gives up');
  });
  assertEqual(window.getSelection().toString(), 'err-abc123', 'a refused copy selects the reference');
  const works = await pageWith('api');
  const banner = works.window.document.createElement('div');
  works.container.appendChild(banner);
  works.api.fillPersistentError(works.container, banner, 'The request failed.', 'err-abc123');
  const ok = banner.querySelector('.osa-error-copy');
  click(works.window, ok);
  await waitUntil(() => /Copied/.test(ok.textContent), 'the button says Copied');
  assertEqual(await works.window.navigator.clipboard.readText(), 'err-abc123', 'a working copy puts the reference on the clipboard');
  assertEqual(works.container.querySelector('.osa-status-announcer').textContent, 'Copied', 'and the announcer says so');
}

console.log('\nthe error reference copies through the copy command too');
{
  const { window, api, container, commandCalls } = await pageWith('command');
  const banner = window.document.createElement('div');
  container.appendChild(banner);
  api.fillPersistentError(container, banner, 'The request failed.', 'err-abc123');
  const copy = banner.querySelector('.osa-error-copy');
  await quietly(async () => {
    click(window, copy);
    await waitUntil(() => copy.classList.contains('copied'), 'the button marks the copy');
  });
  assertEqual(commandCalls, [{ name: 'copy', text: 'err-abc123' }], 'the copy command ran once, on the reference');
  assert(/Copied/.test(copy.textContent), 'and the button says Copied');
}

console.log('\na second copy inside the wait keeps Copied up and is said again');
{
  const { window, container } = await pageWith('api');
  const el = BUTTONS['the whole-reply copy'].find(container);
  const announcer = container.querySelector('.osa-status-announcer');
  click(window, el);
  await waitUntil(() => el.classList.contains('copied'), 'the first copy');
  await new Promise((resolve) => setTimeout(resolve, 1200));
  click(window, el);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assertEqual(announcer.textContent, 'Copied', 'the repeat is said again (cleared, then set)');
  // The first click's timer would have fired at 2 s; the second click's runs to about 3.2 s.
  await new Promise((resolve) => setTimeout(resolve, 1000));
  assert(el.classList.contains('copied') && /Copied/.test(el.textContent), 'at 2.2 s the button still says Copied (the first timer was cancelled)');
  assertEqual(announcer.textContent, 'Copied', 'and so does the announcer');
  await waitUntil(() => !el.classList.contains('copied'), 'the button goes back', 4000);
  assertEqual(announcer.textContent, '', 'and the announcer is cleared once');
}

console.log('\na new copy takes away the earlier refusal line');
{
  const { window, container } = await pageWith('none');
  const el = BUTTONS['a reply code block'].find(container);
  await quietly(async () => {
    click(window, el);
    await waitUntil(() => (container.querySelector('.osa-copy-status') || { textContent: '' }).textContent !== '', 'the refusal line');
  });
  window.document.execCommand = () => true;
  click(window, el);
  await waitUntil(() => el.classList.contains('copied'), 'the second copy works');
  assertEqual(container.querySelector('.osa-copy-status').textContent, '', 'the refusal line is emptied');
}

console.log('\nthe prompt does not say the text is selected when nothing could be');
{
  const { window, container } = await pageWith('none');
  window.getSelection = undefined;
  const el = BUTTONS['a reply code block'].find(container);
  await quietly(async () => {
    click(window, el);
    await waitUntil(() => /cannot copy/.test(spokenFor(el).textContent), 'the copy gives up');
  });
  const spoken = spokenFor(el).textContent;
  assert(!/is selected/.test(spoken) && /select the text yourself, then press (Cmd|Ctrl)\+C$/.test(spoken), `it asks the reader to select (${JSON.stringify(spoken)})`);
}

console.log('\nthe fallback blocks are labelled groups, which a screen reader announces');
{
  const { window, container } = await pageWith('none');
  const el = BUTTONS['the whole-reply copy'].find(container);
  await quietly(async () => {
    click(window, el);
    await waitUntil(() => container.querySelector('.osa-message-copy-fallback'), 'the reply source is drawn');
  });
  const reply = container.querySelector('.osa-message-copy-fallback');
  assertEqual([reply.getAttribute('role'), reply.getAttribute('aria-label')], ['group', 'The reply as markdown, to copy'], 'the reply source block');
  assert(SOURCE.includes("pre.setAttribute('role', 'group');\n      pre.setAttribute('aria-label', 'The error, to copy');"), 'the error block is a labelled group in the source');
}

console.log('\nthe word Copied is readable on the page, in light and dark, and the error banner shows it as success');
{
  const { window, api, container } = await pageWith('api');
  api.injectStyles();
  const luminance = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a, b) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const el = BUTTONS['the whole-reply copy'].find(container);
  click(window, el);
  await waitUntil(() => el.classList.contains('copied'), 'the copy');
  const word = window.getComputedStyle(el.querySelector('.osa-copied-text')).color;
  const check = window.getComputedStyle(el).color;
  assert(contrast(word, '#ffffff') >= 4.5 && contrast(word, '#f3f4f6') >= 4.5, `light: the word is ${word}, at least 4.5:1 on white and on the grey bubble`);
  assert(word !== check, `and it is not the check's green (${check})`);
  container.classList.add('osa-dark');
  const darkWord = window.getComputedStyle(el.querySelector('.osa-copied-text')).color;
  assert(contrast(darkWord, '#1f2937') >= 4.5, `dark: ${darkWord} reads on the dark panel`);
  container.classList.remove('osa-dark');
  const banner = window.document.createElement('div');
  banner.className = 'osa-error';
  container.appendChild(banner);
  api.fillPersistentError(container, banner, 'The request failed.', 'err-abc123');
  const copy = banner.querySelector('.osa-error-copy');
  click(window, copy);
  await waitUntil(() => copy.classList.contains('copied'), 'the banner copy');
  const bannerWord = window.getComputedStyle(copy).color;
  assert(contrast(bannerWord, '#fef2f2') >= 4.5 && bannerWord !== window.getComputedStyle(banner).color, `the banner's Copied is ${bannerWord}: readable on #fef2f2 and not the failure red`);
}

clearTimeout(watchdog);
console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed === 0 ? 0 : 1);
