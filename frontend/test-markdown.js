/**
 * Tests for the OSA widget's markdown rendering (markdownToHtml / renderInlineMarkdown).
 *
 * Like test-citation-markers.js, these run the widget's own functions, extracted from
 * the shipped osa-chat-widget.js, rather than a copy.
 *
 * Regression covered: inline code was only converted in plain paragraphs, so a list
 * item such as "* `session_description`: ..." rendered with literal backticks, and
 * the same in numbered lists, headings and table cells. Also covers indented
 * (nested) list items, which used to fall through to a paragraph with a literal "*".
 *
 * Run with: bun frontend/test-markdown.js
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

// DOM stub for escapeHtml, the only DOM user among the extracted functions.
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
const markdownToHtml = new Function(
  'ICONS',
  'getCodeBlockId',
  ['escapeHtml', 'isSafeUrl', 'renderInlineMarkdown', 'markdownToHtml']
    .map((name) => extractFunction(widgetSource, name))
    .join('\n') + '\nreturn markdownToHtml;'
)({ copy: '' }, () => 'code-block');

console.log('\nInline code outside paragraphs\n');

// The answer that exposed the bug.
const requiredFields = markdownToHtml(
  [
    'Required fields:',
    '',
    '* `session_description`: Description of the experimental session',
    '* `identifier`: Unique identifier (UUID recommended)',
    '* `session_start_time`: Reference time for all timestamps in the file (datetime object with timezone)',
  ].join('\n')
);
assert(
  requiredFields.includes('<li><code>session_description</code>: Description of the experimental session</li>') &&
    requiredFields.includes('<li><code>session_start_time</code>: Reference time'),
  'inline code in a bullet list item renders as <code>'
);
assert(!requiredFields.includes('`'), 'no literal backticks are left in the rendered list');

assert(
  markdownToHtml('1. Call `nwbRead` first').includes('<li>Call <code>nwbRead</code> first</li>'),
  'inline code in a numbered list item renders as <code>'
);
assert(
  markdownToHtml('## The `NWBFile` object') === '<h2>The <code>NWBFile</code> object</h2>',
  'inline code in a heading renders as <code>'
);
assert(
  markdownToHtml('| Field | Type |\n|---|---|\n| `identifier` | str |').includes('<td><code>identifier</code></td>'),
  'inline code in a table cell renders as <code>'
);

console.log('\nCode content is escaped and not parsed\n');

assert(
  markdownToHtml('* `a*b*c` and `x**y**z`').includes('<code>a*b*c</code> and <code>x**y**z</code>'),
  'emphasis markers inside inline code are left alone'
);
assert(
  markdownToHtml('* `<script>alert(1)</script>`').includes('<code>&lt;script&gt;alert(1)&lt;/script&gt;</code>'),
  'HTML inside inline code is escaped'
);
const citations = { 1: { marker: 1, source: 'https://example.com', title: 'Doc', cited_text: 'span' } };
assert(
  !markdownToHtml('* index with `arr[1]` here', citations).includes('osa-citation'),
  'a bracketed number inside inline code is not linked as a citation'
);

console.log('\nParagraphs keep their behavior\n');

assert(
  markdownToHtml('Use `NWBHDF5IO` and **always** set a timezone.') ===
    '<p>Use <code>NWBHDF5IO</code> and <strong>always</strong> set a timezone.</p>',
  'a paragraph with inline code and bold renders as before'
);
assert(
  markdownToHtml('See [PyNWB](https://pynwb.readthedocs.io) for `NWBFile`.') ===
    '<p>See <a href="https://pynwb.readthedocs.io" target="_blank" rel="noopener noreferrer">PyNWB</a> for <code>NWBFile</code>.</p>',
  'a paragraph with a link and inline code renders as before'
);

console.log('\nIndented (nested) list items\n');

const nested = markdownToHtml('* Subject fields:\n  * `subject_id`\n  * `species`\n* Done');
assert(!nested.includes('<p>'), 'indented items stay in the list instead of becoming paragraphs');
assert(
  (nested.match(/<li>/g) || []).length === 4 && nested.includes('<li><code>species</code></li>'),
  'indented items become list items'
);
assert(
  markdownToHtml('1. First\n   2. Second').includes('<li>Second</li>'),
  'an indented numbered item becomes a list item'
);

console.log(`\n${testsPassed} passed, ${testsFailed} failed\n`);
process.exit(testsFailed === 0 ? 0 : 1);
