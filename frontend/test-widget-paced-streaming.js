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

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed} checks, passed: ${passed}, failed: ${failed}`);
process.exit(failed === 0 ? 0 : 1);
