/**
 * Paced reveal of streamed text (#531), run against the real widget source.
 *
 * The problem it answers: a reasoning model can think for tens of seconds and then
 * emit its whole answer in about a second, so a stream that is fine on the wire looks
 * like nothing and then everything. The widget now shows the delivered text at a
 * reading pace, faster only when that pace would fall seconds behind the stream, and
 * takes fenced code whole rather than typing it out.
 *
 * Three levels, each holding the widget to something different:
 *   - the planner (`nextRevealEnd`, `fencedRanges`) is pure, so its rules are checked
 *     on strings, including as properties over many burst sizes and block positions;
 *   - the controller (`createReveal`) runs on a fake clock, so pace, catch-up and the
 *     drain bound are checked in virtual time, exactly and fast;
 *   - the stream handler runs for real in a happy-dom window on real timers, fed a
 *     burst through a ReadableStream, and is observed the way a reader would: by what
 *     the message holds while the reply is still arriving.
 *
 * What stands in: `fetch` (never used; the stream is handed to the handler) and, for
 * the controller, the clock.
 *
 * Run with: bun frontend/test-widget-paced-streaming.js
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

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');

function loadWidget({ matchMedia } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  if (matchMedia) window.matchMedia = matchMedia;
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const config = { default_model: 'm', offered_models: [], widget: {}, client_tools: [], runtime: null };
  const fetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  window.OSAChatWidget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-paced' });
  window.OSAChatWidget.init();
  return { window, widget: window.OSAChatWidget, reveal: window.OSAChatWidget.__reveal, api: window.OSAChatWidget.__browser };
}

/** A clock a test turns by hand: timers fire in order as time is advanced. */
function fakeClock() {
  let time = 0;
  let nextId = 1;
  const timers = new Map();
  return {
    now: () => time,
    later: (fn, ms) => {
      const id = nextId++;
      timers.set(id, { at: time + ms, fn });
      return id;
    },
    unlater: (id) => { timers.delete(id); },
    pending: () => timers.size,
    /** Run timers until none is due within `ms` more milliseconds. */
    advance(ms) {
      const until = time + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        time = due[1].at;
        due[1].fn();
      }
      time = until;
    },
  };
}

const { reveal: R } = loadWidget();

console.log('='.repeat(60));
console.log('Widget: paced reveal of streamed text (#531)');
console.log('='.repeat(60));

// ---------------------------------------------------------------- planner

console.log('\nfencedRanges finds code the way the markdown renderer reads it');
{
  const text = 'a\n```python\nx = 1\n```\nb\n  ```\ny\n```\nc';
  const ranges = R.fencedRanges(text);
  assertEqual(ranges.map(([s, e]) => text.slice(s, e)), ['```python\nx = 1\n```\n', '  ```\ny\n```\n'],
    'two closed blocks, fence lines included, an indented fence too');
  const open = 'intro\n```\nprint(1)\nprint(';
  assertEqual(R.fencedRanges(open).map(([s, e]) => open.slice(s, e)), ['```\nprint(1)\nprint('],
    'a block that has not closed yet runs to the end of what has arrived');
  assertEqual(R.fencedRanges('no code, just `inline` and a ``` mid-line'), [], 'no fence line, no block');
  assertEqual(R.fencedRanges(''), [], 'nothing, no blocks');
}

