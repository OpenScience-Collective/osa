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

/**
 * Timers the widget starts, so a test can see that none is left running. Recorded with
 * their delay: the reveal's are its tick and its drain guard, and the widget starts
 * others of its own (an error banner's dismissal) that are not the reveal's to clear.
 */
function timerTracker() {
  const live = new Map();
  return {
    setTimeout: (fn, ms, ...rest) => {
      const id = setTimeout((...args) => { live.delete(id); fn(...args); }, ms, ...rest);
      live.set(id, ms);
      return id;
    },
    clearTimeout: (id) => { live.delete(id); clearTimeout(id); },
    revealTimers: () => [...live.values()].filter((ms) => ms === R.REVEAL_TICK_MS || ms === R.REVEAL_DRAIN_MAX_MS).length,
  };
}

function loadWidget({ matchMedia, timers = null } = {}) {
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
    TextDecoder, timers ? timers.setTimeout : setTimeout, timers ? timers.clearTimeout : clearTimeout, console);
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
    'and what arrives inside it is shown at once, with no pacing');
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

console.log('\na stream slower than the pace is shown as it arrives, at most one tick late');
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
  c.clock.advance(R.REVEAL_TICK_MS);
  assertEqual(c.state.shows.at(-1).visible, PROSE_1500, 'unpaced: the whole burst is shown within a tick of arriving');
  assertEqual(c.state.shows.length, 1, 'in one redraw');
  assertEqual(c.clock.pending(), 0, 'with no timer left');
}

console.log('\nunpaced: chunks are gathered into one redraw per tick, not one redraw each');
{
  // 150 per-token chunks in one network read, then a steady 60 tokens per second.
  const burst = harness('', { paced: false });
  for (let i = 0; i < 150; i++) {
    burst.state.text += 'tok ';
    burst.controller.kick();
  }
  burst.clock.advance(R.REVEAL_TICK_MS);
  assertEqual(burst.state.shows.length, 1, 'a 150 chunk burst is one redraw');
  assertEqual(burst.state.shows.at(-1).visible, burst.state.text, 'showing all of it');

  const steady = harness('', { paced: false });
  for (let ms = 0; ms < 1000; ms += 17) {
    steady.state.text += 'tok ';
    steady.controller.kick();
    steady.clock.advance(17);
  }
  steady.clock.advance(R.REVEAL_TICK_MS);
  assert(steady.state.shows.length <= 14, `60 chunks a second is at most about 12 redraws a second (${steady.state.shows.length})`);
  assertEqual(steady.state.shows.at(-1).visible, steady.state.text, 'and the last of it is shown');
}

console.log('\na redraw that throws is not lost in a timer: nobody waits for good, and it is handed back');
{
  const boom = new Error('redraw failed');
  const { clock, state, controller } = harness(PROSE_1500, {
    show: () => { throw boom; },
  });
  controller.kick();
  let drained = false;
  controller.drain().then(() => { drained = true; });
  clock.advance(R.REVEAL_TICK_MS * 2);
  await Promise.resolve();
  assert(drained, 'a drain that was waiting is released');
  let caught = null;
  try { controller.check(); } catch (err) { caught = err; }
  assert(caught === boom, 'check hands back what show threw');
  assertEqual(clock.pending(), 0, 'no timer is left running, and no guard timer either');
  controller.kick();
  assertEqual(clock.pending(), 0, 'a failed reveal does not restart');
  let again = false;
  controller.drain().then(() => { again = true; });
  await Promise.resolve();
  assert(again, 'and a later drain resolves at once');

  const noFailure = harness('fine');
  noFailure.controller.flush();
  let quiet = true;
  try { noFailure.controller.check(); } catch (err) { quiet = false; }
  assert(quiet, 'check is silent when nothing failed');
}

