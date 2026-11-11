/**
 * The widget's Privacy choices (#706), run against the real widget source in happy-dom
 * windows, the way test-widget-host-api.js runs the host API.
 *
 * What it holds the widget to: the footer has a "Privacy choices" button (nemar.org's
 * opt-out symbol beside the text) and no page-context checkbox; the button opens a modal
 * dialog (role, aria-modal, aria-labelledby, labelled checkboxes) with "Use this page for
 * answers" and "Share usage data", both on by default; an earlier 'false' under
 * `osa-page-context-enabled` carries over, and then no URL or title is sent, while the
 * dataset still is; the usage choice is stored under its own key and survives a reload;
 * with usage sharing off the /chat and /chat/resume bodies say `share_usage: false`, and
 * with it on they say nothing; Tab and Shift+Tab wrap inside the dialog, Escape closes
 * only the dialog (the center dialog behind it stays, a second Escape docks it), and focus
 * returns to the button, in the corner and in the center; the privacy link is https, new
 * tab, noopener noreferrer, from the community config or the default; a community that
 * turns page context off has no row for it, and the button stays.
 *
 * What stands in: `fetch` (the community config, and a one-line chat reply). Layout, the
 * real Tab key and contrast are browser-harness/privacy-choices-check.mjs's.
 *
 * Run with: bun frontend/test-widget-privacy-choices.js
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
    if (Date.now() - started > timeoutMs) {
      throw new Error(`waitUntil timed out after ${timeoutMs}ms: ${label}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');

const API = 'http://localhost/api';
const KEY = 'osa-test-privacy';
const PAGE_KEY = 'osa-page-context-enabled';
const USAGE_KEY = 'osa-share-usage-enabled';
const DATASET = 'xx099904';
const DEFAULT_PRIVACY_URL = 'https://github.com/OpenScience-Collective/osa/blob/develop/PRIVACY.md';

// The community config, answered at once, and a chat reply of one line. `sent` records the
// body of every /chat and /chat/resume request, in order, with the path it went to.
function serverFetch(widget) {
  const sent = [];
  const encoder = new TextEncoder();
  const answer = () => new Response(new ReadableStream({
    start(controller) {
      for (const event of [{ event: 'session', session_id: 's-1' }, { event: 'content', content: 'An answer.' }, { event: 'done' }]) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      }
      controller.close();
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
  const fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (u.endsWith('/chat') || u.endsWith('/chat/resume')) {
      sent.push({ path: new URL(u).pathname.replace(/^\/api\/[^/]+/, ''), body: JSON.parse(init.body) });
      return answer();
    }
    return new Response(JSON.stringify({
      default_model: 'm',
      offered_models: [],
      widget: { title: 'Privacy Tests', ...widget },
      client_tools: [],
      runtime: null,
    }), { headers: { 'content-type': 'application/json' } });
  };
  return { fetch, sent };
}

// A page load. `storage` seeds localStorage; `widget` is the community config's widget
// block; `config` is what the embedding page passes to setConfig.
async function start({ storage = {}, widget = {}, config = {}, dataset = { id: DATASET }, saves = true, blockedKeys = [] } = {}) {
  const window = new Window({
    url: 'http://localhost/dataset/x',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  window.sessionStorage.setItem(`${KEY}-session`, '1');
  for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, value);
  window.document.body.innerHTML = '<main id="host-page"><button id="host-button" type="button">Host</button></main>';
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const server = serverFetch(widget);
  window.fetch = server.fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  // `saves` false is a browser that reads storage but will not write it (a quota, a policy).
  // `blockedKeys` is a browser that will not write only those keys.
  const localStorage = saves && blockedKeys.length === 0 ? window.localStorage : {
    getItem: (key) => window.localStorage.getItem(key),
    setItem: (key, value) => {
      if (!saves || blockedKeys.includes(key)) throw new Error('QuotaExceededError');
      window.localStorage.setItem(key, value);
    },
  };
  run(window, window.document, localStorage, server.fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  if (dataset) api.setDataset(dataset);
  api.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: 'Hello', ...config });
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-chat-title').firstChild.textContent.trim() === 'Privacy Tests', 'the community config arrives');
  const ctx = {
    window, api, hooks: api.__browser, sent: server.sent, document: window.document, container,
    q: (selector) => container.querySelector(selector),
    qa: (selector) => [...container.querySelectorAll(selector)],
    stored: (key) => window.localStorage.getItem(key),
    overlay: () => container.querySelector('.osa-privacy-overlay'),
    isDialogOpen: () => container.querySelector('.osa-privacy-overlay').classList.contains('open'),
    button: () => container.querySelector('.osa-privacy-btn'),
    page: () => container.querySelector('#osa-privacy-page'),
    usage: () => container.querySelector('#osa-privacy-usage'),
    active: () => window.document.activeElement,
    key: (name, init = {}) => new window.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...init }),
    isCenter: () => container.querySelector('.osa-chat-window').classList.contains('osa-center'),
    // The storage a page load after this one would start with.
    storageNow: () => Object.fromEntries(Array.from({ length: window.localStorage.length }, (_, i) => {
      const name = window.localStorage.key(i);
      return [name, window.localStorage.getItem(name)];
    })),
  };
  return ctx;
}

// The chat open, as a reader has it, and the dialog showing.
async function opened(options, mode = 'corner') {
  const ctx = await start(options);
  ctx.api.open({ mode });
  await waitUntil(() => ctx.q('.osa-chat-window').classList.contains('open'), 'the window opens');
  ctx.button().click();
  assert(ctx.isDialogOpen(), `sanity: the dialog opens in the ${mode}`);
  return ctx;
}

function choose(ctx, box, checked) {
  box.checked = checked;
  box.dispatchEvent(new ctx.window.Event('change', { bubbles: true }));
}

async function send(ctx, question) {
  const expected = ctx.sent.length + 1;
  ctx.q('.osa-chat-input input').value = question;
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => ctx.sent.length >= expected && !ctx.q('.osa-chat-input input').disabled, 'the reply ends');
  return ctx.sent.at(-1);
}

console.log('='.repeat(60));
console.log('Widget: Privacy choices (#706)');
console.log('='.repeat(60));

console.log('\nthe footer has a Privacy choices button, and no page-context checkbox');
{
  const ctx = await start();
  const footer = ctx.q('.osa-combined-footer');
  const button = footer.querySelector('.osa-privacy-btn');
  assert(button && button.tagName === 'BUTTON' && button.type === 'button', 'the footer has a button');
  assertEqual(button.textContent.trim(), 'Privacy choices', 'named "Privacy choices"');
  assert(ctx.q('#osa-page-context-checkbox') === null, 'there is no #osa-page-context-checkbox');
  assert(footer.querySelector('input') === null, 'and no checkbox in the footer at all');
  assert(!footer.textContent.includes('Share page URL'), 'and the old label is gone');
  const icon = button.querySelector('svg');
  assert(icon && icon.getAttribute('aria-hidden') === 'true', 'the opt-out symbol is there, and hidden from a screen reader');
  assertEqual([icon.getAttribute('viewBox'), icon.getAttribute('width'), icon.getAttribute('height')], ['0 0 30 14', '30', '14'], 'at nemar.org\'s 30:14 proportions, 14px tall');
  const fills = [...icon.querySelectorAll('path')].map((path) => path.getAttribute('fill'));
  assertEqual(fills, ['#fff', '#06f', '#fff', '#06f'], 'in its fixed colors, which no theme changes');
  assert(!ctx.isDialogOpen(), 'the dialog is closed until the button is pressed');
}

console.log('\nthe dialog is a modal with two labelled choices, both on by default');
{
  const ctx = await opened();
  const modal = ctx.q('.osa-privacy-modal');
  assertEqual(modal.getAttribute('role'), 'dialog', 'role=dialog');
  assertEqual(modal.getAttribute('aria-modal'), 'true', 'aria-modal=true');
  const title = ctx.document.getElementById(modal.getAttribute('aria-labelledby'));
  assertEqual(title && title.textContent.trim(), 'Privacy choices', 'aria-labelledby names the dialog "Privacy choices"');
  assert(ctx.overlay().classList.contains('osa-settings-overlay'), 'it is a .osa-settings-overlay, so the center trap scopes to it');
  for (const [box, label] of [[ctx.page(), 'Use this page for answers'], [ctx.usage(), 'Share usage data']]) {
    const labels = [...ctx.container.querySelectorAll(`label[for="${box.id}"]`)];
    assertEqual(labels.map((l) => l.textContent.trim()), [label], `"${label}" is a labelled checkbox`);
    assert(box.type === 'checkbox' && box.checked, `and it is on by default`);
  }
  const pageHint = ctx.document.getElementById(ctx.page().getAttribute('aria-describedby'));
  assert(/dataset a page names is still sent/i.test(pageHint.textContent), 'the page row says the dataset a page names is still sent');
  assert(ctx.document.getElementById(ctx.usage().getAttribute('aria-describedby')).textContent.trim().length > 0, 'the usage row has its own helper text');
  assert(ctx.active() === ctx.page(), 'focus moves into the dialog, onto the first choice');
}

console.log('\nthe privacy link is https, a new tab, noopener noreferrer');
{
  const ctx = await opened();
  const link = ctx.q('.osa-privacy-link');
  assertEqual(link.getAttribute('href'), DEFAULT_PRIVACY_URL, 'it goes to the default privacy page');
  assertEqual([link.getAttribute('target'), link.getAttribute('rel')], ['_blank', 'noopener noreferrer'], 'in a new tab with no opener');
  assert(link.textContent.includes('(opens in a new tab)'), 'and says so to a screen reader');
}

console.log('\nthe privacy page comes from the community config, checked again here');
{
  const fromCommunity = await opened({ widget: { privacy_url: 'https://example.org/privacy/' } });
  assertEqual(fromCommunity.q('.osa-privacy-link').getAttribute('href'), 'https://example.org/privacy/', 'the community\'s https page is used');
  for (const bad of ['http://example.org/privacy', 'javascript:alert(1)', 'https://user:pass@example.org/p', '/privacy', 'https://example.org/a b', 'https://', `https://example.org/${'a'.repeat(500)}`]) {
    const ctx = await opened({ widget: { privacy_url: bad } });
    assertEqual(ctx.q('.osa-privacy-link').getAttribute('href'), DEFAULT_PRIVACY_URL, `${bad.slice(0, 40)} falls back to the default`);
  }
  const page = await opened({ config: { privacyUrl: 'https://page.example.org/p' }, widget: { privacy_url: 'https://example.org/privacy/' } });
  assertEqual(page.q('.osa-privacy-link').getAttribute('href'), 'https://page.example.org/p', 'a page\'s own setConfig outranks the community\'s');
  const badPage = await opened({ config: { privacyUrl: 'ftp://example.org/p' } });
  assertEqual(badPage.q('.osa-privacy-link').getAttribute('href'), DEFAULT_PRIVACY_URL, 'and a bad one from the page falls back too');
}

console.log('\nan earlier choice under osa-page-context-enabled carries over');
{
  const ctx = await opened({ storage: { [PAGE_KEY]: 'false' } });
  assert(!ctx.page().checked, 'the page box starts unchecked');
  assert(ctx.usage().checked, 'the usage box is still on');
  const body = (await send(ctx, 'Hello')).body;
  assertEqual(body.page_context, { dataset_id: DATASET }, 'no url or title is sent, only the dataset');
}

console.log('\nthe page choice is stored under the same key as before');
{
  const ctx = await opened();
  choose(ctx, ctx.page(), false);
  assertEqual(ctx.stored(PAGE_KEY), 'false', 'unchecking stores false under osa-page-context-enabled');
  const off = (await send(ctx, 'One')).body;
  assert(!('url' in off.page_context) && !('title' in off.page_context), 'then no url or title is sent');
  assertEqual(off.page_context.dataset_id, DATASET, 'while dataset_id still goes');
  choose(ctx, ctx.page(), true);
  assertEqual(ctx.stored(PAGE_KEY), 'true', 'checking stores true');
  const on = (await send(ctx, 'Two')).body;
  assert(typeof on.page_context.url === 'string' && 'title' in on.page_context, 'and the url and title go again');
}

console.log('\nthe usage choice has its own key and survives a reload');
{
  const ctx = await opened();
  assertEqual(ctx.stored(USAGE_KEY), null, 'nothing is stored until the reader chooses');
  choose(ctx, ctx.usage(), false);
  assertEqual(ctx.stored(USAGE_KEY), 'false', 'unchecking stores false under osa-share-usage-enabled');
  assertEqual(ctx.stored(PAGE_KEY), null, 'and does not touch the page choice');
  const reloaded = await opened({ storage: ctx.storageNow() });
  assert(!reloaded.usage().checked, 'after a reload the box is still unchecked');
  assert(reloaded.page().checked, 'and the page box is still checked');
  choose(reloaded, reloaded.usage(), true);
  assertEqual(reloaded.stored(USAGE_KEY), 'true', 'checking it again stores true');
  const again = await opened({ storage: reloaded.storageNow() });
  assert(again.usage().checked, 'and that survives a reload too');
}

console.log('\nwith usage sharing off, /chat and /chat/resume say share_usage: false; on, they say nothing');
{
  const ctx = await opened();
  const chatOn = (await send(ctx, 'Sharing')).body;
  assert(!('share_usage' in chatOn), '/chat carries no share_usage while it is on');
  await ctx.hooks.postResume({ session_id: 's-1' }, { call_id: 'c', status: 'ok' });
  const resumeOn = ctx.sent.at(-1);
  assertEqual(resumeOn.path, '/chat/resume', 'sanity: that was a resume');
  assert(!('share_usage' in resumeOn.body), '/chat/resume carries none while it is on');

  choose(ctx, ctx.usage(), false);
  const chatOff = await send(ctx, 'Not sharing');
  assertEqual(chatOff.path, '/chat', 'sanity: that was a chat');
  assertEqual(chatOff.body.share_usage, false, '/chat carries share_usage: false');
  await ctx.hooks.postResume({ session_id: 's-1' }, { call_id: 'c', status: 'ok' });
  const resumeOff = ctx.sent.at(-1);
  assertEqual(resumeOff.path, '/chat/resume', 'sanity: that was a resume');
  assertEqual(resumeOff.body.share_usage, false, '/chat/resume carries share_usage: false');

  choose(ctx, ctx.usage(), true);
  const chatBack = (await send(ctx, 'Sharing again')).body;
  assert(!('share_usage' in chatBack), 'and turning it back on takes the field off the next request');
}

console.log('\nwith page use off, the dataset still goes, and the usage choice is separate');
{
  const ctx = await opened();
  choose(ctx, ctx.page(), false);
  choose(ctx, ctx.usage(), false);
  const body = (await send(ctx, 'Both off')).body;
  assertEqual(body.page_context, { dataset_id: DATASET }, 'page_context is the dataset alone');
  assertEqual(body.share_usage, false, 'and the usage choice rides beside it');
}

console.log('\na community that turns page context off has no row for it, and keeps the button');
{
  const ctx = await opened({ config: { allowPageContext: false } });
  assert(ctx.button() !== null, 'the button stays');
  assert(ctx.q('.osa-privacy-page-field').hidden, 'the "Use this page" row is hidden');
  assert(ctx.active() === ctx.usage(), 'focus starts on the choice that is left');
  assert(!ctx.usage().closest('[hidden]'), 'the usage choice is shown');
  const body = (await send(ctx, 'No page')).body;
  assertEqual(body.page_context, { dataset_id: DATASET }, 'no url or title is sent');
}

console.log('\nSpace toggles a checkbox');
{
  const ctx = await opened();
  ctx.usage().focus();
  const before = ctx.usage().checked;
  // What a browser does for Space on a focused checkbox: a click on key release.
  const down = ctx.key(' ');
  ctx.usage().dispatchEvent(down);
  assert(!down.defaultPrevented, 'the dialog leaves Space to the checkbox');
  ctx.usage().click();
  assert(ctx.usage().checked !== before, 'the box changes');
  assertEqual(ctx.stored(USAGE_KEY), String(!before), 'and the choice is saved');
}

for (const mode of ['corner', 'center']) {
  console.log(`\nTab and Shift+Tab wrap inside the dialog (${mode})`);
  {
    const ctx = await opened({}, mode);
    const order = ['.osa-privacy-close-btn', '#osa-privacy-page', '#osa-privacy-usage', '.osa-privacy-link'];
    const list = order.map((selector) => ctx.q(selector));
    assert(list.every(Boolean), 'sanity: the dialog\'s controls are there');
    list.at(-1).focus();
    const forward = ctx.key('Tab');
    ctx.active().dispatchEvent(forward);
    assert(forward.defaultPrevented && ctx.active() === list[0], 'Tab on the last control goes to the first');
    const back = ctx.key('Tab', { shiftKey: true });
    ctx.active().dispatchEvent(back);
    assert(back.defaultPrevented && ctx.active() === list.at(-1), 'Shift+Tab on the first goes to the last');
    list[1].focus();
    const middle = ctx.key('Tab');
    ctx.active().dispatchEvent(middle);
    assert(!middle.defaultPrevented, 'Tab in the middle is left to the browser, which stays in the dialog');
  }

  console.log(`\nEscape closes only the dialog, and focus returns to the button (${mode})`);
  {
    const ctx = await opened({}, mode);
    const closes = [];
    ctx.api.on('close', () => closes.push('close'));
    const modes = [];
    ctx.api.on('modechange', (event) => modes.push(event.mode));
    const escape = ctx.key('Escape');
    ctx.active().dispatchEvent(escape);
    assert(escape.defaultPrevented, 'Escape is handled');
    assert(!ctx.isDialogOpen(), 'the dialog closes');
    assert(ctx.q('.osa-chat-window').classList.contains('open'), 'the chat window stays open');
    assertEqual(ctx.isCenter(), mode === 'center', mode === 'center' ? 'the center dialog stays' : 'the panel stays in the corner');
    assert(ctx.active() === ctx.button(), 'focus is back on the Privacy choices button');
    assertEqual([closes, modes], [[], []], 'the host hears no close and no mode change');
    if (mode === 'center') {
      ctx.active().dispatchEvent(ctx.key('Escape'));
      assert(!ctx.isCenter() && ctx.q('.osa-chat-window').classList.contains('open'), 'a second Escape docks it');
    }
  }

  if (mode === 'center') {
    console.log('\nEscape with focus outside the dialog still closes it first (center)');
    const ctx = await opened({}, mode);
    ctx.q('.osa-privacy-overlay').focus();
    ctx.document.dispatchEvent(ctx.key('Escape'));
    assert(!ctx.isDialogOpen() && ctx.isCenter(), 'the center handler closes the dialog and leaves the center dialog open');
  }

  console.log(`\nthe close button and the backdrop close it, and focus comes back (${mode})`);
  {
    const ctx = await opened({}, mode);
    ctx.q('.osa-privacy-close-btn').click();
    assert(!ctx.isDialogOpen() && ctx.active() === ctx.button(), 'Close closes it, to the button');
    ctx.button().click();
    assert(ctx.isDialogOpen(), 'the button opens it again');
    ctx.overlay().dispatchEvent(new ctx.window.MouseEvent('click', { bubbles: true }));
    assert(!ctx.isDialogOpen() && ctx.active() === ctx.button(), 'a click on the backdrop closes it, to the button');
    ctx.button().click();
    ctx.q('.osa-privacy-modal').dispatchEvent(new ctx.window.MouseEvent('click', { bubbles: true }));
    assert(ctx.isDialogOpen(), 'a click inside the dialog does not');
  }
}

console.log('\nTab wraps inside the dialog when the page row is hidden (allowPageContext: false)');
{
  const ctx = await opened({ config: { allowPageContext: false } });
  const order = ['.osa-privacy-close-btn', '#osa-privacy-usage', '.osa-privacy-link'];
  const list = order.map((selector) => ctx.q(selector));
  list.at(-1).focus();
  const forward = ctx.key('Tab');
  ctx.active().dispatchEvent(forward);
  assert(forward.defaultPrevented && ctx.active() === list[0], 'Tab on the last control goes to the first, not into the hidden row');
  const back = ctx.key('Tab', { shiftKey: true });
  ctx.active().dispatchEvent(back);
  assert(back.defaultPrevented && ctx.active() === list.at(-1), 'Shift+Tab on the first goes to the last');
  ctx.usage().focus();
  const middle = ctx.key('Tab');
  ctx.active().dispatchEvent(middle);
  assert(!middle.defaultPrevented, 'and Tab on the usage choice is left to the browser');
}

console.log('\na choice made in another tab applies here without a reload');
{
  const ctx = await start();
  assert(!('share_usage' in (await send(ctx, 'Before')).body), 'sanity: sharing, as it starts');
  // Another tab, pop-out or notebook tab saves its choice to the same storage.
  ctx.window.localStorage.setItem(USAGE_KEY, 'false');
  ctx.window.localStorage.setItem(PAGE_KEY, 'false');
  const body = (await send(ctx, 'After')).body;
  assertEqual(body.share_usage, false, 'the next request carries the other tab\'s usage choice');
  assertEqual(body.page_context, { dataset_id: DATASET }, 'and its page choice');
  ctx.window.localStorage.setItem(USAGE_KEY, 'true');
  assert(!('share_usage' in (await send(ctx, 'Back on'))).body, 'turning it back on elsewhere is picked up too');
  ctx.window.localStorage.setItem(USAGE_KEY, 'false');
  ctx.api.open();
  await waitUntil(() => ctx.q('.osa-chat-window').classList.contains('open'), 'the window opens');
  ctx.button().click();
  assert(!ctx.usage().checked, 'a dialog opened afterwards shows the choice that holds');
}

console.log('\na choice the browser will not save is said so, and still holds until reload');
{
  const ctx = await opened({ saves: false, storage: { [USAGE_KEY]: 'true' } });
  assertEqual(ctx.q('.osa-privacy-save-note').textContent, '', 'no note before any choice');
  const warn = console.warn;
  console.warn = () => {};
  choose(ctx, ctx.usage(), false);
  console.warn = warn;
  const note = ctx.q('.osa-privacy-save-note');
  assert(note.textContent.includes('could not be saved'), 'the dialog says the choice could not be saved');
  assertEqual(note.getAttribute('role'), 'status', 'in a status region, so a screen reader hears it');
  // Storage still holds the older answer (sharing); the reader's newer choice outranks it.
  assertEqual((await send(ctx, 'After a failed save')).body.share_usage, false, 'the request still follows the choice the reader made');
}

console.log('\nthe note follows every unsaved choice, not the last save');
{
  // Only the page choice cannot be saved; the usage choice saves cleanly afterwards.
  const ctx = await opened({ blockedKeys: [PAGE_KEY] });
  const warn = console.warn;
  console.warn = () => {};
  choose(ctx, ctx.page(), false);
  console.warn = warn;
  const note = () => ctx.q('.osa-privacy-save-note').textContent;
  assert(note().includes('could not be saved'), 'sanity: the failed page save is noted');
  choose(ctx, ctx.usage(), false);
  assertEqual(ctx.stored(USAGE_KEY), 'false', 'sanity: the usage choice did save');
  assert(note().includes('could not be saved'), 'a later clean save does not clear the note for the page choice that did not save');
  // Closing and reopening the dialog does not clear it either.
  ctx.q('.osa-privacy-close-btn').click();
  ctx.button().click();
  assert(ctx.isDialogOpen(), 'sanity: the dialog reopens');
  assert(note().includes('could not be saved'), 'and a reopened dialog still says so');
}

console.log('\na choice that saves leaves no note');
{
  const ctx = await opened();
  choose(ctx, ctx.usage(), false);
  choose(ctx, ctx.page(), false);
  assertEqual(ctx.q('.osa-privacy-save-note').textContent, '', 'the note stays empty');
}

console.log('\nclosing the window closes the dialog, and it opens fresh');
{
  const ctx = await opened();
  ctx.api.close();
  assert(!ctx.isDialogOpen(), 'the dialog is closed with the window');
  ctx.api.open();
  assert(!ctx.isDialogOpen(), 'and is not still showing when the window opens again');
}

console.log('\nthe button does nothing while the window is closed');
{
  const ctx = await start();
  ctx.button().click();
  assert(!ctx.isDialogOpen(), 'no dialog opens from a closed window');
}

console.log('\n' + '='.repeat(60));
console.log(`${passed} passed, ${failed} failed`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