console.log('\nnextRevealEnd: pace, word boundaries and code');
{
  const prose = 'Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu';
  assertEqual(R.nextRevealEnd(prose, 0, 0), 5, 'at least one character is always shown, and a word is finished (Alpha)');
  assertEqual(R.nextRevealEnd(prose, 0, 8), 10, 'a budget ending mid-word is carried to the end of that word');
  assertEqual(R.nextRevealEnd(prose, 5, 1000), prose.length, 'a budget past the end shows everything, no more');
  assertEqual(R.nextRevealEnd(prose, prose.length, 5), prose.length, 'nothing left, nothing changes');
  const longWord = 'x'.repeat(200);
  assertEqual(R.nextRevealEnd(longWord, 0, 10), 10, 'a word longer than the reach is cut at the budget, not run to its end');
  assertEqual(R.nextRevealEnd('ab cd', 0, 1), 2, 'a budget landing on a word boundary is left alone (or finishes the word)');

  const doc = 'Intro sentence here.\n\n```python\nx = 1\ny = 2\n```\n\nAfter the code.';
  const start = doc.indexOf('```python');
  const stop = doc.indexOf('```\n\nAfter') + 4;
  assertEqual(R.nextRevealEnd(doc, 0, 10), 14, 'text before the block is paced (10 characters, carried to the end of "sentence")');
  assert(R.nextRevealEnd(doc, 0, start - 1) <= start, 'a reveal that stops short of the fence stays short of it');
  assertEqual(R.nextRevealEnd(doc, 0, start + 3), stop, 'a reveal that reaches the fence takes the whole block');
  assertEqual(R.nextRevealEnd(doc, start, 1), stop, 'from the fence itself, the whole block at once');
  assertEqual(R.nextRevealEnd(doc, stop, 5), doc.indexOf('After') + 5, 'after the block, pacing resumes (five characters, to the end of the word)');

  const streaming = 'Intro.\n\n```\nprint(1)\nprint(2)';
  assertEqual(R.nextRevealEnd(streaming, 0, 12), streaming.length, 'a block still arriving is shown as far as it has arrived');
  assertEqual(R.nextRevealEnd(streaming + '\nprint(3)', streaming.length, 1), streaming.length + 9,
    'and what arrives inside it is shown at once, with no added delay');
}

console.log('\nproperty: stepping always ends, each step is a prefix, and code is never cut');
{
  const blocks = ['```py\na = 1\nb = 2\n```\n', '```\nsolo\n```\n', '  ```js\nlet x;\n```\n'];
  const prose = ['The Sensory-event tag marks a stimulus. ', 'Tags come from a schema, and a schema is versioned.\n\n', 'One more line.\n'];
  let cases = 0;
  let badPrefix = 0;
  let stuck = 0;
  let cutCode = 0;
  for (const budget of [1, 3, 7, 24, 100, 400]) {
    for (let mask = 0; mask < 27; mask++) {
      const parts = [];
      let m = mask;
      for (let i = 0; i < 3; i++) {
        parts.push(prose[m % 3]);
        parts.push(blocks[Math.floor(m / 3) % 3]);
        m = Math.floor(m / 3);
      }
      const text = parts.join('');
      const ranges = R.fencedRanges(text);
      let shown = 0;
      let steps = 0;
      while (shown < text.length && steps < 10000) {
        const next = R.nextRevealEnd(text, shown, budget);
        if (next <= shown) { stuck++; break; }
        if (next > text.length) badPrefix++;
        // No step may end strictly inside a block it did not finish.
        for (const [s, e] of ranges) if (next > s && next < e) cutCode++;
        shown = next;
        steps++;
      }
      if (shown !== text.length) stuck++;
      cases++;
    }
  }
  assert(cases === 162 && stuck === 0, `every one of ${cases} texts is revealed to the end in finite steps (stuck: ${stuck})`);
  assertEqual(badPrefix, 0, 'no step goes past the text');
  assertEqual(cutCode, 0, 'no step ends inside a closed code block');
}

// ------------------------------------------------------------- controller

function harness(text, options = {}) {
  const clock = fakeClock();
  const state = { text, shows: [] };
  const controller = R.createReveal({
    getText: () => state.text,
    show: (visible) => state.shows.push({ at: clock.now(), visible }),
    now: clock.now,
    later: clock.later,
    unlater: clock.unlater,
    ...options,
  });
  return { clock, state, controller };
}

const PROSE_1500 = ('The Sensory-event tag marks a sensory stimulus in a recording, and it belongs to the Event branch of the schema. ').repeat(14);

