/**
 * Paced reveal of streamed text (#531), run against the real widget source.
 *
 * The problem it answers: a reasoning model can think for tens of seconds and then emit
 * its whole answer in under a second, so a stream that is fine on the wire looks like
 * nothing and then everything. The widget draws the first text the moment it arrives and
 * spreads a burst over at most half a second (every character is drawn no later than the
 * lag bound after it arrived), and takes fenced code whole rather than typing it out.
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

/**
 * The widget in its own window, initialized. `chat`, when given, answers /chat (what a
 * test that presses Send needs); `saved` is what the page's storage already holds under
 * the widget's key, as after a reload.
 */
function loadWidget({ matchMedia, timers = null, chat = null, resume = null, saved = null, settings = null } = {}) {
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
  const fetch = async (url, init) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (resume && String(url).endsWith('/chat/resume')) return resume(init);
    if (chat && String(url).endsWith('/chat')) return chat(init);
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
  window.fetch = fetch;
  if (saved !== null) window.localStorage.setItem('osa-test-paced', saved);
  if (settings !== null) window.localStorage.setItem('osa-settings-test', JSON.stringify(settings));
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

console.log('\nthe delay the reveal may add is a promise, pinned here and not derived from the constants');
{
  assert(R.REVEAL_LAG_MS <= 500, `a character is drawn at most half a second after it arrived (${R.REVEAL_LAG_MS} ms)`);
  assert(R.REVEAL_TICK_MS <= 100, `redrawn at least ten times a second (${R.REVEAL_TICK_MS} ms)`);
  assert(R.REVEAL_DRAIN_MAX_MS <= 750, `and a finished reply waits at most three quarters of a second for it (${R.REVEAL_DRAIN_MAX_MS} ms)`);
  assert(R.REVEAL_MIN_CPS >= 1000, `while a small backlog is drawn at once (${R.REVEAL_MIN_CPS} characters a second or more)`);
}

console.log('\na burst starts at once and is spread over a window shorter than half a second');
{
  const { clock, state, controller } = harness(PROSE_1500);
  controller.kick();
  assertEqual(state.shows.length, 1, 'the first text is drawn the moment it arrives, not a tick later');
  assertEqual(state.shows[0].at, 0, 'at that same instant');
  assert(state.shows[0].visible.length > 0 && state.shows[0].visible.length < PROSE_1500.length / 2,
    `and it is a beginning (${state.shows[0].visible.length} of ${PROSE_1500.length} characters), not the whole burst`);
  clock.advance(60_000);
  const last = state.shows.at(-1);
  assertEqual(last.visible, PROSE_1500, 'everything ends up shown');
  assert(state.shows.every((s, i) => PROSE_1500.startsWith(s.visible) && (i === 0 || s.visible.length >= state.shows[i - 1].visible.length)),
    'every step is a growing prefix of the text');
  assert(state.shows.length >= 5, `in several steps (${state.shows.length}), not one flash`);
  assert(last.at <= R.REVEAL_LAG_MS + R.REVEAL_TICK_MS, `all of it by the deadline (${last.at} ms, bound ${R.REVEAL_LAG_MS + R.REVEAL_TICK_MS})`);
  assert(last.at >= R.REVEAL_TICK_MS * 3, `not in a single tick either (${last.at} ms)`);
  assertEqual(clock.pending(), 0, 'and no timer is left running');
}

console.log('\nproperty: no character is drawn later than the lag bound after it arrived');
{
  // A seeded generator, so the same schedules run every time: bursts of 100 to 9000
  // characters, trickles, and both interleaved, with gaps from nothing to a second.
  let seed = 20260929;
  const random = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
  const words = 'the schema tags a stimulus in the event branch and each recording keeps its own sampling rate '.split(' ');
  let schedules = 0;
  let late = 0;
  let worst = 0;
  let notPrefix = 0;
  let unfinished = 0;
  for (let n = 0; n < 300; n++) {
    const clock = fakeClock();
    const state = { text: '', shows: [] };
    const controller = R.createReveal({
      getText: () => state.text,
      show: (visible) => state.shows.push({ at: clock.now(), length: visible.length, visible }),
      now: clock.now, later: clock.later, unlater: clock.unlater,
    });
    const arrivals = [];
    const events = 1 + Math.floor(random() * 25);
    for (let e = 0; e < events; e++) {
      const size = random() < 0.4 ? 1 + Math.floor(random() * 30) : 100 + Math.floor(random() * 9000);
      let chunk = '';
      while (chunk.length < size) chunk += `${words[Math.floor(random() * words.length)]} `;
      state.text += chunk;
      arrivals.push([state.text.length, clock.now()]);
      controller.kick();
      clock.advance(random() < 0.5 ? Math.floor(random() * 40) : Math.floor(random() * 1000));
    }
    clock.advance(60_000);
    schedules++;
    if (state.shows.at(-1).visible !== state.text) unfinished++;
    if (!state.shows.every((sh) => state.text.startsWith(sh.visible))) notPrefix++;
    for (const [length, arrivedAt] of arrivals) {
      const drawn = state.shows.find((sh) => sh.length >= length);
      const delay = drawn ? drawn.at - arrivedAt : Infinity;
      worst = Math.max(worst, delay);
      if (delay > R.REVEAL_LAG_MS + R.REVEAL_TICK_MS) late++;
    }
  }
  assertEqual(unfinished, 0, `${schedules} schedules all end with the whole text shown`);
  assertEqual(notPrefix, 0, 'every step of every schedule is a prefix of the text');
  assertEqual(late, 0, `no character was drawn more than ${R.REVEAL_LAG_MS + R.REVEAL_TICK_MS} ms after it arrived (worst ${Math.round(worst)} ms)`);
}

console.log('\na later burst is spread too: after a pause, and when it lands late in an earlier reveal');
{
  const words = 'the schema tags a stimulus in the event branch and each recording keeps its own sampling rate '.split(' ');
  const make = (chars) => { let out = ''; while (out.length < chars) out += `${words[out.length % words.length]} `; return out; };
  const steps = (state, from) => state.shows.filter((sh) => sh.at >= from).length;

  // A second burst of 1500 characters after ten seconds of silence, as after a tool call.
  const paused = harness(make(1500));
  paused.controller.kick();
  paused.clock.advance(10_000);
  const before = paused.state.shows.length;
  const start = paused.clock.now();
  paused.state.text += make(1500);
  paused.controller.kick();
  paused.clock.advance(2000);
  const after = paused.state.shows.slice(before);
  assert(after.length >= 4, `after a pause, a burst is spread over several steps (${after.length}), not drawn in one`);
  assert(after[0].at === start && after[0].visible.length < paused.state.text.length - 1500 + 1000,
    `it starts at once, with a beginning (${after[0].visible.length - (paused.state.text.length - 1500)} of 1500 characters)`);
  assert(after.at(-1).at - start <= R.REVEAL_LAG_MS + R.REVEAL_TICK_MS, `and is all drawn by the deadline (${after.at(-1).at - start} ms)`);

  // A second burst that lands 400 ms into the first one's reveal: the first burst's
  // deadline is 100 ms away, and the second must not be drawn on that deadline.
  const overlap = harness(make(3000));
  overlap.controller.kick();
  overlap.clock.advance(400);
  const drawn = overlap.state.shows.length;
  const arrivedAt = overlap.clock.now();
  overlap.state.text += make(3000);
  overlap.controller.kick();
  overlap.clock.advance(2000);
  const afterwards = overlap.state.shows.slice(drawn - 1).map((sh) => sh.visible.length);
  const second = afterwards.slice(1).filter((n) => n > 3000);
  const biggest = Math.max(...afterwards.slice(1).map((n, i) => n - afterwards[i]));
  assert(second.length >= 4, `a burst landing late in an earlier reveal is spread over several steps (${second.length})`);
  assert(biggest <= 1500, `no single step draws most of it (largest step ${biggest} of 3000 characters)`);
  assert(overlap.state.shows.at(-1).at - arrivedAt <= R.REVEAL_LAG_MS + R.REVEAL_TICK_MS, 'and it too is drawn by its own deadline');

  // Repeated 1000 character bursts every 430 ms, the spacing that flashed the last of them.
  const spaced = harness(make(1000));
  spaced.controller.kick();
  let worstStep = 0;
  let previous = 0;
  for (let i = 0; i < 6; i++) {
    spaced.clock.advance(430);
    spaced.state.text += make(1000);
    spaced.controller.kick();
  }
  spaced.clock.advance(2000);
  for (const sh of spaced.state.shows) { worstStep = Math.max(worstStep, sh.visible.length - previous); previous = sh.visible.length; }
  assert(worstStep <= 500, `bursts every 430 ms are never drawn whole (largest step ${worstStep} of 1000 characters)`);
  void steps;
}

console.log('\ntext that arrives on an idle reveal is drawn at once, a line or so of it, not a word');
{
  const big = 'The schema tags a stimulus in the event branch and each recording keeps its own sampling rate, with the sampling rate stored beside the data. '.repeat(20);
  const { clock, state, controller } = harness(big);
  controller.kick();
  const first = state.shows[0].visible.length;
  assert(first >= 60 && first < big.length / 2, `the first draw is a line or so (${first} of ${big.length} characters), neither a word nor the burst`);
  clock.advance(60_000);
  // Idle again, then more: the same.
  const before = state.shows.length;
  state.text += big;
  controller.kick();
  assert(state.shows.length === before + 1 && state.shows.at(-1).at === clock.now(), 'and again for text that arrives after a pause');
  assert(state.shows.at(-1).visible.length - big.length >= 60, 'a line or so of it');
}

console.log('\nthe bound survives timers that fire early or late');
{
  const words = 'the schema tags a stimulus in the event branch and each recording keeps its own sampling rate '.split(' ');
  let seed = 7;
  const random = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
  for (const [name, skew] of [['1 ms early', -1], ['20 ms late', 20], ['60 ms late', 60]]) {
    let late = 0;
    let worst = 0;
    for (let n = 0; n < 120; n++) {
      const clock = fakeClock();
      const state = { text: '', shows: [] };
      const controller = R.createReveal({
        getText: () => state.text,
        show: (visible) => state.shows.push({ at: clock.now(), length: visible.length }),
        now: clock.now,
        later: (fn, ms) => clock.later(fn, Math.max(0, ms + skew)),
        unlater: clock.unlater,
      });
      const arrivals = [];
      for (let e = 0; e < 1 + Math.floor(random() * 15); e++) {
        const size = 100 + Math.floor(random() * (random() < 0.3 ? 100000 : 6000));
        let chunk = '';
        while (chunk.length < size) chunk += `${words[Math.floor(random() * words.length)]} `;
        state.text += chunk;
        arrivals.push([state.text.length, clock.now()]);
        controller.kick();
        clock.advance(Math.floor(random() * 700));
      }
      clock.advance(60_000);
      for (const [length, arrivedAt] of arrivals) {
        const drawn = state.shows.find((sh) => sh.length >= length);
        const delay = drawn ? drawn.at - arrivedAt : Infinity;
        worst = Math.max(worst, delay);
        if (delay > R.REVEAL_LAG_MS + R.REVEAL_TICK_MS + Math.max(skew, 0)) late++;
      }
    }
    assertEqual(late, 0, `timers ${name}: no character drawn later than the bound plus the lateness (worst ${Math.round(worst)} ms)`);
  }
}

console.log('\na stream slower than the pace is shown as it arrives, at most one tick late');
{
  const { clock, state, controller } = harness('');
  const words = 'a steady stream of small words arriving well below the pace the reveal can draw'.split(' ');
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
  const before = 'Here is how to read an NWB file with PyNWB. The handle stays open while you read, so keep the work inside the with block and close nothing yourself. '.repeat(5) + '\n\n';
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

console.log('\ndrain: waits for the reveal to catch up, but only so long');
{
  const { clock, state, controller } = harness(PROSE_1500);
  controller.kick();
  let done = false;
  controller.drain().then(() => { done = true; });
  clock.advance(200);
  await Promise.resolve();
  assert(!done, 'a reply still being revealed is waited for');
  clock.advance(R.REVEAL_DRAIN_MAX_MS);
  await Promise.resolve();
  assert(done, 'until it has all been shown');
  assertEqual(state.shows.at(-1).visible, PROSE_1500, 'which it has');
  assert(state.shows.at(-1).at <= R.REVEAL_LAG_MS + R.REVEAL_TICK_MS, `by the deadline, not the drain bound (${state.shows.at(-1).at} ms)`);

  // If ticks stop coming (a timer throttled or starved), the bound still holds.
  const clock2 = fakeClock();
  const state2 = { text: 'word '.repeat(40_000), shows: [] };
  const starved = R.createReveal({
    getText: () => state2.text,
    show: (visible) => state2.shows.push({ at: clock2.now(), visible }),
    now: clock2.now,
    later: (fn, ms) => (ms === R.REVEAL_TICK_MS ? 0 : clock2.later(fn, ms)),
    unlater: clock2.unlater,
  });
  starved.kick();
  let starvedDone = false;
  starved.drain().then(() => { starvedDone = true; });
  clock2.advance(R.REVEAL_DRAIN_MAX_MS - 50);
  await Promise.resolve();
  assert(!starvedDone, 'with no ticks, a reply is still waited for just under the bound');
  clock2.advance(100);
  await Promise.resolve();
  assert(starvedDone, `and is shown in full at the bound (${R.REVEAL_DRAIN_MAX_MS} ms), not left waiting`);
  assertEqual(state2.shows.at(-1).visible.length, state2.text.length, 'all of it');

  const idle = harness('Nothing pending.');
  idle.controller.flush();
  let idleDone = false;
  idle.controller.drain().then(() => { idleDone = true; });
  await Promise.resolve();
  assert(idleDone, 'a reveal that has caught up drains at once');
}

console.log('\ndrain on an unpaced reveal shows everything now, with no tick to wait for');
{
  const { clock, state, controller } = harness('first line of the reply. '.repeat(10), { paced: false });
  controller.kick();
  state.text += 'more that arrived a moment later. '.repeat(10);
  controller.kick();
  assert(state.shows.at(-1).visible.length < state.text.length, 'a tick is pending, with more text than is drawn');
  let released = false;
  controller.drain().then(() => { released = true; });
  await Promise.resolve();
  assert(released, 'drain resolves at once, without waiting for a tick or the guard');
  assertEqual(state.shows.at(-1).visible, state.text, 'with all of it drawn');
  assertEqual(clock.pending(), 0, 'and no timer left');
}

console.log('\nflush shows everything now; stop abandons the pending redraw; unpaced shows on arrival');
{
  const a = harness(PROSE_1500);
  a.controller.kick();
  a.clock.advance(100);
  a.controller.flush();
  assertEqual(a.state.shows.at(-1).visible, PROSE_1500, 'flush: all of it');
  assertEqual(a.clock.pending(), 0, 'and no timer left');

  const b = harness(PROSE_1500);
  b.controller.kick();
  const drawn = b.state.shows.length;
  b.controller.stop();
  b.clock.advance(60_000);
  assertEqual(b.state.shows.length, drawn, 'stop: nothing more is drawn after it');

  const c = harness('', { paced: false });
  c.state.text = PROSE_1500;
  c.controller.kick();
  assertEqual(c.state.shows.at(-1).visible, PROSE_1500, 'unpaced: the whole burst is shown the moment it arrives');
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
  assertEqual(burst.state.shows.length, 2, 'a 150 chunk burst is two redraws: the first chunk at once, the other 149 gathered into one');
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

console.log('\npacing is asked at each tick: a page that becomes hidden mid-reply gets the rest at once');
{
  let visible = true;
  const { clock, state, controller } = harness(PROSE_1500, { paced: () => visible });
  controller.kick();
  clock.advance(R.REVEAL_TICK_MS * 3);
  const before = state.shows.at(-1).visible.length;
  assert(before > 0 && before < PROSE_1500.length, `paced while it is on screen (${before} of ${PROSE_1500.length})`);
  visible = false;
  clock.advance(R.REVEAL_TICK_MS);
  assertEqual(state.shows.at(-1).visible, PROSE_1500, 'and shown in full at the next tick once it is hidden');
  assertEqual(clock.pending(), 0, 'with nothing left running');
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
  assert(distinct.length >= 3, `the reader saw the reply grow through ${distinct.length} different lengths, not one jump`);
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
  const text = `Open the file like this, then read from the handle while it is open, and keep every read inside the with block so that the handle is closed for you when the block ends. ${'The handle is the file. '.repeat(12)}\n\n${code}The series are under acquisition, and each has data and timestamps to read.`;
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
  // One redraw per 80 ms tick, plus the first text (drawn at once) and the canonical text.
  assert(counter.redraws <= 3 + seconds * 13, `${counter.redraws} redraws over ${seconds.toFixed(2)} s`);
}

console.log('\na page that is already hidden when the reply starts is not paced');
{
  const { window, api } = loadWidget();
  Object.defineProperty(window.document, 'hidden', { configurable: true, get: () => true });
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const text = 'A reply for a tab nobody is looking at, long enough that pacing it would take seconds. '.repeat(20);
  const stream = api.handleStreamingResponse(sse([{ event: 'content', content: text }, { event: 'done', content: text }], { gapMs: 250 }), container);
  const seen = await watch(api, started, stream);
  await stream;
  const first = seen.find((s) => s.content);
  assertEqual(first && first.content, text, 'the whole reply is there at the first tick, before done arrives');
}

console.log('\na change of the system time mid-reply does not stall the reveal');
{
  // The reveal reads a clock that cannot go backward. With the wall clock (Date.now) a
  // burst's age would go negative when the time steps back, and the reveal would run
  // on a stale deadline. Here the wall clock steps back ten seconds 30 ms into it.
  const realNow = Date.now;
  let skew = 0;
  Date.now = () => realNow() - skew;
  try {
    const { window, api } = loadWidget();
    const container = window.document.querySelector('.osa-chat-widget');
    const started = api.getMessages().length;
    const text = 'The schema tags a stimulus in the event branch and each recording keeps its own sampling rate. '.repeat(40);
    const begun = performance.now();
    const stream = api.handleStreamingResponse(sse([{ event: 'content', content: text }, { event: 'done', content: text }], { gapMs: 1500 }), container);
    setTimeout(() => { skew = 10_000; }, 30);
    let fullAt = null;
    let over = false;
    stream.then(() => { over = true; }, () => { over = true; });
    while (!over) {
      const shown = (api.getMessages()[started] || {}).content || '';
      if (fullAt === null && shown.length === text.length) fullAt = performance.now() - begun;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    await stream;
    assert(fullAt !== null && fullAt < 900, `the whole reply was drawn within about half a second of arriving, before done (${Math.round(fullAt)} ms)`);
  } finally {
    Date.now = realNow;
  }
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

console.log('\na redraw that throws before done leaves the finished reply whole, and not called interrupted');
{
  // The reply completed: its canonical text and request id are what the server sent,
  // whatever became of the page's own redraw on the way.
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const messagesEl = container.querySelector('.osa-chat-messages');
  const started = api.getMessages().length;
  const CANONICAL = `${REPLY} [1]`;
  const stream = api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'done', content: CANONICAL, request_id: 'req-42', citations: [{ marker: 1, source: 'https://a.example', title: 'A', cited_text: '' }] },
  ], { gapMs: 50 }), container);
  await new Promise((resolve) => setTimeout(resolve, 20));
  messagesEl.className = 'not-the-messages'; // the redraw's own lookup now finds nothing
  const outcome = await Promise.race([
    stream.then(() => 'resolved', (err) => `rejected: ${err.message.slice(0, 40)}`),
    new Promise((resolve) => setTimeout(() => resolve('still pending'), 6000)),
  ]);
  assert(/^rejected/.test(outcome), `the page's failure is still raised to the caller (${outcome})`);
  const message = api.getMessages()[started];
  assertEqual(message && message.content, CANONICAL, 'the message holds the canonical text, in full');
  assertEqual(message && message.requestId, 'req-42', 'and the request id, which feedback is posted against');
  assertEqual(message && message.citations.length, 1, 'and the citations');
  assert(!/interrupted|incomplete/i.test(message ? message.content : ''), 'no note says the stream was cut');
  assert((window.localStorage.getItem('osa-test-paced') || '').includes(CANONICAL.slice(-40)), 'and the whole reply is saved');
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
  const second = 'The peak is at ten hertz, which matches the alpha band you would expect from an eyes-closed recording of this kind. '.repeat(6).trim();
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

console.log('\nwhat a reply used and cost is shown under it, between its sources and the feedback buttons');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const citations = [{ marker: 1, source: 'https://example.org/1', title: 'Source 1', cited_text: 'x' }];
  const usage = { input_tokens: 1240, output_tokens: 310, cache_read_tokens: 980, cache_creation_tokens: 0, estimated_cost: 0.0021, partial: false };
  const text = 'The first claim is stated plainly here, with a marker after it.[1]';
  const lastReply = () => [...window.document.querySelectorAll('.osa-message.assistant')].at(-1);
  const started = api.getMessages().length;
  const stream = api.handleStreamingResponse(sse([
    { event: 'content', content: text },
    { event: 'done', content: text, citations, usage },
  ]), container);
  let shownWhileOpen = false;
  let over = false;
  stream.then(() => { over = true; }, () => { over = true; });
  while (!over) {
    shownWhileOpen = shownWhileOpen || (lastReply() !== undefined && lastReply().querySelector('.osa-message-usage') !== null);
    await new Promise((resolve) => setTimeout(resolve, 8));
  }
  await stream;
  const line = lastReply().querySelector('.osa-message-usage');
  assert(line !== null, 'the reply has a usage line once it is done');
  assertEqual(line.textContent, '1,240 in (980 cached), 310 out, about $0.0021', 'saying tokens, cache and cost');
  assert(!shownWhileOpen, 'and no line was on the page while the stream was open');
  assert(/price table/.test(line.getAttribute('title')), 'its tooltip says the cost is an estimate from the price table');
  const order = [...lastReply().children].map((el) => el.className.split(' ')[0]);
  assert(
    order.indexOf('osa-message-sources') < order.indexOf('osa-message-usage')
      && order.indexOf('osa-message-usage') < order.indexOf('osa-message-feedback'),
    `it sits under the sources and above the feedback buttons (${order.join(', ')})`
  );
  assertEqual(api.getMessages()[started].usage, usage, 'and the message keeps it, so a reload shows it again');
}

console.log('\na reply with no usage has no usage line');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  for (const usage of [undefined, null]) {
    await api.handleStreamingResponse(sse([
      { event: 'content', content: 'Fine.' },
      { event: 'done', content: 'Fine.', ...(usage === undefined ? {} : { usage }) },
    ]), container);
  }
  assertEqual(window.document.querySelectorAll('.osa-message-usage').length, 0, 'whether the server sent null or left it out');
}