console.log('\na reveal step never ends inside a citation marker or a surrogate pair');
{
  const claim = 'Sensory-event marks a stimulus.[12] Tags come from a schema.[3][4] Done.';
  const inside = (end) => /\[\d*$/.test(claim.slice(0, end)) && /^\d*\]/.test(claim.slice(end));
  let cuts = 0;
  for (let budget = 1; budget <= 30; budget++) {
    let shown = 0;
    while (shown < claim.length) {
      shown = R.nextRevealEnd(claim, shown, budget);
      if (inside(shown)) cuts++;
    }
  }
  assertEqual(cuts, 0, 'no reveal of any pace stops inside [12], [3] or [4]');
  const eight = 'a' + '[1][2][3][4][5][6][7][8]' + 'b';
  let chained = 0;
  for (let budget = 1; budget <= 6; budget++) {
    let shown = 0;
    while (shown < eight.length) {
      shown = R.nextRevealEnd(eight, shown, budget);
      if (/\[\d*$/.test(eight.slice(0, shown)) && /^\d*\]/.test(eight.slice(shown))) chained++;
    }
  }
  assertEqual(chained, 0, 'nor inside a run of eight chained markers');
  const emoji = '\u4e2d\u6587\u{1F600}\u{1F600}\u{1F600}\u{1F600}\u{1F600}\u{1F600}\u{1F600}\u{1F600}';
  let split = 0;
  for (let budget = 1; budget <= 5; budget++) {
    let shown = 0;
    while (shown < emoji.length) {
      shown = R.nextRevealEnd(emoji, shown, budget);
      if (/[\uD800-\uDBFF]$/.test(emoji.slice(0, shown))) split++;
    }
  }
  assertEqual(split, 0, 'nor between the halves of an emoji, in text with no spaces to break on');
  assertEqual(R.nextRevealEnd('see [12', 0, 5), 7, 'a marker that has not finished arriving is shown as far as it has');
  assertEqual(R.nextRevealEnd('x [note that is long] y', 0, 4), 7, 'a bracket that is not a citation marker is cut like any other text (to the end of the word)');
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
  const CANONICAL = `${REPLY} [1]`;
  const stream = api.handleStreamingResponse(sse([
    { event: 'session', session_id: 's' },
    { event: 'content', content: REPLY },
    { event: 'done', session_id: 's', content: CANONICAL, citations: [{ marker: 1, source: 'https://a.example', title: 'A', cited_text: '' }] },
  ]), container);
  const seen = await watch(api, started, stream);
  await stream;
  const lengths = seen.map((s) => (s.content || '').length).filter((n) => n > 0);
  const distinct = [...new Set(lengths)];
  assert(distinct.length >= 5, `the reader saw the reply grow through ${distinct.length} different lengths, not one jump`);
  assert(distinct[0] < REPLY.length * 0.9, `the first thing shown was a beginning (${distinct[0]} of ${REPLY.length} characters)`);
  assert(seen.every((s) => s.content === null || CANONICAL.startsWith(s.content)), 'every state was a prefix of the reply');
  assertEqual(api.getMessages()[started].content, CANONICAL, 'after done the message holds the canonical text, which the stream did not carry');
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
  assert(delays.every((ms) => ms < 400), `every word was on the page within a tick or two of arriving (slowest ${Math.max(...delays)} ms)`);
  assertEqual(api.getMessages()[started].content, words.join(''), 'and the reply is complete');
}

console.log('\na reader who asked for reduced motion gets text as it arrives');
{
  const { window, api } = loadWidget({ matchMedia: (query) => ({ matches: /prefers-reduced-motion/.test(query), media: query }) });
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const begun = Date.now();
  // The reply's text arrives at once, and `done` a quarter of a second later, so what
  // the reader had in between can be seen.
  const stream = api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'done', content: REPLY },
  ], { gapMs: 250 }), container);
  const seen = await watch(api, started, stream);
  await stream;
  const firstText = seen.find((s) => s.content);
  assertEqual(firstText && firstText.content, REPLY, 'the whole burst is shown, not paced');
  assert(firstText && firstText.at - begun < 200, `within a tick of arriving, before done (${firstText && firstText.at - begun} ms)`);
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