console.log('\na burst is spread over a reading pace, not shown at once');
{
  const { clock, state, controller } = harness(PROSE_1500);
  controller.kick();
  clock.advance(60);
  assertEqual(state.shows.length, 0, 'nothing is drawn before the first tick');
  clock.advance(60);
  assert(state.shows.length === 1, 'the first tick draws');
  assert(state.shows[0].visible.length > 0 && state.shows[0].visible.length < 100,
    `and only a first few words (${state.shows[0].visible.length} of ${PROSE_1500.length} characters)`);
  clock.advance(60_000);
  const last = state.shows.at(-1);
  assertEqual(last.visible, PROSE_1500, 'everything ends up shown');
  assert(state.shows.every((s, i) => PROSE_1500.startsWith(s.visible) && (i === 0 || s.visible.length >= state.shows[i - 1].visible.length)),
    'every step is a growing prefix of the text');
  assert(state.shows.length > 20, `in many steps (${state.shows.length}), not one`);
  assert(last.at >= 2000 && last.at <= 5000, `over a few seconds (${last.at} ms), not instantly and not slowly`);
  const speeds = state.shows.slice(1).map((s, i) => (s.visible.length - state.shows[i].visible.length) / ((s.at - state.shows[i].at) / 1000));
  assert(Math.max(...speeds) < 1700, `never faster than the catch-up allows (peak ${Math.round(Math.max(...speeds))} chars/s)`);
  assert(Math.min(...speeds.slice(0, -1)) >= 200, `nor slower than the floor pace (slowest ${Math.round(Math.min(...speeds.slice(0, -1)))} chars/s)`);
  assertEqual(clock.pending(), 0, 'and no timer is left running');
}

console.log('\na stream slower than the pace is shown as it arrives, with no delay added');
{
  const { clock, state, controller } = harness('');
  const words = 'a steady stream of small words arriving well below the floor pace of the reveal'.split(' ');
  const lag = [];
  for (const word of words) {
    state.text += `${word} `;
    controller.kick();
    clock.advance(120);
    lag.push(state.text.length - state.shows.at(-1).visible.length);
  }
  assert(lag.every((n) => n === 0), `after each word's tick the reader has all of it (lag ${Math.max(...lag)} chars)`);
}

console.log('\ntext that keeps arriving while the reveal runs is picked up');
{
  const { clock, state, controller } = harness('First part of the reply, which is long enough to take a while. '.repeat(6));
  controller.kick();
  clock.advance(500);
  const mid = state.shows.at(-1).visible.length;
  state.text += 'And the second part arrives later. '.repeat(6);
  controller.kick();
  clock.advance(60_000);
  assert(mid < state.text.length, 'the reveal was still running when the rest arrived');
  assertEqual(state.shows.at(-1).visible, state.text, 'and it went on to show all of it');
}

console.log('\na code block is shown whole, in one step, at its place in the reply');
{
  const before = 'Here is how to read an NWB file with PyNWB. The handle stays open while you read.\n\n';
  const code = '```python\nfrom pynwb import NWBHDF5IO\n\nwith NWBHDF5IO("file.nwb", "r") as io:\n    nwbfile = io.read()\n```\n';
  const after = '\nAfter that, `nwbfile.acquisition` holds the raw series, and each one has its data and timestamps.';
  const { clock, state, controller } = harness(before + code + after);
  controller.kick();
  clock.advance(60_000);
  const withoutCode = state.shows.filter((s) => !s.visible.includes('```'));
  const withCode = state.shows.filter((s) => s.visible.includes('```'));
  assert(withoutCode.length > 1, `the sentence before it is paced (${withoutCode.length} steps)`);
  assert(withCode.every((s) => s.visible.length >= (before + code).length), 'no step shows part of the block');
  const jump = withCode[0].visible.length - withoutCode.at(-1).visible.length;
  assert(jump >= code.length, `the block arrives in one step (${jump} characters at once)`);
  assert(withCode.length > 1, 'and the text after it is paced again');
}

console.log('\ncode that is still arriving passes straight through');
{
  const { clock, state, controller } = harness('Intro line.\n\n```python\nx = 1\n');
  controller.kick();
  clock.advance(1000);
  assertEqual(state.shows.at(-1).visible, state.text, 'everything that has arrived is shown, block included');
  state.text += 'y = 2\n';
  controller.kick();
  clock.advance(200);
  assertEqual(state.shows.at(-1).visible, state.text, 'and each line that follows is shown when it arrives');
}

