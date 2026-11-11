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

console.log('\nCode spans are found before emphasis (globs are common in EEGLAB and BIDS answers)\n');

// An italic match that starts before a code span must not swallow it: the lone "*" in
// "*.set" has no partner outside the span, so it is text.
assert(
  markdownToHtml('Use *.set or `*.fdt` files') === '<p>Use *.set or <code>*.fdt</code> files</p>',
  'a lone asterisk before a code span holding a glob is text, and the span stays code'
);
assert(
  markdownToHtml('Match `**/*.json` and `**/*.tsv` files') ===
    '<p>Match <code>**/*.json</code> and <code>**/*.tsv</code> files</p>',
  'two code spans holding globstar patterns stay code'
);
assert(
  markdownToHtml('* Read *.set or `*.fdt` here') === '<ul><li>Read *.set or <code>*.fdt</code> here</li></ul>',
  'the same in a bullet list item'
);
assert(
  markdownToHtml('## Reading *.set and `*.fdt`') === '<h2>Reading *.set and <code>*.fdt</code></h2>',
  'the same in a heading'
);
assert(
  markdownToHtml('| Pattern | Use |\n|---|---|\n| *.set | `*.fdt` |').includes('<td>*.set</td><td><code>*.fdt</code></td>'),
  'the same across table cells'
);
assert(
  markdownToHtml('**Note:** `**/*.json` and *emphasis* stay apart') ===
    '<p><strong>Note:</strong> <code>**/*.json</code> and <em>emphasis</em> stay apart</p>',
  'bold and italic beside code still render'
);
assert(
  markdownToHtml('**bold**`code` and `code`**bold**') ===
    '<p><strong>bold</strong><code>code</code> and <code>code</code><strong>bold</strong></p>',
  'bold directly against code on either side renders both'
);
assert(
  markdownToHtml('*emphasis* then `a*b` then *more*') === '<p><em>emphasis</em> then <code>a*b</code> then <em>more</em></p>',
  'an asterisk inside code does not pair with one outside it'
);
assert(
  markdownToHtml('**use `*.set` files**') === '<p><strong>use <code>*.set</code> files</strong></p>',
  'code inside bold renders as code'
);
assert(
  markdownToHtml('[`pop_loadset`](https://sccn.ucsd.edu/eeglab/) and `x`') ===
    '<p><a href="https://sccn.ucsd.edu/eeglab/" target="_blank" rel="noopener noreferrer"><code>pop_loadset</code></a> and <code>x</code></p>',
  'code inside a link text renders as code and the link survives'
);
assert(
  markdownToHtml('See https://example.com/a and `b`') ===
    '<p>See <a href="https://example.com/a" target="_blank" rel="noopener noreferrer">https://example.com/a</a> and <code>b</code></p>',
  'a plain URL beside a code span still links'
);
assert(
  markdownToHtml('an unmatched ` backtick and *italic*') === '<p>an unmatched ` backtick and <em>italic</em></p>',
  'an unmatched backtick stays text and does not stop other markup'
);
{
  // The placeholder a code span leaves in the run is built from private-use characters;
  // text that carries them itself must not be able to name a span.
  const open = String.fromCharCode(0xe000);
  const close = String.fromCharCode(0xe001);
  const forged = markdownToHtml(`forged ${open}0${close} then \`real\``);
  assert(
    (forged.match(/<code>/g) || []).length === 1 && forged.includes('<code>real</code>') && !forged.includes(open),
    'private-use characters in the text cannot forge a code span'
  );
}

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

console.log('\nText that renders to nothing is escaped, not returned as markup\n');

// A reply that is only an opening fence renders no block, and the raw text used to be
// returned as HTML: an info string such as <img onerror=...> became a live element.
const loneFence = markdownToHtml('```<img src=x onerror=alert(1)>');
assert(!loneFence.includes('<img'), 'a lone opening fence with HTML in its info string is not returned as markup');
assert(loneFence.includes('&lt;img src=x onerror=alert(1)&gt;'), 'the lone fence text is shown escaped');
assert(markdownToHtml('```') === '```', 'a bare lone fence is shown as typed');
assert(markdownToHtml('') === '', 'an empty reply renders as nothing');
assert(
  !markdownToHtml('Here is a tag: <img src=x onerror=alert(1)//').includes('<img'),
  'a "<" in a paragraph is escaped, not passed through as the start of a tag'
);

console.log('\nLinks, code and emphasis inside bold and italic (#666)\n');