/** Count the times the widget redraws its message list. */
function countRedraws(window) {
  const el = window.document.querySelector('.osa-chat-messages');
  let proto = Object.getPrototypeOf(el);
  while (proto && !Object.getOwnPropertyDescriptor(proto, 'innerHTML')) proto = Object.getPrototypeOf(proto);
  const descriptor = Object.getOwnPropertyDescriptor(proto, 'innerHTML');
  const counter = { redraws: 0 };
  Object.defineProperty(el, 'innerHTML', {
    configurable: true,
    get() { return descriptor.get.call(this); },
    set(value) { counter.redraws++; descriptor.set.call(this, value); },
  });
  return counter;
}

console.log('\na reader who asked for reduced motion does not pay for a redraw per chunk');
{
  const { window, api } = loadWidget({ matchMedia: (query) => ({ matches: /prefers-reduced-motion/.test(query), media: query }) });
  const container = window.document.querySelector('.osa-chat-widget');
  const counter = countRedraws(window);
  const started = api.getMessages().length;
  const tokens = Array.from({ length: 150 }, (_, i) => `word${i} `);
  const full = tokens.join('');
  await api.handleStreamingResponse(sse([
    ...tokens.map((content) => ({ event: 'content', content })),
    { event: 'done', content: full },
  ]), container);
  assertEqual(api.getMessages()[started].content, full, 'all of the reply is there');
  assert(counter.redraws <= 6, `150 chunks in one read cost ${counter.redraws} redraws of the conversation, not 150`);
}

console.log('\na paced reveal redraws at most about a dozen times a second');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const counter = countRedraws(window);
  const began = Date.now();
  const text = 'A reply long enough that the reveal takes a couple of seconds to show all of it, sentence by sentence. '.repeat(14);
  await api.handleStreamingResponse(sse([{ event: 'content', content: text }, { event: 'done', content: text }]), container);
  const seconds = (Date.now() - began) / 1000;
  assert(counter.redraws / seconds <= 14, `${counter.redraws} redraws over ${seconds.toFixed(1)} s`);
}

console.log('\na redraw that throws mid-reply fails the reply at once, not never');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const messagesEl = container.querySelector('.osa-chat-messages');
  const stream = api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'done', content: REPLY },
  ], { gapMs: 50 }), container);
  await new Promise((resolve) => setTimeout(resolve, 20));
  messagesEl.className = 'not-the-messages'; // the redraw's own lookup now finds nothing
  const outcome = await Promise.race([
    stream.then(() => 'resolved', (err) => `rejected: ${err.message.slice(0, 40)}`),
    new Promise((resolve) => setTimeout(() => resolve('still pending'), 6000)),
  ]);
  assert(/^rejected/.test(outcome), `the stream ends in an error the caller can handle (${outcome})`);
}

console.log('\nno timer is left running after any way a stream can end');
{
  const endings = {
    done: [{ event: 'content', content: REPLY }, { event: 'done', content: REPLY }],
    'no done': [{ event: 'content', content: REPLY }],
    'error event': [{ event: 'content', content: REPLY }, { event: 'error', message: 'gone' }],
    'browser call': [{ event: 'content', content: REPLY }, { event: 'tool_request', call_id: 'c', tool: 't', args: {}, content: REPLY }],
    'nothing at all': [],
  };
  for (const [name, events] of Object.entries(endings)) {
    const timers = timerTracker();
    const { window, api } = loadWidget({ timers });
    const container = window.document.querySelector('.osa-chat-widget');
    const before = api.getMessages().length;
    const outcome = await api.handleStreamingResponse(sse(events), container).then(() => 'ok', () => 'threw');
    void outcome;
    const stray = timers.revealTimers();
    await new Promise((resolve) => setTimeout(resolve, 200));
    const held = api.getMessages()[before] ? api.getMessages()[before].content : null;
    await new Promise((resolve) => setTimeout(resolve, 200));
    const still = api.getMessages()[before] ? api.getMessages()[before].content : null;
    assert(stray === 0 && timers.revealTimers() === 0 && held === still,
      `${name}: no reveal timer is left, and the message no longer changes`);
  }
}