console.log('\ndrain: waits for the pace to catch up, but only so long');
{
  const { clock, state, controller } = harness(PROSE_1500);
  controller.kick();
  let done = false;
  controller.drain().then(() => { done = true; });
  clock.advance(500);
  await Promise.resolve();
  assert(!done, 'a reply still being revealed is waited for');
  clock.advance(10_000);
  await Promise.resolve();
  assert(done, 'until it has all been shown');
  assertEqual(state.shows.at(-1).visible, PROSE_1500, 'which it has');

  const huge = harness('word '.repeat(40_000));
  huge.controller.kick();
  let hugeDone = false;
  huge.controller.drain().then(() => { hugeDone = true; });
  huge.clock.advance(R.REVEAL_DRAIN_MAX_MS - 50);
  await Promise.resolve();
  assert(!hugeDone, 'a very long reply is still being waited for just under the bound');
  huge.clock.advance(100);
  await Promise.resolve();
  assert(hugeDone, `and is shown in full at the bound (${R.REVEAL_DRAIN_MAX_MS} ms), not left to take minutes`);
  assertEqual(huge.state.shows.at(-1).visible.length, huge.state.text.length, 'all of it');

  const idle = harness('Nothing pending.');
  idle.controller.flush();
  let idleDone = false;
  idle.controller.drain().then(() => { idleDone = true; });
  await Promise.resolve();
  assert(idleDone, 'a reveal that has caught up drains at once');
}

console.log('\nflush shows everything now; stop abandons the pending redraw; unpaced shows on arrival');
{
  const a = harness(PROSE_1500);
  a.controller.kick();
  a.clock.advance(200);
  a.controller.flush();
  assertEqual(a.state.shows.at(-1).visible, PROSE_1500, 'flush: all of it');
  assertEqual(a.clock.pending(), 0, 'and no timer left');

  const b = harness(PROSE_1500);
  b.controller.kick();
  b.controller.stop();
  b.clock.advance(60_000);
  assertEqual(b.state.shows.length, 0, 'stop: nothing more is drawn');

  const c = harness('', { paced: false });
  c.state.text = PROSE_1500;
  c.controller.kick();
  assertEqual(c.state.shows.at(-1).visible, PROSE_1500, 'unpaced: the whole burst is shown the moment it arrives');
  assertEqual(c.clock.pending(), 0, 'with no timer');
}

// ------------------------------------------------- the stream handler, real

function sse(events, { gapMs = 0, delivered = null } = {}) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    async start(controller) {
      for (const [i, event] of events.entries()) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        if (delivered) delivered[i] = Date.now();
        if (gapMs) await new Promise((resolve) => setTimeout(resolve, gapMs));
      }
      controller.close();
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
}