console.log('\na browser-execution reply shows what all of its runs used, and says so when one reported none');
{
  const run = (input, output, cost) => ({
    input_tokens: input, output_tokens: output, cache_read_tokens: 0, cache_creation_tokens: 0, estimated_cost: cost, partial: false,
  });
  const twoRuns = async (firstUsage, lastUsage) => {
    const { window, api } = loadWidget();
    const container = window.document.querySelector('.osa-chat-widget');
    const first = await api.handleStreamingResponse(sse([
      { event: 'content', content: 'Let me check that for you.' },
      { event: 'tool_request', call_id: 'c1', tool: 'execute_code', args: {}, content: 'Let me check that for you.', usage: firstUsage },
    ]), container);
    const whileUnfinished = window.document.querySelectorAll('.osa-message-usage').length;
    await api.handleStreamingResponse(sse([
      { event: 'content', content: 'The peak is at ten hertz.' },
      { event: 'done', content: 'The peak is at ten hertz.', usage: lastUsage },
    ]), container, { messageIndex: first.messageIndex });
    const lines = [...window.document.querySelectorAll('.osa-message-usage')].map((el) => el.textContent);
    return { whileUnfinished, lines, saved: window.localStorage.getItem('osa-test-paced') };
  };

  const both = await twoRuns(run(1000, 100, 0.0010), run(1500, 200, 0.0015));
  assertEqual(both.whileUnfinished, 0, 'nothing is shown while the reply is unfinished');
  assertEqual(both.lines, ['2,500 in, 300 out, about $0.0025'], 'one line, the sum of the two runs');

  assertEqual((await twoRuns(run(1000, 100, 0.0010), null)).lines, ['at least 1,000 in, 100 out, about $0.0010'],
    'a last run that reported none leaves the first run\'s figures, marked "at least"');
  assertEqual((await twoRuns(null, run(1500, 200, 0.0015))).lines, ['at least 1,500 in, 200 out, about $0.0015'],
    'and so does a first run that reported none');
  assertEqual((await twoRuns(null, null)).lines, [], 'while two runs that both reported none have nothing to show');
  assertEqual((await twoRuns({ ...run(1000, 100, 0.0010), partial: true }, run(1500, 200, 0.0015))).lines,
    ['at least 2,500 in, 300 out, about $0.0025'], 'and a run the server marked partial makes the sum partial');
}