console.log('\nan abnormal end keeps what arrived, in full, and says so');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  await api.handleStreamingResponse(sse([{ event: 'content', content: REPLY }]), container).catch(() => {});
  const content = api.getMessages()[started].content;
  assert(content.startsWith(REPLY) && /incomplete/.test(content), 'all the text, then the note that it may be incomplete');
}

console.log('\na reader failure mid-reveal keeps the text that arrived');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const encoder = new TextEncoder();
  const response = new Response(new ReadableStream({
    async start(controller) {
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ event: 'content', content: REPLY })}\n\n`));
      await new Promise((resolve) => setTimeout(resolve, 60));
      controller.error(new Error('connection reset'));
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
  let error = null;
  await api.handleStreamingResponse(response, container).catch((err) => { error = err; });
  assert(error !== null, 'the failure is raised');
  const content = api.getMessages()[started].content;
  assert(content.startsWith(REPLY) && /interrupted/.test(content), 'the whole of what arrived is kept, with the note that it was interrupted');
}

console.log('\na browser-execution reply keeps the earlier run and reveals the next one after it');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const first = await api.handleStreamingResponse(sse([
    { event: 'content', content: 'Let me check that for you.' },
    { event: 'tool_request', call_id: 'c1', tool: 'execute_code', args: {}, content: 'Let me check that for you.' },
  ]), container);
  const index = first.messageIndex;
  const second = 'The peak is at ten hertz, which matches the alpha band you would expect from an eyes-closed recording of this kind.';
  const stream = api.handleStreamingResponse(sse([
    { event: 'content', content: second },
    { event: 'done', content: second },
  ]), container, { messageIndex: index });
  const states = [];
  let over = false;
  stream.then(() => { over = true; }, () => { over = true; });
  while (!over) {
    states.push(api.getMessages()[index].content);
    await new Promise((resolve) => setTimeout(resolve, 8));
  }
  await stream;
  assert(states.every((c) => c.startsWith('Let me check that for you.')), 'every state after the first run keeps its text');
  assert(new Set(states.map((c) => c.length)).size >= 3, 'and the second run is revealed progressively after it');
  assertEqual(api.getMessages()[index].content, `Let me check that for you.\n\n${second}`, 'ending as the two runs composed');
}

console.log('\nsources appear with the sentence that cites them, not ahead of it');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const citations = [1, 2, 3].map((marker) => ({ marker, source: `https://example.org/${marker}`, title: `Source ${marker}`, cited_text: 'x' }));
  const sentences = [
    'The first claim is stated plainly here, with a marker after it.[1] ',
    'The second claim follows and it too is long enough to take a moment to reveal.[2] ',
    'The third claim comes last, and so does its source.[3]',
  ];
  const text = sentences.join('');
  const stream = api.handleStreamingResponse(sse([
    ...citations.map((c) => ({ event: 'citation', ...c })),
    { event: 'content', content: text },
    { event: 'done', content: text, citations },
  ]), container);
  const frames = [];
  let over = false;
  stream.then(() => { over = true; }, () => { over = true; });
  while (!over) {
    const content = (api.getMessages()[started] || {}).content || '';
    const rows = [...window.document.querySelectorAll('.osa-message.assistant .osa-message-sources li')].length;
    frames.push({ content, rows });
    await new Promise((resolve) => setTimeout(resolve, 8));
  }
  await stream;
  const ahead = frames.filter((f) => f.rows > [1, 2, 3].filter((n) => f.content.includes(`[${n}]`)).length);
  assertEqual(ahead.length, 0, 'at no frame was there a source row for a marker not yet shown');
  assertEqual([...window.document.querySelectorAll('.osa-message.assistant .osa-message-sources li')].length, 3, 'and once done, all three are listed');
}