/** Sample what the reply holds every few milliseconds until `finished` settles. */
async function watch(api, index, finished) {
  const seen = [];
  let over = false;
  finished.then(() => { over = true; }, () => { over = true; });
  while (!over) {
    const message = api.getMessages()[index];
    seen.push({ at: Date.now(), content: message ? message.content : null });
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return seen;
}

const REPLY = 'GPT-6 Luna reasons for a while and then answers quickly. This sentence is here so that the reply is long enough to see revealed over time rather than in one piece. '.repeat(3).trim();

console.log('\nthe widget shows a burst progressively, and the canonical text at the end');
{
  const { window, api, widget } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const stream = api.handleStreamingResponse(sse([
    { event: 'session', session_id: 's' },
    { event: 'content', content: REPLY },
    { event: 'done', session_id: 's', content: REPLY },
  ]), container);
  const seen = await watch(api, started, stream);
  await stream;
  const lengths = seen.map((s) => (s.content || '').length).filter((n) => n > 0);
  const distinct = [...new Set(lengths)];
  assert(distinct.length >= 5, `the reader saw the reply grow through ${distinct.length} different lengths, not one jump`);
  assert(distinct[0] < REPLY.length / 2, `the first thing shown was a beginning (${distinct[0]} of ${REPLY.length} characters)`);
  assert(seen.every((s) => s.content === null || REPLY.startsWith(s.content)), 'every state was a prefix of the reply');
  assertEqual(api.getMessages()[started].content, REPLY, 'after done the message holds the canonical text');
  const shown = [...window.document.querySelectorAll('.osa-message.assistant .osa-message-content')].at(-1);
  assert(shown && shown.textContent.includes('answers quickly'), 'and it is what the page shows');
  assertEqual(widget.__reveal !== undefined, true, 'the test hooks are present only in test mode');
}

console.log('\na code block in a streamed reply is never on the page half-typed');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const code = '```python\nfrom pynwb import NWBHDF5IO\nwith NWBHDF5IO("f.nwb", "r") as io:\n    nwbfile = io.read()\n```\n\n';
  const text = `Open the file like this, then read from the handle while it is open.\n\n${code}The series are under acquisition, and each has data and timestamps to read.`;
  const stream = api.handleStreamingResponse(sse([
    { event: 'content', content: text },
    { event: 'done', content: text },
  ]), container);
  const codeTexts = new Set();
  let over = false;
  stream.then(() => { over = true; }, () => { over = true; });
  let sawTextWithoutCode = false;
  while (!over) {
    const drawn = [...window.document.querySelectorAll('.osa-message.assistant pre code')].at(-1);
    if (drawn) codeTexts.add(drawn.textContent);
    const content = (api.getMessages()[started] || {}).content || '';
    if (content && !content.includes('```')) sawTextWithoutCode = true;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  await stream;
  assert(sawTextWithoutCode, 'the sentence before the block was on the page first');
  assertEqual(codeTexts.size, 1, 'and the code was drawn once');
  assertEqual([...codeTexts][0], 'from pynwb import NWBHDF5IO\nwith NWBHDF5IO("f.nwb", "r") as io:\n    nwbfile = io.read()',
    'complete');
}

console.log('\na stream that arrives slowly is not held back');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const words = ['One ', 'word ', 'at ', 'a ', 'time, ', 'each ', 'well ', 'inside ', 'the ', 'pace.'];
  const delivered = [];
  const stream = api.handleStreamingResponse(sse(
    [...words.map((content) => ({ event: 'content', content })), { event: 'done', content: words.join('') }],
    { gapMs: 120, delivered },
  ), container);
  const trail = [];
  let over = false;
  stream.then(() => { over = true; }, () => { over = true; });
  while (!over) {
    trail.push({ at: Date.now(), content: (api.getMessages()[started] || {}).content || '' });
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  await stream;
  // The moment each word first appears on the page, against the moment it arrived.
  const delays = words.map((_, i) => {
    const prefix = words.slice(0, i + 1).join('');
    const appeared = trail.find((seen) => seen.content.length >= prefix.length);
    return appeared ? appeared.at - delivered[i] : Infinity;
  });
  assert(delays.every((ms) => ms < 250), `every word was on the page within a tick or two of arriving (slowest ${Math.max(...delays)} ms)`);
  assertEqual(api.getMessages()[started].content, words.join(''), 'and the reply is complete');
}

console.log('\na reader who asked for reduced motion gets text as it arrives');
{
  const { window, api } = loadWidget({ matchMedia: (query) => ({ matches: /prefers-reduced-motion/.test(query), media: query }) });
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const stream = api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'done', content: REPLY },
  ], { gapMs: 30 }), container);
  const seen = await watch(api, started, stream);
  await stream;
  const firstText = seen.find((s) => s.content);
  assertEqual(firstText && firstText.content, REPLY, 'the whole burst is shown the moment it arrives');
}

console.log('\nan error mid-reply keeps what arrived and reports the error, without waiting for the pace');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  let error = null;
  const begun = Date.now();
  try {
    await api.handleStreamingResponse(sse([
      { event: 'content', content: REPLY },
      { event: 'error', message: 'the model went away' },
    ]), container);
  } catch (err) {
    error = err;
  }
  assert(error && /the model went away/.test(error.message), 'the error is raised');
  const content = api.getMessages()[started].content;
  assert(content.startsWith(REPLY) && /the model went away/.test(content), 'the message holds everything that arrived, then the error');
  assert(Date.now() - begun < 1500, `promptly (${Date.now() - begun} ms)`);
}

console.log('\na reply that ends on a browser call shows its text in full before handing it back');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const result = await api.handleStreamingResponse(sse([
    { event: 'session', session_id: 's' },
    { event: 'content', content: REPLY },
    { event: 'tool_request', session_id: 's', call_id: 'c1', tool: 'execute_code', args: {}, content: REPLY, citations: [] },
  ]), container);
  assert(result && result.toolRequest && result.toolRequest.call_id === 'c1', 'the request is handed back');
  assertEqual(api.getMessages()[started].content, REPLY, 'with the whole of the text before it on the page');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed} checks, passed: ${passed}, failed: ${failed}`);
process.exit(failed === 0 ? 0 : 1);