console.log('\nthe usage in a saved conversation is read back, and only as the widget writes it');
{
  const warns = [];
  const warn = console.warn;
  console.warn = (...args) => warns.push(args.join(' '));
  try {
    const usage = { input_tokens: 5, output_tokens: 1, cache_read_tokens: 0, cache_creation_tokens: 0, estimated_cost: 0.0001, partial: false };
    const history = (message) => JSON.stringify({ version: 99, messages: [{ role: 'assistant', content: 'Hi.', ...message }], sessionId: null });
    const lineOf = (loaded) => [...loaded.window.document.querySelectorAll('.osa-message-usage')].map((el) => el.textContent);

    const good = loadWidget({ saved: history({ usage }) });
    assertEqual(lineOf(good), ['5 in, 1 out, about $0.0001'], 'a saved usage is shown again after a reload');

    const forged = loadWidget({ saved: history({ usage: { ...usage, evil: '<img src=x onerror=alert(1)>', cache_read_tokens: -4 } }) });
    assertEqual(forged.api.getMessages().at(-1).usage, usage, 'unknown fields are dropped and a negative count is zero');

    const junk = loadWidget({ saved: history({ usage: '5 tokens' }) });
    assertEqual(lineOf(junk), [], 'a usage that is not an object shows nothing');
    assert(!('usage' in junk.api.getMessages().at(-1)), 'and is not kept');
    assertEqual(warns.filter((w) => /cannot read/.test(w)).length, 1, 'a usage this version cannot read is reported on the console, once');
    loadWidget({ saved: history({ usage: { input_tokens: 'many' } }) });
    assertEqual(warns.filter((w) => /cannot read/.test(w)).length, 2, 'once for each page load, not once for every redraw of it');
  } finally {
    console.warn = warn;
  }
}

