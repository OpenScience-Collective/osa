/**
 * Tests for OSA Widget Citation Marker Rendering
 *
 * These execute the widget's own renderInlineMarkdown against answer text,
 * which the Python drift tests in tests/test_frontend/ cannot do: they read
 * the widget source and assert on its shape. That difference matters here.
 * The bug these tests were written for (a real marker left unlinked when an
 * unrelated bracketed number appeared earlier in the same text run) was
 * invisible to a source-level check, because every regex involved looked
 * correct in isolation.
 *
 * Run with: bun frontend/test-citation-markers.js
 *
 * Not wired into CI, which has no JS runtime; see issue #377, which tracks
 * that gap for frontend/test-streaming.js as well. The always-running guard
 * is in tests/test_frontend/test_widget_citations.py.
 */

const { readFileSync } = require('fs');
const path = require('path');

let testsPassed = 0;
let testsFailed = 0;

function assert(condition, message) {
  if (!condition) {
    console.error(`  x FAIL: ${message}`);
    testsFailed++;
  } else {
    console.log(`  ok ${message}`);
    testsPassed++;
  }
}

// A DOM stub for escapeHtml, which is the only DOM user in the extracted
// functions. Escaping itself is covered by TestWidgetAttributeEscaping in
// tests/test_frontend/test_widget_drift.py; here it only has to not throw.
globalThis.document = {
  createElement() {
    return {
      textContent: '',
      get innerHTML() {
        return String(this.textContent)
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
      },
    };
  },
};
globalThis.window = { location: { origin: 'https://demo.osc.earth' } };

// Pull the renderer and its two helpers out of the shipped widget, so these
// tests exercise the file that is served rather than a copy of it.
function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`function not found in widget source: ${name}`);
  let depth = 0;
  let i = source.indexOf('{', start);
  for (; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) break;
    }
  }
  return source.slice(start, i + 1);
}

const widgetSource = readFileSync(path.join(__dirname, 'osa-chat-widget.js'), 'utf8');
const renderInlineMarkdown = new Function(
  ['escapeHtml', 'isSafeUrl', 'renderInlineMarkdown']
    .map((name) => extractFunction(widgetSource, name))
    .join('\n') + '\nreturn renderInlineMarkdown;'
)();
const normalizeCitationMarkersAtSentenceEnd = new Function(
  extractFunction(widgetSource, 'normalizeCitationMarkersAtSentenceEnd') +
    '\nreturn normalizeCitationMarkersAtSentenceEnd;'
)();

const CITATIONS = {
  1: { marker: 1, source: 'https://example.com/doc', title: 'Doc', cited_text: 'a cited span' },
  2: { marker: 2, source: 'https://example.com/other', title: 'Other', cited_text: 'another span' },
};

function linkedMarkers(rendered) {
  return [...rendered.matchAll(/<sup class="osa-citation">.*?\[(\d+)\].*?<\/sup>/g)].map(
    (m) => m[1]
  );
}

console.log('\nCitation markers in rendered answers\n');

// The regression this file exists for.
assert(
  linkedMarkers(
    renderInlineMarkdown('See item [42] for details, this claim is supported[1].', CITATIONS)
  ).join(',') === '1',
  'a real marker is linked even when an unknown bracketed number comes first'
);

assert(
  linkedMarkers(renderInlineMarkdown('arr[0] then a real marker [1].', CITATIONS)).join(',') === '1',
  'an array index earlier in the run does not suppress a real marker'
);

assert(
  linkedMarkers(
    renderInlineMarkdown('Prose [42], marker [1], prose [43], marker [2].', CITATIONS)
  ).join(',') === '1,2',
  'unknown numbers interleaved with markers leave every real marker linked'
);

// Text must survive intact whether or not anything is linked.
const withUnknown = renderInlineMarkdown('Only an unknown bracket [42] here.', CITATIONS);
assert(linkedMarkers(withUnknown).length === 0, 'an unknown bracketed number is not linked');
assert(
  withUnknown === 'Only an unknown bracket [42] here.',
  'text with no real marker is returned unchanged'
);

assert(
  renderInlineMarkdown('No brackets at all.', CITATIONS) === 'No brackets at all.',
  'plain text is returned unchanged'
);

assert(
  normalizeCitationMarkersAtSentenceEnd(
    'Infomax is implemented in ru[1]nica.m, a MATLAB version.',
    [CITATIONS[1]]
  ) === 'Infomax is implemented in runica.m, a MATLAB version.[1]',
  'a marker split into the middle of a word moves to the sentence end'
);

assert(
  normalizeCitationMarkersAtSentenceEnd('[1]The cited claim.', [CITATIONS[1]]) ===
    'The cited claim.[1]',
  'a leading marker moves after the sentence'
);

assert(
  normalizeCitationMarkersAtSentenceEnd('First claim.[1] Next claim.', [CITATIONS[1]]) ===
    'First claim.[1] Next claim.',
  'a marker already after a sentence stays with that sentence'
);

assert(
  normalizeCitationMarkersAtSentenceEnd('First. Second.[1] Next.', [CITATIONS[1]]) ===
    'First. Second.[1] Next.',
  'a marker after the last of multiple sentences stays with that sentence'
);

// Behavior that was already correct, pinned so the scan change cannot break it.
assert(
  linkedMarkers(renderInlineMarkdown('One [1] and again [1].', CITATIONS)).join(',') === '1,1',
  'a repeated marker is linked at every occurrence'
);

const withLink = renderInlineMarkdown('A [markdown link](https://example.com) plus [1].', CITATIONS);
assert(
  withLink.includes('<a href="https://example.com"') && linkedMarkers(withLink).join(',') === '1',
  'a markdown link is not mistaken for a marker, and a later marker still links'
);

assert(
  renderInlineMarkdown('A claim [1].', null) === 'A claim [1].',
  'with no citation map, a bracketed number stays plain text'
);

assert(
  linkedMarkers(renderInlineMarkdown('**Bold** then [1].', CITATIONS)).join(',') === '1',
  'a marker after other inline markup still links'
);

console.log(`\n${testsPassed} passed, ${testsFailed} failed\n`);
process.exit(testsFailed === 0 ? 0 : 1);
