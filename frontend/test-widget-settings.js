/**
 * The widget's Settings dialog: the model choice first, and the reader's own API key and
 * model name only for "Custom", run against the real widget source in a happy-dom window,
 * the way test-widget-first-paint.js runs the first paint.
 *
 * What it holds the widget to: the model menu is the first field; the model name and the
 * API key are hidden until Custom is chosen and hidden again when it is not; a key that is
 * already saved stays in view whatever model is chosen, so it can be seen and removed; a
 * custom model cannot be saved without a key or without a model name; a key the dialog is
 * not showing is not saved; a saved setting comes back into the dialog as it was; and a
 * custom model name is held to the server's list of valid and invalid ids
 * (tests/fixtures/model_ids.json), variant suffixes such as ":nitro" included.
 *
 * What stands in: `fetch` (an HTTP fixture for the community config, whose `offered_models`
 * fill the menu). Storage is the window's own localStorage, read back to see what was saved.
 *
 * Run with: bun frontend/test-widget-settings.js
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
const SETTINGS_KEY = 'osa-settings-hed';
const ANTHROPIC_KEY = `sk-ant-${'a'.repeat(90)}`;
const OPENROUTER_KEY = `sk-or-v1-${'b'.repeat(64)}`;

const CONFIG = {
  default_model: 'claude-haiku-4-5',
  offered_models: [
    { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5' },
    { id: 'claude-sonnet-5-5', label: 'Claude Sonnet 5.5' },
    { id: 'openai.gpt-6-luna', label: 'OpenAI GPT-6 Luna', platform_only: true },
  ],
  widget: {},
  client_tools: [],
  runtime: null,
};

// A fetch fixture for one page load, remembering whether the community config was served.
function makeConfigFetch() {
  const state = { served: false };
  const fetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    state.served = true;
    return new Response(JSON.stringify(CONFIG), { status: 200, headers: { 'content-type': 'application/json' } });
  };
  return { fetch, state };
}

// A fresh page load; `saved` seeds the reader's saved settings.
function loadWidget({ saved = null } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  if (saved) window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(saved));
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const { fetch, state } = makeConfigFetch();
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  return { window, widget: window.OSAChatWidget, state };
}

// The widget with its chat open and its Settings dialog showing, as a reader sees it.
async function openSettingsDialog(options) {
  const { window, widget, state } = loadWidget(options);
  widget.setConfig({ apiEndpoint: API, communityId: 'hed', storageKey: 'osa-test-settings' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const q = (selector) => container.querySelector(selector);
  await waitUntil(() => q('.osa-chat-button'), 'the chat button');
  // The menu is built from the community config's offered_models, so wait for it to land.
  await waitUntil(() => state.served, 'the community config is served');
  await new Promise((resolve) => setTimeout(resolve, 30));
  q('.osa-chat-button').dispatchEvent(new window.Event('click', { bubbles: true }));
  q('.osa-settings-btn-open').dispatchEvent(new window.Event('click', { bubbles: true }));
  await waitUntil(() => q('.osa-settings-overlay').classList.contains('open'), 'the Settings dialog opens');
  return { window, q, saved: () => JSON.parse(window.localStorage.getItem(SETTINGS_KEY) || 'null') };
}

const shown = (q, selector) => q(selector).style.display !== 'none';
const click = (window, element) => element.dispatchEvent(new window.Event('click', { bubbles: true }));

function choose(window, q, value) {
  const select = q('#osa-settings-model');
  select.value = value;
  select.dispatchEvent(new window.Event('change', { bubbles: true }));
}

console.log('='.repeat(60));
console.log('Widget: Settings, model first');
console.log('='.repeat(60));

console.log('\nthe model menu comes first, and the key and model name are out of the way');
{
  const { q } = await openSettingsDialog();
  assert(q('.osa-settings-body').firstElementChild.contains(q('#osa-settings-model')), 'the model menu is the first field');
  assert(!shown(q, '#osa-settings-api-key-field'), 'the API key field is hidden');
  assert(!shown(q, '#osa-settings-custom-model-field'), 'the model name field is hidden');
  const options = [...q('#osa-settings-model').options].map((o) => o.value);
  assertEqual(options, ['default', 'claude-sonnet-5-5', 'openai.gpt-6-luna', 'custom'], 'the menu lists the default, the other offered models, and Custom');
}

console.log('\nchoosing Custom brings up the model name and the key; choosing an offered model puts them away');
{
  const { window, q } = await openSettingsDialog();
  choose(window, q, 'custom');
  assert(shown(q, '#osa-settings-custom-model-field'), 'Custom shows the model name');
  assert(shown(q, '#osa-settings-api-key-field'), 'Custom shows the API key');
  choose(window, q, 'claude-sonnet-5-5');
  assert(!shown(q, '#osa-settings-custom-model-field'), 'an offered model hides the model name');
  assert(!shown(q, '#osa-settings-api-key-field'), 'and the API key');
}

console.log('\na key the reader already saved stays in view, so it can be seen and removed');
{
  const { q } = await openSettingsDialog({ saved: { apiKey: ANTHROPIC_KEY, model: null } });
  assert(shown(q, '#osa-settings-api-key-field'), 'the saved key is shown with the default model');
  assertEqual(q('#osa-settings-api-key').value, ANTHROPIC_KEY, 'and filled in');
  assert(!shown(q, '#osa-settings-custom-model-field'), 'the model name stays hidden');
}

console.log('\nthe key field stays in view while it has focus, and is put away once focus leaves it');
{
  // In a browser, hiding the focused field drops focus to the page, so a reader who
  // empties the field with the keyboard would have to tab in from the top again.
  const { window, q } = await openSettingsDialog({ saved: { apiKey: ANTHROPIC_KEY, model: null } });
  const input = q('#osa-settings-api-key');
  input.focus();
  assert(window.document.activeElement === input, 'the key field has focus');
  input.value = ANTHROPIC_KEY.slice(0, -1);
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  input.value = '';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  assert(shown(q, '#osa-settings-api-key-field'), 'emptied while focused with an offered model, it stays in view');
  assert(window.document.activeElement === input, 'and keeps focus');
  input.blur();
  assert(!shown(q, '#osa-settings-api-key-field'), 'once focus leaves it, it is put away');
}

console.log('\nthe emptied key field waits out a click that began on Save, so the click lands');
{
  // The dialog is centred: hiding the field mid-press would move Save out from under
  // the pointer. The press, not where focus went, is what is watched: Safari and
  // Firefox on a Mac do not focus the button.
  const { window, q, saved } = await openSettingsDialog({ saved: { apiKey: ANTHROPIC_KEY, model: null } });
  const input = q('#osa-settings-api-key');
  input.focus();
  input.value = '';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  const save = q('.osa-settings-btn-save');
  save.dispatchEvent(new window.Event('pointerdown', { bubbles: true }));
  input.blur();
  assert(shown(q, '#osa-settings-api-key-field'), 'blurred by a press, the field has not moved anything yet');
  save.dispatchEvent(new window.Event('pointerup', { bubbles: true }));
  click(window, save);
  assertEqual(saved(), { apiKey: null, model: null, keyProvider: null }, 'the click on Save saved the removal of the key');
  assert(!shown(q, '#osa-settings-api-key-field'), 'and the field is put away after the click');
}

console.log('\na saved custom model comes back as Custom, with its key');
{
  const { q } = await openSettingsDialog({ saved: { apiKey: OPENROUTER_KEY, model: 'openai/gpt-5' } });
  assertEqual(q('#osa-settings-model').value, 'custom', 'the menu is on Custom');
  assertEqual(q('#osa-settings-custom-model').value, 'openai/gpt-5', 'the model name is filled in');
  assertEqual(q('#osa-settings-api-key').value, OPENROUTER_KEY, 'and so is the key');
  assert(shown(q, '#osa-settings-custom-model-field') && shown(q, '#osa-settings-api-key-field'), 'both are showing');
}

console.log('\na saved retired model id comes back as the model that replaced it');
{
  const { q } = await openSettingsDialog({ saved: { apiKey: null, model: 'claude-sonnet-5' } });
  assertEqual(q('#osa-settings-model').value, 'claude-sonnet-5-5', 'the menu is on Claude Sonnet 5.5, not Custom');
}

console.log('\nsaving an offered model keeps no key that was not showing');
{
  const { window, q, saved } = await openSettingsDialog();
  choose(window, q, 'custom');
  q('#osa-settings-api-key').value = OPENROUTER_KEY;
  choose(window, q, 'claude-sonnet-5-5');
  // The typed key keeps the field in view (it is the reader's to remove) ...
  assert(shown(q, '#osa-settings-api-key-field'), 'a typed key stays in view when the model changes');
  q('#osa-settings-api-key').value = '';
  choose(window, q, 'openai.gpt-6-luna');
  // ... and once it is empty and not Custom, nothing is left to save.
  click(window, q('.osa-settings-btn-save'));
  assertEqual(saved(), { apiKey: null, model: 'openai.gpt-6-luna', keyProvider: null }, 'the offered model is saved with no key');
}

console.log('\na custom model cannot be saved without a key or without a name');
{
  const { window, q, saved } = await openSettingsDialog();
  choose(window, q, 'custom');
  q('#osa-settings-custom-model').value = 'openai/gpt-5';
  click(window, q('.osa-settings-btn-save'));
  assertEqual(saved(), null, 'no key: nothing is saved');
  assert(q('.osa-error').textContent.includes('needs your own API key'), 'and the reader is told why');

  q('#osa-settings-api-key').value = OPENROUTER_KEY;
  q('#osa-settings-custom-model').value = '';
  click(window, q('.osa-settings-btn-save'));
  assertEqual(saved(), null, 'no model name: nothing is saved');
  assert(q('.osa-error').textContent.includes('custom model name'), 'and the reader is told why');
}

console.log('\na custom model with a key is saved, and its provider is read from the key');
{
  const { window, q, saved } = await openSettingsDialog();
  choose(window, q, 'custom');
  q('#osa-settings-custom-model').value = 'openai/gpt-5';
  q('#osa-settings-api-key').value = OPENROUTER_KEY;
  click(window, q('.osa-settings-btn-save'));
  assertEqual(saved(), { apiKey: OPENROUTER_KEY, model: 'openai/gpt-5', keyProvider: 'openrouter' }, 'the model and key are saved');
}

console.log('\na key in the wrong format is refused');
{
  const { window, q, saved } = await openSettingsDialog();
  choose(window, q, 'custom');
  q('#osa-settings-custom-model').value = 'openai/gpt-5';
  q('#osa-settings-api-key').value = 'not-a-key';
  click(window, q('.osa-settings-btn-save'));
  assertEqual(saved(), null, 'nothing is saved');
  assert(q('.osa-error').textContent.includes('Invalid API key format'), 'and the reader is told why');
}

console.log('\nremoving a saved key and saving an offered model clears the key');
{
  const { window, q, saved } = await openSettingsDialog({ saved: { apiKey: ANTHROPIC_KEY, model: null } });
  q('#osa-settings-api-key').value = '';
  choose(window, q, 'claude-sonnet-5-5');
  click(window, q('.osa-settings-btn-save'));
  assertEqual(saved(), { apiKey: null, model: 'claude-sonnet-5-5', keyProvider: null }, 'the key is gone');
}

console.log('\nnext to the reader\'s own Anthropic key, the models only the service can run are unavailable');
{
  const { window, q } = await openSettingsDialog();
  const luna = () => [...q('#osa-settings-model').options].find((o) => o.value === 'openai.gpt-6-luna');
  const sonnet = () => [...q('#osa-settings-model').options].find((o) => o.value === 'claude-sonnet-5-5');
  assert(!luna().disabled, 'without a key, the service-only model can be chosen');
  choose(window, q, 'custom');
  q('#osa-settings-api-key').value = ANTHROPIC_KEY;
  q('#osa-settings-api-key').dispatchEvent(new window.Event('input', { bubbles: true }));
  assert(luna().disabled, 'an Anthropic key disables it');
  assert(luna().textContent.includes('not with your own Anthropic key'), 'and says why');
  assert(!sonnet().disabled, 'a Claude model stays available');
  q('#osa-settings-api-key').value = '';
  q('#osa-settings-api-key').dispatchEvent(new window.Event('input', { bubbles: true }));
  assert(!luna().disabled && luna().textContent === 'OpenAI GPT-6 Luna', 'removing the key restores it');
  q('#osa-settings-api-key').value = OPENROUTER_KEY;
  q('#osa-settings-api-key').dispatchEvent(new window.Event('input', { bubbles: true }));
  assert(!luna().disabled, 'an OpenRouter key runs the same model there, so it stays available');
}

console.log('\nan Anthropic key with a service-only model cannot be saved');
{
  const { window, q, saved } = await openSettingsDialog();
  choose(window, q, 'custom');
  q('#osa-settings-api-key').value = ANTHROPIC_KEY;
  choose(window, q, 'openai.gpt-6-luna');
  click(window, q('.osa-settings-btn-save'));
  assertEqual(saved(), null, 'nothing is saved');
  assert(q('.osa-error').textContent.includes('cannot be used with your own Anthropic API key'), 'and the reader is told why');
}

console.log('\nan OpenRouter key with a service-only model is saved');
{
  const { window, q, saved } = await openSettingsDialog();
  choose(window, q, 'custom');
  q('#osa-settings-api-key').value = OPENROUTER_KEY;
  choose(window, q, 'openai.gpt-6-luna');
  click(window, q('.osa-settings-btn-save'));
  assertEqual(saved(), { apiKey: OPENROUTER_KEY, model: 'openai.gpt-6-luna', keyProvider: 'openrouter' }, 'the pair is saved');
}

// The ids the server's community config validator accepts and refuses (issue #552): one
// list, read here and by tests/test_core/test_model_id_corpus.py, so the widget refuses
// exactly what the server would and nothing more. Its variants (":nitro", ":floor",
// ":free") are OpenRouter's, and the dialog used to refuse them.
const MODEL_IDS = JSON.parse(
  readFileSync(new URL('../tests/fixtures/model_ids.json', import.meta.url), 'utf8')
);

console.log('\na custom model the server accepts is saved, variant suffixes included');
for (const modelId of MODEL_IDS.valid) {
  const { window, q, saved } = await openSettingsDialog();
  choose(window, q, 'custom');
  q('#osa-settings-custom-model').value = modelId;
  q('#osa-settings-api-key').value = OPENROUTER_KEY;
  click(window, q('.osa-settings-btn-save'));
  assertEqual(
    saved(),
    { apiKey: OPENROUTER_KEY, model: modelId, keyProvider: 'openrouter' },
    `${modelId.length > 60 ? `${modelId.slice(0, 20)}... (${modelId.length} characters)` : modelId} is saved`
  );
}

console.log('\na custom model the server would refuse is refused, with the reason');
for (const modelId of MODEL_IDS.invalid) {
  const { window, q, saved } = await openSettingsDialog();
  choose(window, q, 'custom');
  q('#osa-settings-custom-model').value = modelId;
  q('#osa-settings-api-key').value = OPENROUTER_KEY;
  click(window, q('.osa-settings-btn-save'));
  const label = modelId.length > 60 ? `${modelId.slice(0, 20)}... (${modelId.length} characters)` : JSON.stringify(modelId);
  assertEqual(saved(), null, `${label} is not saved`);
  assert(q('.osa-error').textContent.includes('Invalid model format'), `and the reader is told why (${label})`);
}

console.log('\nthe refusal says a :variant is allowed');
{
  const { window, q } = await openSettingsDialog();
  choose(window, q, 'custom');
  q('#osa-settings-custom-model').value = 'not a model';
  q('#osa-settings-api-key').value = OPENROUTER_KEY;
  click(window, q('.osa-settings-btn-save'));
  assert(q('.osa-error').textContent.includes(':nitro'), 'the message names a variant such as :nitro');
}

console.log('\na saved model with a variant suffix comes back as Custom, kept');
{
  const slug = 'openai/gpt-oss-120b:nitro';
  const { window, q } = await openSettingsDialog({ saved: { apiKey: OPENROUTER_KEY, model: slug } });
  assertEqual(q('#osa-settings-model').value, 'custom', 'the menu is on Custom');
  assertEqual(q('#osa-settings-custom-model').value, slug, 'the model name is kept whole');
  assert(!window.document.body.textContent.includes('was ignored'), 'and no notice says it was dropped');
}

console.log(`\nTotal: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
process.exit(failed ? 1 : 0);