console.log('\na reply saved with its usage shows it again after a reload, and a failed second run saves nothing kept aside');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const usage = { input_tokens: 1240, output_tokens: 310, cache_read_tokens: 980, cache_creation_tokens: 0, estimated_cost: 0.0021, partial: false };
  await api.handleStreamingResponse(sse([
    { event: 'content', content: 'Fine.' },
    { event: 'done', content: 'Fine.', usage },
  ]), container);
  const again = loadWidget({ saved: window.localStorage.getItem('osa-test-paced') });
  assertEqual([...again.window.document.querySelectorAll('.osa-message-usage')].map((el) => el.textContent),
    ['1,240 in (980 cached), 310 out, about $0.0021'], 'what the widget itself saved is shown again after a reload');
}
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const usage = { input_tokens: 1000, output_tokens: 100, cache_read_tokens: 0, cache_creation_tokens: 0, estimated_cost: 0.001, partial: false };
  const first = await api.handleStreamingResponse(sse([
    { event: 'content', content: 'Let me check.' },
    { event: 'tool_request', call_id: 'c1', tool: 'execute_code', args: {}, content: 'Let me check.', usage },
  ]), container);
  let failed = null;
  try {
    await api.handleStreamingResponse(sse([{ event: 'error', message: 'The model is unavailable.' }]), container, { messageIndex: first.messageIndex });
  } catch (error) { failed = error; }
  assert(failed !== null, 'the second run fails');
  assertEqual(window.document.querySelectorAll('.osa-message-usage').length, 0, 'a reply that did not finish shows no usage');
  const saved = window.localStorage.getItem('osa-test-paced') || '';
  assert(saved.includes('The model is unavailable.'), 'the failed reply was saved');
  assert(!/_usageSoFar|_usageRuns|"input_tokens"/.test(saved), 'with no usage in it, and nothing kept aside between the runs');
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
  window.dispatchEvent(new window.Event('pagehide'));
  assertEqual(api.getMessages()[started].content, text, 'the whole reply is on the page the moment the page is hidden, before the next tick');
  await new Promise((resolve) => setTimeout(resolve, 0));
  const savedAt = window.localStorage.getItem('osa-test-paced') || '';
  assert(savedAt.includes(text.slice(-40)), 'and it is already in the saved history, not saved later');
  await stream;
}

console.log('\nleaving the page shows a reply that is still streaming, but does not save it: only done does');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const text = 'A long reply that is still arriving when the page is left in the middle of it. '.repeat(20);
  const encoder = new TextEncoder();
  let control;
  const response = new Response(new ReadableStream({
    start(controller) {
      control = controller;
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ event: 'content', content: text })}\n\n`));
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
  const stream = api.handleStreamingResponse(response, container);
  await new Promise((resolve) => setTimeout(resolve, 150));
  assert(api.getMessages()[started].content.length < text.length, 'the reveal is still in progress');
  window.dispatchEvent(new window.Event('pagehide'));
  assertEqual(api.getMessages()[started].content, text, 'the whole of what has arrived is on the page');
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert(!(window.localStorage.getItem('osa-test-paced') || '').includes(text.slice(-40)), 'and none of it is in the saved history yet: no done has arrived');
  control.enqueue(encoder.encode(`data: ${JSON.stringify({ event: 'done', content: text })}\n\n`));
  control.close();
  await stream;
  assert((window.localStorage.getItem('osa-test-paced') || '').includes(text.slice(-40)), 'the done event is what saves it');
}

// -------------------------------------- what the end of a reply tells the reader
//
// A reply can end with a notice (a warning event: the model stopped at its length
// limit, the conversation is long) or a failure (an error event, a stream that broke).
// What the reader is left with: every notice read, a cut-off reply marked where it
// stands, a failed question handed back with the reason still on screen.

const TURN_OUTCOME = readFileSync(new URL('../src/api/turn_outcome.py', import.meta.url), 'utf8');

/** A string constant of src/api/turn_outcome.py, so the widget is held to the server's wording. */
function serverMessage(name) {
  const block = TURN_OUTCOME.match(new RegExp(`^${name} = \\(([^)]*)\\)`, 'm'));
  if (!block) return null;
  return [...block[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]).join('');
}

const CUT_OFF_MESSAGE = serverMessage('CUT_OFF_MESSAGE');
const NO_ANSWER_MESSAGE = serverMessage('NO_ANSWER_MESSAGE');
const CONTEXT_FULL_CUT_OFF_MESSAGE = serverMessage('CONTEXT_FULL_CUT_OFF_MESSAGE');
const CUT_OFF_LONG_MESSAGE = serverMessage('CUT_OFF_LONG_MESSAGE');
const LONG_MESSAGE = serverMessage('LONG_CONVERSATION_MESSAGE');
assert([CUT_OFF_MESSAGE, NO_ANSWER_MESSAGE, CONTEXT_FULL_CUT_OFF_MESSAGE, CUT_OFF_LONG_MESSAGE, LONG_MESSAGE].every(Boolean),
  'the server\'s cut-off, no-answer, context-full, combined and long-conversation messages are found in src/api/turn_outcome.py');

/**
 * Timers of four seconds or more are held until a test runs them (a banner's and a
 * notice's are five and ten seconds, and a test does not wait them out); shorter ones,
 * the reveal's and the launcher tooltip's, run for real. Held timers are numbered below
 * zero, so clearTimeout knows them. Nothing is held until `arm()`, so the widget's own
 * start-up timers run as usual.
 */
function heldTimers() {
  const held = new Map();
  let next = 1;
  let armed = false;
  return {
    arm() { armed = true; },
    setTimeout: (fn, ms, ...rest) => {
      if (!armed || ms < 4000) return setTimeout(fn, ms, ...rest);
      const id = -(next++);
      held.set(id, { fn, ms });
      return id;
    },
    clearTimeout: (id) => {
      if (typeof id === 'number' && id < 0) held.delete(id);
      else clearTimeout(id);
    },
    delays: () => [...held.values()].map((timer) => timer.ms),
    /** Run the oldest held timer, as if its delay had passed. */
    fireOldest() {
      const [id, timer] = [...held.entries()][0];
      held.delete(id);
      timer.fn();
    },
    fireAll() {
      while (held.size) this.fireOldest();
    },
  };
}

const lastAssistant = (container) => [...container.querySelectorAll('.osa-message.assistant')].at(-1) || null;
const lastReplyText = (container) => {
  const reply = lastAssistant(container);
  return reply ? reply.querySelector('.osa-message-content').textContent : '';
};
const countOf = (text, part) => text.split(part).length - 1;

/** Press Send with `question` typed, as a reader does. */
function send(window, container, question) {
  container.querySelector('.osa-chat-input input').value = question;
  container.querySelector('.osa-send-btn').dispatchEvent(new window.Event('click', { bubbles: true }));
}
const settled = (container) => !container.querySelector('.osa-send-btn').disabled;
async function waitFor(predicate, label, timeoutMs = 5000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) throw new Error(`timed out after ${timeoutMs} ms: ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 3));
  }
}
const json = (body, init = {}) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' }, ...init });

console.log('\nwarnings that arrive together are all shown, each for its own full period');
{
  const timers = heldTimers();
  const { window, api } = loadWidget({ timers });
  timers.arm();
  const container = window.document.querySelector('.osa-chat-widget');
  await api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'warning', message: CUT_OFF_MESSAGE },
    { event: 'warning', message: LONG_MESSAGE },
    { event: 'done', content: REPLY },
  ]), container);
  const banner = container.querySelector('.osa-warning');
  assertEqual(banner.style.display, 'block', 'the warning banner is up');
  assert(banner.textContent.includes(CUT_OFF_MESSAGE), 'the cut-off notice is on it');
  assert(banner.textContent.includes(LONG_MESSAGE), 'and the long-conversation notice beside it, not in its place');
  assert(timers.delays().length === 2 && timers.delays().every((ms) => ms >= 10_000),
    'each has a timer of its own, no shorter than the ten seconds one notice had');
  timers.fireOldest();
  assert(!banner.textContent.includes(CUT_OFF_MESSAGE) && banner.textContent.includes(LONG_MESSAGE) && banner.style.display === 'block',
    'when the first runs out only it goes; the second is still there to read');
  timers.fireOldest();
  assertEqual(banner.style.display, 'none', 'the banner goes when the last one does');
}

console.log('\nthe same warning twice is one line, read for a full period from the last time');
{
  const timers = heldTimers();
  const { window, api } = loadWidget({ timers });
  timers.arm();
  const container = window.document.querySelector('.osa-chat-widget');
  await api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'warning', message: LONG_MESSAGE },
    { event: 'warning', message: LONG_MESSAGE },
    { event: 'done', content: REPLY },
  ]), container);
  const banner = container.querySelector('.osa-warning');
  assertEqual(countOf(banner.textContent, LONG_MESSAGE), 1, 'it is shown once');
  assertEqual(timers.delays().length, 1, 'with one timer, the earlier one replaced');
  timers.fireAll();
  assertEqual(banner.style.display, 'none', 'and it goes when that runs out');
}

