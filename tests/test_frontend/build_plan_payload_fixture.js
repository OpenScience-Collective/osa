#!/usr/bin/env bun
// Drive the REAL widget through the plan card and record what it posts to /chat/resume, for
// tests/test_frontend/test_plan_resume_payload.py to feed to the server's own models
// (ResumeRequest, PlanDecision, build_plan_tool_message).
//
// The widget, the runtime bundle as built and its plan logic are the real ones, in a
// happy-dom window; the server's side of the conversation is the recorded events of
// frontend/test-support/plan-widget-harness.js. Nothing here builds a payload itself:
// every body below is one the widget sent after the clicks the scenario makes.
//
// Usage: bun build_plan_payload_fixture.js <output-json-path>

import { writeFileSync } from 'node:fs';
import { PLAN_LIMITS } from '../../frontend/osa-plan.js';
import {
  PLAN_ARGS, approve, click, doneEvent, proposeFirst, start, typeInto, waitUntil,
} from '../../frontend/test-support/plan-widget-harness.js';

const outPath = process.argv[2];
if (!outPath) {
  console.error('usage: bun build_plan_payload_fixture.js <output-json-path>');
  process.exit(1);
}

/** Run one scenario on a page of its own; resolves with the /chat/resume body it posted. */
async function scenario({ args = PLAN_ARGS, allowPageContext = false }, act) {
  const page = await start({ allowPageContext });
  await proposeFirst(page, args);
  const finish = await act(page);
  await waitUntil(() => page.requests.some((r) => r.kind === 'resume'), 'the decision is posted');
  finish.send(doneEvent('Done.'));
  finish.close();
  return { proposed: args, body: page.requests.find((r) => r.kind === 'resume').body };
}

const out = {};

// Edited, added and removed steps, approved in pause mode.
out.approved_edited = await scenario({}, async (page) => {
  click(page, page.qa('.osa-plan-edit-btn')[0]);
  typeInto(page, page.q('.osa-plan-input'), 'Count the   face onsets');
  typeInto(page, page.q('.osa-plan-textarea'), 'Per condition, from the events file.');
  click(page, page.q('.osa-plan-save'));
  click(page, page.q('.osa-plan-add'));
  typeInto(page, page.q('.osa-plan-input'), 'Summarize');
  typeInto(page, page.q('.osa-plan-textarea'), 'Say what the N170 shows.');
  click(page, page.q('.osa-plan-save'));
  click(page, page.qa('.osa-plan-remove-btn')[1]);
  return approve(page);
});

// The plan as proposed, run all.
out.approved_run_all = await scenario({}, (page) => approve(page, { runAll: true }));

// Changes asked for, with a note.
out.changes_with_feedback = await scenario({}, async (page) => {
  click(page, page.q('.osa-plan-ask'));
  typeInto(page, page.q('.osa-plan-feedback textarea'), 'Use sub-003 and leave out the EOG channels.');
  const next = page.queue();
  click(page, page.q('.osa-plan-send-feedback'));
  return next;
});

// Changes asked for with no note.
out.changes_without_feedback = await scenario({}, async (page) => {
  click(page, page.q('.osa-plan-ask'));
  const next = page.queue();
  click(page, page.q('.osa-plan-send-feedback'));
  return next;
});

// The largest plan the server accepts, edited to the largest the card allows: eight steps with
// 120-character titles and 600-character details, and a note of 1000 characters.
const longArgs = {
  findings: 'f'.repeat(PLAN_LIMITS.findings),
  est_peak_mb: PLAN_LIMITS.peakMb,
  steps: Array.from({ length: PLAN_LIMITS.steps }, (_, i) => ({
    title: `${i}`.padEnd(PLAN_LIMITS.title, 't'),
    detail: `${i}`.padEnd(PLAN_LIMITS.detail, 'd'),
  })),
};
out.changes_largest = await scenario({ args: longArgs }, async (page) => {
  click(page, page.q('.osa-plan-ask'));
  typeInto(page, page.q('.osa-plan-feedback textarea'), 'n'.repeat(PLAN_LIMITS.feedback + 500));
  const next = page.queue();
  click(page, page.q('.osa-plan-send-feedback'));
  return next;
});

// Characters outside the Basic Multilingual Plane (an emoji is two UTF-16 code units, one
// code point): the server's limits count code points, so a title of 120 code points, a
// detail of 600 and a note over 1000 must be sent whole or cut at a code point, never in
// the middle of a surrogate pair, which the server's JSON parser refuses.
const EMOJI = '\u{1F600}';
const astralArgs = {
  findings: `a${EMOJI.repeat(PLAN_LIMITS.findings - 1)}`,
  est_peak_mb: 300,
  steps: [{
    title: `a${EMOJI.repeat(PLAN_LIMITS.title - 1)}`,
    detail: `a${EMOJI.repeat(PLAN_LIMITS.detail - 1)}`,
  }],
};
out.changes_astral = await scenario({ args: astralArgs }, async (page) => {
  click(page, page.q('.osa-plan-ask'));
  typeInto(page, page.q('.osa-plan-feedback textarea'), `n${EMOJI.repeat(PLAN_LIMITS.feedback + 100)}`);
  const next = page.queue();
  click(page, page.q('.osa-plan-send-feedback'));
  return next;
});

// With the page context a reader shares by default: the body the widget really sends.
out.approved_with_page_context = await scenario({ allowPageContext: true }, (page) => approve(page));

out.limits = PLAN_LIMITS;
writeFileSync(outPath, JSON.stringify(out));
process.exit(0);