const SAFE_LINK = (text, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`;
assert(
  markdownToHtml('**[text](https://x)**') === `<p><strong>${SAFE_LINK('text', 'https://x')}</strong></p>`,
  'a link that is all of a bold run renders as a link inside <strong>'
);
assert(
  markdownToHtml('**see [the docs](https://x.org/a) first**') ===
    `<p><strong>see ${SAFE_LINK('the docs', 'https://x.org/a')} first</strong></p>`,
  'a link in the middle of bold text renders, with the text on either side kept'
);
assert(
  markdownToHtml('**[`pop_loadset`](https://x)**') ===
    `<p><strong>${SAFE_LINK('<code>pop_loadset</code>', 'https://x')}</strong></p>`,
  'a link whose text is a code span, inside bold, renders both'
);
assert(
  markdownToHtml('**run `a*b` now** and `c`') === '<p><strong>run <code>a*b</code> now</strong> and <code>c</code></p>',
  'code inside bold stays valid code with its own asterisk, and a span after the bold is not confused with it'
);
assert(
  markdownToHtml('*see [the docs](https://x)*') === `<p><em>see ${SAFE_LINK('the docs', 'https://x')}</em></p>`,
  'a link inside italic renders'
);
assert(
  markdownToHtml('**bold *and italic* text**') === '<p><strong>bold <em>and italic</em> text</strong></p>',
  'italic inside bold renders'
);
assert(
  markdownToHtml('*italic* and **bold**, then `code`') === '<p><em>italic</em> and <strong>bold</strong>, then <code>code</code></p>',
  'italic, bold and code side by side keep rendering as before'
);
{
  const unsafe = markdownToHtml('**[click](javascript:alert(1))**');
  assert(!unsafe.includes('<a') && !unsafe.includes('href'), 'a javascript: link inside bold renders no anchor');
  assert(unsafe.includes('<strong>click'), 'and its text is kept, in the bold');
  const unsafeItalic = markdownToHtml('*[click](javascript:alert(1))*');
  assert(!unsafeItalic.includes('href'), 'a javascript: link inside italic renders no anchor');
  const dataUrl = markdownToHtml('**[x](data:text/html;base64,PHNjcmlwdD4=)**');
  assert(!dataUrl.includes('href'), 'a data: link inside bold renders no anchor');
  const forged = markdownToHtml('**[x](https://a.test/"onmouseover="alert(1))**');
  assert(!/<a [^>]*"\s*onmouseover=/.test(forged) && !forged.includes('" onmouseover'), 'a quote in a link inside bold cannot open an attribute');
}
assert(
  markdownToHtml('**<img src=x onerror=alert(1)>**') === '<p><strong>&lt;img src=x onerror=alert(1)&gt;</strong></p>',
  'markup inside bold is escaped'
);

console.log('\nImage references point to the figure above, never load one (#666)\n');

{
  const plain = markdownToHtml('![Welch PSD](https://x/y.png)');
  assert(!plain.includes('<img') && !plain.includes('<a') && !plain.includes('href'), 'an image reference renders no image and no link');
  assert(!plain.includes('x/y.png'), 'and the remote address is not in the output');
  assert(!plain.replace(/<[^>]*>/g, '').includes('!'), 'and no stray "!" is left');
  assert(plain.replace(/<[^>]*>/g, '') === 'Welch PSD', 'with no run figure it reads as the alt text alone');

  const withFigure = markdownToHtml('![Welch PSD](https://x/y.png)', undefined, { figureAbove: true });
  assert(!withFigure.includes('<img') && !withFigure.includes('<a'), 'with a run figure it is still no image and no link');
  assert(withFigure.replace(/<[^>]*>/g, '') === 'Welch PSD (see the figure above)', 'but reads as the alt text with a pointer to the figure above');
  assert(withFigure.includes('<span class="osa-image-reference">'), 'inside one span');

  assert(
    markdownToHtml('Result: ![PSD](https://x/y.png) shown, and [a link](https://x).', undefined, { figureAbove: true }) ===
      '<p>Result: <span class="osa-image-reference">PSD<span class="osa-image-hint"> (see the figure above)</span></span> shown, and ' +
      `${SAFE_LINK('a link', 'https://x')}.</p>`,
    'an image reference between text and an ordinary link leaves the link alone'
  );
  const emptyAlt = markdownToHtml('![](https://x/y.png)', undefined, { figureAbove: true });
  assert(emptyAlt.replace(/<[^>]*>/g, '') === '(see the figure above)', 'an image with no alt text still points to the figure');
  assert(markdownToHtml('![](https://x/y.png)') === '<p></p>', 'an image with no alt text and no figure to point to renders nothing');
  const hostile = markdownToHtml('![<img src=x onerror=alert(1)>](javascript:alert(1))', undefined, { figureAbove: true });
  assert(!hostile.includes('<img') && !hostile.includes('javascript:') && !hostile.includes('href'), 'a hostile alt text and address render as escaped text only');
  const inCode = markdownToHtml('![a `b` c](https://x/y.png)');
  assert(inCode.includes('<code>b</code>') && !inCode.includes('<img'), 'a code span in the alt text renders as code');
  assert(
    markdownToHtml('**![Fig](https://x/y.png)**').includes('<strong><span class="osa-image-reference">Fig</span></strong>'),
    'an image reference inside bold renders as a reference too'
  );
  const inTable = markdownToHtml('| a |\n|---|\n| ![T](https://x/t.png) |', undefined, { figureAbove: true });
  assert(!inTable.includes('<img') && inTable.includes('(see the figure above)'), 'in a table cell, the option reaches the reference');
  assert(markdownToHtml('wow! [x](https://y) ok').includes('wow! ' + SAFE_LINK('x', 'https://y')), 'a "!" ending a sentence before a link stays text beside the link');
}

console.log(`\n${testsPassed} passed, ${testsFailed} failed\n`);
process.exit(testsFailed === 0 ? 0 : 1);