const INCOMPLETE = /Response may be incomplete/;
const CUT_OFF_CASES = [
  ['a cut_off code', { code: 'cut_off', message: 'The reply stopped short.' }, true],
  ['the server\'s cut-off notice, with its code', { code: 'cut_off', message: CUT_OFF_MESSAGE }, true],
  ['the combined notice the server sends when the conversation is long too', { code: 'cut_off', codes: ['cut_off', 'long_conversation'], message: CUT_OFF_LONG_MESSAGE }, true],
  ['the notice for a conversation that filled the context window', { code: 'cut_off', message: CONTEXT_FULL_CUT_OFF_MESSAGE }, true],
  ['the server\'s cut-off wording, from a server that sends no code', { message: CUT_OFF_MESSAGE }, true],
  ['the combined wording, from a server that sends no code', { message: CUT_OFF_LONG_MESSAGE }, true],
  ['the context-full wording, from a server that sends no code', { message: CONTEXT_FULL_CUT_OFF_MESSAGE }, true],
  ['the long-conversation notice with its code', { code: 'long_conversation', message: LONG_MESSAGE }, false],
  ['the long-conversation notice from a server that sends no code', { message: LONG_MESSAGE }, false],
];

console.log('\na reply a warning says was cut off is marked where it stands, and only that one');
for (const [label, warning, marked] of CUT_OFF_CASES) {
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  await api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'warning', ...warning },
    { event: 'done', content: REPLY },
  ]), container);
  const message = api.getMessages()[started];
  assertEqual(message.cutOff === true, marked, `${label}: ${marked ? 'marked' : 'not marked'}`);
  assertEqual(INCOMPLETE.test(lastReplyText(container)), marked, `${label}: the page ${marked ? 'says so under the text' : 'says nothing under the text'}`);
  assertEqual(message.content, REPLY, `${label}: the reply's own text is untouched`);
  if (marked) {
    const note = lastAssistant(container).querySelector('.osa-message-content em');
    assert(note && INCOMPLETE.test(note.textContent) && !lastReplyText(container).includes('_['),
      `${label}: the note is drawn in italics, with no markup showing as typed`);
  }
}
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  await api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'warning', code: 'cut_off', message: CUT_OFF_MESSAGE },
    { event: 'done', content: REPLY },
  ]), container);
  await api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'done', content: REPLY },
  ]), container);
  assertEqual([api.getMessages()[started].cutOff === true, api.getMessages()[started + 1].cutOff === true], [true, false],
    'a later reply that ended on its own is not marked because an earlier one was cut off');
}

console.log('\na cut-off mark is kept in the saved conversation and shown again after a reload');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  await api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'warning', code: 'cut_off', message: CUT_OFF_MESSAGE },
    { event: 'done', content: REPLY },
  ]), container);
  const saved = window.localStorage.getItem('osa-test-paced');
  assert(saved && saved.includes('"cutOff":true'), 'the mark is in what is saved');
  const again = loadWidget({ saved });
  const reloaded = again.window.document.querySelector('.osa-chat-widget');
  assert(lastReplyText(reloaded).startsWith(REPLY.slice(0, 40)) && INCOMPLETE.test(lastReplyText(reloaded)),
    'the reloaded reply shows its text and the note');
  assert(again.api.getMessages().some((m) => m.cutOff === true), 'and the message still carries the mark');
  const forged = JSON.stringify({ version: 99, messages: [{ role: 'assistant', content: 'Hi.', cutOff: 'yes' }], sessionId: null });
  assertEqual(loadWidget({ saved: forged }).api.getMessages().map((m) => m.cutOff === true), [false],
    'a stored value that is not true marks nothing');
}

console.log('\nthe note waits for the end of the reveal: a reply still being drawn is not called incomplete yet');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const stream = api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'warning', code: 'cut_off', message: CUT_OFF_MESSAGE },
    { event: 'done', content: REPLY },
  ], { gapMs: 5 }), container);
  const texts = [];
  let over = false;
  stream.then(() => { over = true; }, () => { over = true; });
  while (!over) {
    texts.push(lastReplyText(container));
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  await stream;
  const early = texts.filter((t) => t.length > 0 && t.length < REPLY.length && INCOMPLETE.test(t));
  assertEqual(early.length, 0, 'no partial text was shown with the note');
  assert(INCOMPLETE.test(lastReplyText(container)) && api.getMessages()[started].cutOff === true, 'it is there once the reply is whole');
}

console.log('\na cut-off reply with no text and no run record is kept, and says why');
{
  // A reply whose only run was a read of an earlier run's output (get_full_output) has
  // no record in `executions`: answerToolRequest writes one only for code it ran.
  for (const [label, shown] of [['output limit', CUT_OFF_MESSAGE], ['context window', CONTEXT_FULL_CUT_OFF_MESSAGE]]) {
    const { window, api } = loadWidget();
    const container = window.document.querySelector('.osa-chat-widget');
    const first = await api.handleStreamingResponse(sse([
      { event: 'tool_request', call_id: 'c1', tool: 'get_full_output', args: { call_id: 'earlier' }, content: '' },
    ]), container);
    await api.handleStreamingResponse(sse([
      { event: 'warning', code: 'cut_off', message: shown },
      { event: 'done', content: '' },
    ]), container, { messageIndex: first.messageIndex });
    const message = api.getMessages()[first.messageIndex];
    assert(message && message.cutOff === true, `${label}: the reply is still there, marked`);
    assertEqual(lastReplyText(container), `[${shown}]`, `${label}: what the reader sees is the server's explanation, with what to do next, not a missing bubble`);
    assert(!INCOMPLETE.test(lastReplyText(container)), `${label}: and, having no text at all, it does not call nothing incomplete`);
    const saved = window.localStorage.getItem('osa-test-paced');
    const again = loadWidget({ saved });
    assertEqual(lastReplyText(again.window.document.querySelector('.osa-chat-widget')), `[${shown}]`, `${label}: a reload shows it again`);
  }
}

console.log('\nan error event names itself as the server\'s: a word in it does not turn it into a stream timeout');
{
  const message = 'The model request hit its timeout after 30 s';
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  let error = null;
  await api.handleStreamingResponse(sse([
    { event: 'content', content: REPLY },
    { event: 'error', message },
  ]), container).catch((err) => { error = err; });
  assert(error && error.message.includes(message), 'the error raised is the server\'s');
  const content = api.getMessages()[started].content;
  assert(content.includes(message), 'the reply says what the server said');
  assert(!/Stream timeout/.test(content), 'and does not claim the stream timed out');
}