console.log('\na reader typing in a message keeps their caret through the reveal');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const messagesEl = container.querySelector('.osa-chat-messages');
  const box = window.document.createElement('textarea');
  messagesEl.appendChild(box);
  box.focus();
  assert(window.document.activeElement === box, 'the reader is in a textarea inside the messages');
  const stream = api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'done', content: REPLY },
  ]), container);
  const survived = [];
  let over = false;
  stream.then(() => { over = true; }, () => { over = true; });
  while (!over) {
    survived.push(messagesEl.contains(box) && window.document.activeElement === box);
    await new Promise((resolve) => setTimeout(resolve, 8));
  }
  await stream;
  assert(survived.length > 5 && survived.every(Boolean), `the textarea was never rebuilt while the reply was revealed (${survived.length} samples)`);
  assert(api.getMessages().at(-1).content === REPLY, 'and the reply still completed');
}

console.log('\na reader who scrolled up is not pulled back down by the reveal');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const messagesEl = container.querySelector('.osa-chat-messages');
  let top = 0;
  Object.defineProperty(messagesEl, 'scrollHeight', { configurable: true, get: () => 5000 });
  Object.defineProperty(messagesEl, 'clientHeight', { configurable: true, get: () => 400 });
  Object.defineProperty(messagesEl, 'scrollTop', { configurable: true, get: () => top, set: (v) => { top = v; } });

  top = 300; // far from the bottom (4300 px away)
  const away = api.handleStreamingResponse(sse([{ event: 'content', content: REPLY }, { event: 'done', content: REPLY }]), container);
  const tops = [];
  let over = false;
  away.then(() => { over = true; }, () => { over = true; });
  while (!over) {
    tops.push(top);
    await new Promise((resolve) => setTimeout(resolve, 8));
  }
  await away;
  assert(tops.length > 5 && tops.every((t) => t === 300), `the scroll position held through the reveal (${[...new Set(tops)].join(', ')})`);
  assertEqual(top, 300, 'and when the reply finished');

  top = 4560; // 40 px from the bottom: following along
  await api.handleStreamingResponse(sse([{ event: 'content', content: REPLY }, { event: 'done', content: REPLY }]), container);
  assertEqual(top, 5000, 'a reader at the bottom is followed to it');
}

console.log('\nleaving the page, or hiding the tab, shows and saves the whole reply at once');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const text = 'A long reply that would take a few seconds to reveal, so the page is left in the middle of it. '.repeat(20);
  const stream = api.handleStreamingResponse(sse([{ event: 'content', content: text }, { event: 'done', content: text }]), container);
  await new Promise((resolve) => setTimeout(resolve, 150));
  const partial = api.getMessages()[started].content.length;
  assert(partial > 0 && partial < text.length, `mid-reveal (${partial} of ${text.length} characters)`);
  const begun = Date.now();
  window.dispatchEvent(new window.Event('pagehide'));
  await stream;
  assert(Date.now() - begun < 300, `the rest was shown at once (${Date.now() - begun} ms, not the seconds the pace would take)`);
  assertEqual(api.getMessages()[started].content, text, 'in full');
  const saved = JSON.parse(window.localStorage.getItem('osa-test-paced') || '[]');
  const savedText = (Array.isArray(saved) ? saved : saved.messages || []).map((m) => m.content).join('\n');
  assert(savedText.includes(text.slice(-40)), 'and it is in the saved history');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed} checks, passed: ${passed}, failed: ${failed}`);
process.exit(failed === 0 ? 0 : 1);