console.log('\na failure of the stream itself that says timeout still reads as a stream timeout');
{
  const { window, api } = loadWidget();
  const container = window.document.querySelector('.osa-chat-widget');
  const started = api.getMessages().length;
  const encoder = new TextEncoder();
  const response = new Response(new ReadableStream({
    async start(controller) {
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ event: 'content', content: REPLY })}\n\n`));
      await new Promise((resolve) => setTimeout(resolve, 60));
      controller.error(new Error('network timeout'));
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
  await api.handleStreamingResponse(response, container).catch(() => {});
  const content = api.getMessages()[started].content;
  assert(content.startsWith(REPLY) && content.includes('_[Stream timeout]_'), 'the text that arrived, then the note that the stream timed out');
}

console.log('\nthe banner for a server error is the server\'s own words, whatever words they contain');
for (const message of ['The tool arguments were not valid JSON', 'Stream closed by the upstream provider', 'Gateway timeout from the model host']) {
  const { window } = loadWidget({ chat: () => sse([{ event: 'error', message }]) });
  const container = window.document.querySelector('.osa-chat-widget');
  send(window, container, 'A question');
  await waitFor(() => settled(container), 'the send settles');
  assertEqual(container.querySelector('.osa-error').textContent, message, `"${message}" is shown as sent`);
}

console.log('\nan error with no reply: the banner stays until dismissed, the question goes back in the box, the reference can be copied');
{
  const timers = heldTimers();
  const QUESTION = 'Which event tag marks a button press?';
  let calls = 0;
  const { window, api } = loadWidget({
    timers,
    chat: () => (++calls === 1
      ? sse([
        { event: 'session', session_id: 's' },
        { event: 'error', message: NO_ANSWER_MESSAGE, request_id: 'req-9', error_id: 'err-7f3a' },
      ])
      : sse([{ event: 'content', content: REPLY }, { event: 'done', content: REPLY }])),
  });
  timers.arm();
  const container = window.document.querySelector('.osa-chat-widget');
  const written = [];
  Object.defineProperty(window.navigator, 'clipboard', { value: { writeText: async (text) => { written.push(text); } }, configurable: true });
  const before = api.getMessages().length;
  const input = container.querySelector('.osa-chat-input input');
  send(window, container, QUESTION);
  await waitFor(() => settled(container), 'the first send settles');
  const banner = container.querySelector('.osa-error');
  assertEqual(banner.style.display, 'block', 'the error banner is up');
  assert(banner.textContent.includes(NO_ANSWER_MESSAGE), 'with the server\'s message');
  assert(banner.textContent.includes('err-7f3a'), 'and the error id');
  assertEqual(input.value, QUESTION, 'the question is back in the box, to send again');
  assertEqual(api.getMessages().length, before, 'and the failed turn left nothing in the conversation');
  timers.fireAll();
  assertEqual(banner.style.display, 'block', 'the banner is still up after its old five seconds and long after');
  const style = window.getComputedStyle(banner);
  assertEqual(style.userSelect || style.getPropertyValue('user-select'), 'text', 'its text can be selected');
  const copy = banner.querySelector('.osa-error-copy');
  assert(copy, 'the error id has a copy button');
  copy.dispatchEvent(new window.Event('click', { bubbles: true }));
  await waitFor(() => written.length > 0, 'the id is written to the clipboard');
  assertEqual(written, ['err-7f3a'], 'the clipboard holds the id alone');
  // The next send starts clean.
  send(window, container, input.value);
  await waitFor(() => settled(container), 'the second send settles');
  assertEqual(banner.style.display, 'none', 'the next send takes the old error away');
  assertEqual(input.value, '', 'and, having worked, leaves the box empty');
  assert(lastReplyText(container).startsWith(REPLY.slice(0, 40)), 'with the answer on the page');
}

console.log('\nan error banner can be dismissed, and one with no error id shows no reference');
{
  const { window, api } = loadWidget({
    chat: () => sse([{ event: 'error', message: NO_ANSWER_MESSAGE }]),
  });
  const container = window.document.querySelector('.osa-chat-widget');
  send(window, container, 'A question');
  await waitFor(() => settled(container), 'the send settles');
  const banner = container.querySelector('.osa-error');
  assertEqual(banner.style.display, 'block', 'the error banner is up');
  assert(banner.textContent.includes(NO_ANSWER_MESSAGE), 'with the message');
  assert(!/Reference/.test(banner.textContent) && !banner.querySelector('.osa-error-copy'), 'and no reference or copy button, as there is no id');
  banner.querySelector('.osa-error-dismiss').dispatchEvent(new window.Event('click', { bubbles: true }));
  assertEqual(banner.style.display, 'none', 'the dismiss button takes it away');
  assertEqual(api.getMessages().length, 1, 'the conversation holds only its greeting');
}

console.log('\na failed request that lost the question hands it back too; an error after a partial reply does not, since that question is still in the conversation');
{
  const QUESTION = 'What is the sampling rate?';
  const { window } = loadWidget({ chat: () => json({ detail: 'The service is over capacity' }, { status: 503 }) });
  const container = window.document.querySelector('.osa-chat-widget');
  send(window, container, QUESTION);
  await waitFor(() => settled(container), 'the send settles');
  assertEqual(container.querySelector('.osa-chat-input input').value, QUESTION, 'an HTTP error: the question is back in the box');
  assert(container.querySelector('.osa-error').textContent.includes('over capacity'), 'with the reason on screen');
}
{
  const { window, api } = loadWidget({
    chat: () => sse([{ event: 'content', content: REPLY }, { event: 'error', message: 'the model went away', error_id: 'err-55' }]),
  });
  const container = window.document.querySelector('.osa-chat-widget');
  send(window, container, 'A question that got a partial reply');
  await waitFor(() => settled(container), 'the send settles');
  assertEqual(container.querySelector('.osa-chat-input input').value, '', 'a partial reply: the box is empty, the question is in the conversation');
  assertEqual(api.getMessages().map((m) => m.role), ['assistant', 'user', 'assistant'], 'with the question and the partial reply kept');
  assert(container.querySelector('.osa-error').textContent.includes('err-55'), 'and the error id is shown');
}

console.log('\na 502 for an empty or cut-off reply, not streamed, carries the reference a streamed error does');
{
  const { window } = loadWidget({
    chat: () => json({ detail: NO_ANSWER_MESSAGE, error_id: 'err-502a', request_id: 'req-502' }, { status: 502 }),
  });
  const container = window.document.querySelector('.osa-chat-widget');
  send(window, container, 'A question');
  await waitFor(() => settled(container), 'the send settles');
  const banner = container.querySelector('.osa-error');
  assert(banner.textContent.includes(NO_ANSWER_MESSAGE), 'the server\'s message is shown');
  assert(banner.textContent.includes('err-502a'), 'with the error id');
  assert(banner.querySelector('.osa-error-copy'), 'and the button that copies it');
}
{
  const { window } = loadWidget({ chat: () => json({ detail: 'The service is over capacity' }, { status: 503 }) });
  const container = window.document.querySelector('.osa-chat-widget');
  send(window, container, 'A question');
  await waitFor(() => settled(container), 'the send settles');
  const banner = container.querySelector('.osa-error');
  assert(!/Reference/.test(banner.textContent) && !banner.querySelector('.osa-error-copy'), 'an HTTP error with no error id shows no reference');
}

console.log('\nthe non-streamed fallback shows the warnings the response carries, as a stream does');
{
  const warn = console.warn;
  console.warn = () => {};
  try {
    const { window, api } = loadWidget({
      chat: () => json({
        message: { content: 'A short answer.' },
        session_id: 's',
        request_id: 'req-1',
        warnings: [CUT_OFF_MESSAGE, { message: LONG_MESSAGE, code: 'long_conversation' }],
      }),
    });
    const container = window.document.querySelector('.osa-chat-widget');
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the send settles');
    const banner = container.querySelector('.osa-warning');
    assertEqual(banner.style.display, 'block', 'the banner is up');
    assert(banner.textContent.includes(CUT_OFF_MESSAGE) && banner.textContent.includes(LONG_MESSAGE), 'with both warnings on it');
    const reply = api.getMessages().at(-1);
    assertEqual([reply.content, reply.cutOff === true], ['A short answer.', true], 'and the reply is marked cut off');
    assert(INCOMPLETE.test(lastReplyText(container)), 'where the page shows it');
    assert((window.localStorage.getItem('osa-test-paced') || '').includes('"cutOff":true'), 'and the mark is saved');
  } finally {
    console.warn = warn;
  }
}
{
  // The response the server sends today: `warnings` is a list of the messages, no codes.
  const warn = console.warn;
  console.warn = () => {};
  try {
    for (const [label, message] of [['output limit', CUT_OFF_MESSAGE], ['context window', CONTEXT_FULL_CUT_OFF_MESSAGE]]) {
      const { window, api } = loadWidget({
        chat: () => json({ message: { content: 'A short answer.' }, session_id: 's', warnings: [message] }),
      });
      const container = window.document.querySelector('.osa-chat-widget');
      send(window, container, 'A question');
      await waitFor(() => settled(container), 'the send settles');
      assert(container.querySelector('.osa-warning').textContent.includes(message), `${label}: the banner has the server's message`);
      assertEqual(api.getMessages().at(-1).cutOff === true, true, `${label}: and the reply is marked cut off`);
    }
  } finally {
    console.warn = warn;
  }
}
{
  const warn = console.warn;
  console.warn = () => {};
  try {
    for (const [label, warnings] of [['none', undefined], ['not a list', 'cut off'], ['nothing usable in it', [null, 7, '', {}, { code: 'cut_off' }]]]) {
      const { window, api } = loadWidget({ chat: () => json({ message: { content: 'Fine.' }, session_id: 's', warnings }) });
      const container = window.document.querySelector('.osa-chat-widget');
      send(window, container, 'A question');
      await waitFor(() => settled(container), 'the send settles');
      assertEqual([container.querySelector('.osa-warning').style.display, api.getMessages().at(-1).cutOff === true], ['none', false],
        `${label}: no banner, no mark`);
      assertEqual(lastReplyText(container), 'Fine.', `${label}: the answer is on the page`);
    }
  } finally {
    console.warn = warn;
  }
}

console.log('\na response that is not streamed shows its usage too');
{
  const usage = { input_tokens: 1240, output_tokens: 310, cache_read_tokens: 980, cache_creation_tokens: 0, estimated_cost: 0.0021, partial: false };
  const { window, api } = loadWidget({
    chat: () => json({ message: { content: 'A short answer.' }, session_id: 's', usage: { ...usage, evil: '<img src=x onerror=alert(1)>' } }),
  });
  const container = window.document.querySelector('.osa-chat-widget');
  send(window, container, 'A question');
  await waitFor(() => settled(container), 'the send settles');
  const lines = [...window.document.querySelectorAll('.osa-message-usage')].map((el) => el.textContent);
  assertEqual(lines, ['1,240 in (980 cached), 310 out, about $0.0021'], 'the line is there');
  assertEqual(api.getMessages().at(-1).usage, usage, 'and the message keeps only the fields the widget reads');
}

console.log('\nthe widget\'s own 120 s limit is described as a timeout, not shown as the browser\'s raw text');
{
  // `AbortSignal.timeout` aborts with a `TimeoutError`, not an `AbortError`: the widget's two
  // checks for a timeout looked only for the second, so the limit it sets itself fell
  // through to the browser's own message ("signal timed out").
  const warn = console.error;
  console.error = () => {};
  try {
    for (const name of ['TimeoutError', 'AbortError']) {
      const { window } = loadWidget({
        chat: () => { throw new DOMException('signal timed out', name); },
      });
      const container = window.document.querySelector('.osa-chat-widget');
      send(window, container, 'A question');
      await waitFor(() => settled(container), 'the send settles');
      assertEqual(container.querySelector('.osa-error-text').textContent, 'Request timed out. Please try again.',
        `a request that ends in a ${name} says it timed out`);
    }

    for (const name of ['TimeoutError', 'AbortError']) {
      const { window, api } = loadWidget();
      const container = window.document.querySelector('.osa-chat-widget');
      const started = api.getMessages().length;
      const encoder = new TextEncoder();
      const response = new Response(new ReadableStream({
        async start(controller) {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ event: 'content', content: REPLY })}\n\n`));
          await new Promise((resolve) => setTimeout(resolve, 40));
          controller.error(new DOMException('signal timed out', name));
        },
      }), { headers: { 'content-type': 'text/event-stream' } });
      await api.handleStreamingResponse(response, container).catch(() => {});
      const content = api.getMessages()[started].content;
      assert(content.startsWith(REPLY) && content.endsWith('_[Connection timeout]_'),
        `a stream that ends in a ${name} keeps its text and says the connection timed out`);
    }
  } finally {
    console.error = warn;
  }
}

console.log('\nwhat the browsers say when the network fails is described, not shown as they word it');
{
  // Chrome, Firefox and Safari each word a failed or cut-short request their own way; the
  // banner used to repeat "Load failed" or "network error" to the reader.
  const warn = console.error;
  console.error = () => {};
  try {
    for (const message of [
      'Failed to fetch',
      'NetworkError when attempting to fetch resource.',
      'Load failed',
      'network error',
      'The network connection was lost.',
      'The Internet connection appears to be offline.',
    ]) {
      const { window } = loadWidget({ chat: () => { throw new TypeError(message); } });
      const container = window.document.querySelector('.osa-chat-widget');
      send(window, container, 'A question');
      await waitFor(() => settled(container), 'the send settles');
      assertEqual(container.querySelector('.osa-error').textContent, 'Network error. Please check your connection.',
        `"${message}" is a network error`);
    }

    // A bug of the page's own is a TypeError too, and is not a network error.
    const { window } = loadWidget({ chat: () => { throw new TypeError("Cannot read properties of undefined (reading 'network')"); } });
    const container = window.document.querySelector('.osa-chat-widget');
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the send settles');
    assertEqual(container.querySelector('.osa-error').textContent, "Cannot read properties of undefined (reading 'network')",
      'a TypeError that only mentions "network" keeps its own text');

    // Only the browser's own failure, a TypeError, is taken for the network.
    const other = loadWidget({ chat: () => { throw new Error('Load failed'); } });
    const otherContainer = other.window.document.querySelector('.osa-chat-widget');
    send(other.window, otherContainer, 'A question');
    await waitFor(() => settled(otherContainer), 'the send settles');
    assertEqual(otherContainer.querySelector('.osa-error').textContent, 'Load failed',
      'an error of another kind with the same words keeps its own text');

    // The browsers' wordings are the whole message, or contain "fetch": a longer message
    // that merely ends the same way is not one of them.
    const longer = loadWidget({ chat: () => { throw new TypeError('x: load failed'); } });
    const longerContainer = longer.window.document.querySelector('.osa-chat-widget');
    send(longer.window, longerContainer, 'A question');
    await waitFor(() => settled(longerContainer), 'the send settles');
    assertEqual(longerContainer.querySelector('.osa-error').textContent, 'x: load failed',
      'a TypeError that only ends in "load failed" keeps its own text');
  } finally {
    console.error = warn;
  }
}

// ------------------------------------------------ silence is bounded, the run is not (#564, #593)

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sseLine = (event) => new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`);

/** A stream that delivers `events`, `gapMs` apart, and ends with the request's own abort. */
function pacedStream(events, gapMs, init) {
  return new Response(new ReadableStream({
    async start(controller) {
      init.signal.addEventListener('abort', () => {
        try { controller.error(init.signal.reason); } catch { /* already closed */ }
      }, { once: true });
      try {
        for (const event of events) {
          controller.enqueue(sseLine(event));
          if (gapMs) await sleep(gapMs);
        }
        controller.close();
      } catch { /* the request was aborted */ }
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
}

/** A stream that delivers `events` and then says nothing until the request is aborted. */
function silentAfter(events, init) {
  return new Response(new ReadableStream({
    start(controller) {
      for (const event of events) controller.enqueue(sseLine(event));
      init.signal.addEventListener('abort', () => controller.error(init.signal.reason), { once: true });
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
}

const QUIET_LIMIT_MS = 200;

/** Events with the wait before each, ended by the request's own abort. */
function sse2(timed, init) {
  return new Response(new ReadableStream({
    async start(controller) {
      init.signal.addEventListener('abort', () => {
        try { controller.error(init.signal.reason); } catch { /* already closed */ }
      }, { once: true });
      try {
        for (const [event, waitBefore] of timed) {
          if (waitBefore) await sleep(waitBefore);
          controller.enqueue(sseLine(event));
        }
        controller.close();
      } catch { /* the request was aborted */ }
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
}

console.log('\na reply that keeps working is not cut off, however long it takes');
{
  const events = [
    ...Array.from({ length: 8 }, (_, i) => ({ event: 'tool_start', name: `step_${i}`, input: {} })),
    { event: 'content', content: REPLY },
    { event: 'done', content: REPLY },
  ];
  const { window, widget } = loadWidget({ chat: (init) => pacedStream(events, QUIET_LIMIT_MS / 2, init) });
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  const began = Date.now();
  send(window, container, 'A question');
  await waitFor(() => settled(container), 'the send settles', 10000);
  assert(Date.now() - began > 3 * QUIET_LIMIT_MS, 'it ran for several times the limit on silence');
  assertEqual(container.querySelector('.osa-error').style.display === 'block', false, 'and no error was shown');
  assert(widget.__browser.getMessages().at(-1).content.startsWith(REPLY.slice(0, 40)), 'the reply is there');
}

console.log('\na stream that goes quiet is given up on, and the reader is offered Claude Haiku 4.5');
{
  const bodies = [];
  const { window, widget } = loadWidget({
    chat: (init) => {
      bodies.push(JSON.parse(init.body));
      return bodies.length === 1
        ? silentAfter([], init)
        : pacedStream([{ event: 'content', content: REPLY }, { event: 'done', content: REPLY }], 0, init);
    },
  });
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  const warn = console.error;
  console.error = () => {};
  try {
    const began = Date.now();
    send(window, container, 'The same question');
    await waitFor(() => settled(container), 'the send settles', 10000);
    assert(Date.now() - began >= QUIET_LIMIT_MS, 'it waited out the limit');
  } finally {
    console.error = warn;
  }
  assertEqual(container.querySelector('.osa-error-text').textContent, 'Request timed out. Please try again.', 'the banner says it timed out');
  const button = container.querySelector('.osa-error-suggest');
  assertEqual(button && button.textContent, 'Try Claude Haiku 4.5', 'and offers the model');
  assertEqual(container.querySelector('.osa-chat-input input').value, 'The same question', 'the question is back in the box');

  button.dispatchEvent(new window.Event('click', { bubbles: true }));
  await waitFor(() => bodies.length === 2 && settled(container), 'the question is sent again');
  assertEqual(bodies[0].model, undefined, 'the first request named no model');
  assertEqual(bodies[1].model, 'claude-haiku-4-5', 'the second names the offered one');
  assertEqual(bodies[1].message, 'The same question', 'with the same question');
  assertEqual(widget.__settings.get().model ?? null, null, 'and the saved model setting is not changed');
  assertEqual(container.querySelector('.osa-error-suggest'), null, 'the button went with the banner');
  assert(widget.__browser.getMessages().at(-1).content.startsWith(REPLY.slice(0, 40)), 'and the reply is there');
}

console.log('\nthe model a server error names is offered, when the widget can send it');
for (const [named, expected] of [
  [{ id: 'claude-sonnet-5-5', label: 'Claude Sonnet 5.5' }, 'Try Claude Sonnet 5.5'],
  [{ id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5' }, 'Try Claude Haiku 4.5'],
  [{ id: 'some-lab/their-model', label: '<b>Their model</b>' }, 'Try Claude Haiku 4.5'],
]) {
  const { window } = loadWidget({
    chat: () => sse([{ event: 'error', message: 'The current model (m) is not available right now.', suggested_model: named }]),
  });
  const container = window.document.querySelector('.osa-chat-widget');
  const warn = console.error;
  console.error = () => {};
  try {
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the send settles');
  } finally {
    console.error = warn;
  }
  const button = container.querySelector('.osa-error-suggest');
  assertEqual(button && button.textContent, expected, `${named.id}: ${expected}`);
  assertEqual(container.querySelector('.osa-error-text').textContent, 'The current model (m) is not available right now.', 'beside the server\'s own words');
}

console.log('\nthe model that failed is not the one suggested');
for (const [saved, expected] of [
  ['claude-haiku-4-5', 'Try Claude Sonnet 5.5'],
  ['claude-sonnet-5-5', 'Try Claude Haiku 4.5'],
]) {
  const { window, widget } = loadWidget({
    settings: { apiKey: null, model: saved, keyProvider: null },
    chat: (init) => silentAfter([], init),
  });
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  assertEqual(widget.__settings.get().model, saved, `${saved} is the saved model`);
  const warn = console.error;
  console.error = () => {};
  try {
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the send settles', 10000);
  } finally {
    console.error = warn;
  }
  const button = container.querySelector('.osa-error-suggest');
  assertEqual(button && button.textContent, expected, `after ${saved}: ${expected}`);
}

console.log('\na saved model that is an OpenRouter slug with a routing variant is still the model that failed');
{
  const { window, widget } = loadWidget({
    settings: { apiKey: `sk-or-v1-${'a'.repeat(48)}`, model: 'anthropic/claude-haiku-4.5:nitro', keyProvider: 'openrouter' },
    chat: (init) => silentAfter([], init),
  });
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  assertEqual(widget.__settings.get().model, 'anthropic/claude-haiku-4.5:nitro', 'the slug with its variant is the saved model');
  const warn = console.error;
  console.error = () => {};
  try {
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the send settles', 10000);
  } finally {
    console.error = warn;
  }
  const button = container.querySelector('.osa-error-suggest');
  assertEqual(button && button.textContent, 'Try Claude Sonnet 5.5', 'Haiku is not offered to a reader already on it');
}

console.log('\na request that timed out before the server answered offers no other model');
{
  const { window, widget } = loadWidget({
    chat: (init) => new Promise((_, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true });
    }),
  });
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  const warn = console.error;
  console.error = () => {};
  try {
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the send settles', 10000);
  } finally {
    console.error = warn;
  }
  assertEqual(container.querySelector('.osa-error-text').textContent, 'Request timed out. Please try again.', 'it says it timed out');
  assertEqual(container.querySelector('.osa-error-suggest'), null, 'with no button: the model is not what failed');
}

console.log('\nthe button sends what is in the box, so a question the reader narrowed is the one sent');
{
  const bodies = [];
  const { window, widget } = loadWidget({
    chat: (init) => {
      bodies.push(JSON.parse(init.body));
      return bodies.length === 1
        ? silentAfter([], init)
        : pacedStream([{ event: 'content', content: REPLY }, { event: 'done', content: REPLY }], 0, init);
    },
  });
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  const warn = console.error;
  console.error = () => {};
  try {
    send(window, container, 'A big question');
    await waitFor(() => settled(container), 'the send settles', 10000);
  } finally {
    console.error = warn;
  }
  container.querySelector('.osa-chat-input input').value = 'A smaller question';
  container.querySelector('.osa-error-suggest').dispatchEvent(new window.Event('click', { bubbles: true }));
  await waitFor(() => bodies.length === 2 && settled(container), 'the question is sent again');
  assertEqual(bodies[1].message, 'A smaller question', 'the edited question was sent');
  assertEqual(bodies[1].model, 'claude-haiku-4-5', 'on the suggested model');
}

console.log('\nan error response whose body never finishes does not hang the send');
{
  const { window, widget } = loadWidget({
    chat: (init) => new Response(new ReadableStream({
      start(controller) {
        init.signal.addEventListener('abort', () => controller.error(init.signal.reason), { once: true });
      },
    }), { status: 502, headers: { 'content-type': 'application/json' } }),
  });
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  const warn = console.error;
  console.error = () => {};
  try {
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the send settles', 10000);
  } finally {
    console.error = warn;
  }
  assert(container.querySelector('.osa-error-text').textContent.length > 0, 'the reader is told something went wrong');
}

console.log('\na reply that is not a stream, answered as JSON after the silence limit, is still taken');
{
  const { window, widget } = loadWidget({
    chat: (init) => new Response(new ReadableStream({
      async start(controller) {
        init.signal.addEventListener('abort', () => { try { controller.error(init.signal.reason); } catch { /* closed */ } }, { once: true });
        await sleep(QUIET_LIMIT_MS * 2);
        try {
          controller.enqueue(new TextEncoder().encode(JSON.stringify({ message: { content: 'A JSON answer' }, session_id: 's' })));
          controller.close();
        } catch { /* aborted */ }
      },
    }), { headers: { 'content-type': 'application/json' } }),
  });
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  send(window, container, 'A question');
  await waitFor(() => settled(container), 'the send settles', 10000);
  assertEqual(widget.__browser.getMessages().at(-1).content, 'A JSON answer', 'the answer is there');
}

console.log('\na server tool that is running has a longer allowance than a quiet model');
{
  const events = [{ event: 'tool_start', name: 'validate_hed_string', input: {} }, { event: 'tool_end', name: 'validate_hed_string', output: {} }];
  // 1.5 times the limit with a tool running: allowed.
  const slowTool = (init) => sse2([
    [events[0], 0], [events[1], QUIET_LIMIT_MS * 1.5], [{ event: 'content', content: REPLY }, 0], [{ event: 'done', content: REPLY }, 0],
  ], init);
  const { window, widget } = loadWidget({ chat: slowTool });
  widget.__idle.set(QUIET_LIMIT_MS, QUIET_LIMIT_MS * 3);
  const container = window.document.querySelector('.osa-chat-widget');
  send(window, container, 'A question');
  await waitFor(() => settled(container), 'the send settles', 10000);
  assert(widget.__browser.getMessages().at(-1).content.startsWith(REPLY.slice(0, 40)), 'a tool that took 1.5 times the limit finished and the reply arrived');

  // The same wait after the tool has ended: a quiet model, given up on.
  const quietModel = (init) => sse2([
    [events[0], 0], [events[1], 0], [{ event: 'content', content: 'Hel' }, QUIET_LIMIT_MS * 1.5], [{ event: 'done', content: 'Hello' }, 0],
  ], init);
  const second = loadWidget({ chat: quietModel });
  second.widget.__idle.set(QUIET_LIMIT_MS, QUIET_LIMIT_MS * 3);
  const secondContainer = second.window.document.querySelector('.osa-chat-widget');
  const warn = console.error;
  console.error = () => {};
  try {
    send(second.window, secondContainer, 'A question');
    await waitFor(() => settled(secondContainer), 'the send settles', 10000);
  } finally {
    console.error = warn;
  }
  assert(/timeout|timed out/i.test(second.widget.__browser.getMessages().at(-1).content + secondContainer.querySelector('.osa-error-text').textContent), 'a model that is quiet for 1.5 times the limit with no tool running is given up on');
}

console.log('\nan error that does not name a model offers none, and neither does a network failure');
for (const chat of [
  () => sse([{ event: 'error', message: 'Trying again will not help.', retryable: false }]),
  () => { throw new TypeError('Failed to fetch'); },
]) {
  const { window } = loadWidget({ chat });
  const container = window.document.querySelector('.osa-chat-widget');
  const warn = console.error;
  console.error = () => {};
  try {
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the send settles');
  } finally {
    console.error = warn;
  }
  assertEqual(container.querySelector('.osa-error-suggest'), null, 'no button');
}

console.log('\na reply that was partly written is not offered a resend: its question is still in the conversation');
{
  const { window, widget } = loadWidget({
    chat: (init) => silentAfter([{ event: 'content', content: REPLY }], init),
  });
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  const warn = console.error;
  console.error = () => {};
  try {
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the send settles', 10000);
  } finally {
    console.error = warn;
  }
  assertEqual(container.querySelector('.osa-error-suggest'), null, 'no button');
  assert(widget.__browser.getMessages().at(-1).content.endsWith('_[Connection timeout]_'), 'the reply ends with the note');
}

console.log('\nthe model of a resent question also runs the later runs of its reply');
{
  const resumeBodies = [];
  let api;
  const { window, widget, api: browser } = loadWidget({
    chat: async (init) => {
      const body = JSON.parse(init.body);
      if (body.model === 'claude-haiku-4-5') {
        // A browser run's result sent while the reply is being written, as its later run.
        await api.postResume({ session_id: 's' }, { ok: true });
        return pacedStream([{ event: 'content', content: REPLY }, { event: 'done', content: REPLY }], 0, init);
      }
      return silentAfter([], init);
    },
    resume: (init) => {
      resumeBodies.push(JSON.parse(init.body));
      return pacedStream([{ event: 'done', content: REPLY }], 0, init);
    },
  });
  api = browser;
  widget.__idle.set(QUIET_LIMIT_MS);
  const container = window.document.querySelector('.osa-chat-widget');
  const warn = console.error;
  console.error = () => {};
  try {
    send(window, container, 'A question');
    await waitFor(() => settled(container), 'the first send settles', 10000);
    container.querySelector('.osa-error-suggest').dispatchEvent(new window.Event('click', { bubbles: true }));
    await waitFor(() => resumeBodies.length === 1 && settled(container), 'the second send settles', 10000);
  } finally {
    console.error = warn;
  }
  assertEqual(resumeBodies[0].model, 'claude-haiku-4-5', 'the run after it named the same model');
  await widget.__browser.postResume({ session_id: 's' }, { ok: true }).catch(() => null);
  assertEqual(resumeBodies[1] && resumeBodies[1].model, undefined, 'and the override ended with the request');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed} checks, passed: ${passed}, failed: ${failed}`);
process.exit(failed === 0 ? 0 : 1);
