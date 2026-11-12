/**
 * The conversation notebook builder (E4.1, #667): cells, ordering, import
 * hoisting, left-out runs, ids and the percent-format script's shape.
 *
 * Runs over the recorded conversations in test-support/notebook-conversations.js.
 * That the notebook is valid nbformat 4.5, that every cell compiles, and that
 * Jupytext turns the script back into the same cells are checked by real
 * `nbformat` and `jupytext` in tests/test_frontend/test_notebook_builder.py;
 * this file covers what does not need Python.
 *
 * Run with: bun frontend/test-notebook-builder.js
 */

import { buildConversationNotebook } from './osa-notebook-builder.js';
import { NOTEBOOK_CONVERSATIONS, run } from './test-support/notebook-conversations.js';

let passed = 0;
let failed = 0;

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

const build = (input) => buildConversationNotebook(input);
const kinds = (notebook) => notebook.cells.map((cell) => cell.cell_type);
const sources = (notebook) => notebook.cells.map((cell) => cell.source);
const lines = (text) => text.split('\n');

function conversation(executions, overrides = {}) {
  return { ...NOTEBOOK_CONVERSATIONS.erp(), executions, ...overrides };
}

console.log('The cells of a conversation with failures');
{
  const input = NOTEBOOK_CONVERSATIONS.erp();
  const { notebook, stats } = build(input);
  const succeeded = input.executions.filter((r) => r.status === 'ok');
  assertEqual(
    kinds(notebook),
    ['markdown', 'code', ...succeeded.flatMap(() => ['markdown', 'code']), 'markdown'],
    'title, setup, a heading and a code cell per successful run, then the closing note'
  );
  assertEqual(stats.steps, succeeded.length, 'stats.steps counts the runs that went in');
  assertEqual(stats.leftOut.map((entry) => entry.position), [4], 'the failed run is named by its position in the chat');
  assertEqual(notebook.nbformat, 4, 'nbformat 4');
  assertEqual(notebook.nbformat_minor, 5, 'minor version 5');
  assertEqual(
    notebook.metadata.kernelspec,
    { display_name: 'Python (Pyodide)', language: 'python', name: 'python' },
    'the Pyodide kernelspec'
  );
  assertEqual(notebook.metadata.language_info.name, 'python', 'language_info names python');
  assert(
    notebook.cells.filter((cell) => cell.cell_type === 'code').every((cell) => cell.outputs.length === 0 && cell.execution_count === null),
    'code cells carry no outputs'
  );
}

console.log('\nThe title cell');
{
  const { notebook } = build(NOTEBOOK_CONVERSATIONS.erp());
  assertEqual(
    notebook.cells[0].source,
    "# Famous vs scrambled ERPs in on002718\n\nFrom a chat with NEMAR Assistant on Nov 10, 2026. Data: NEMAR's streaming copy, quantized and lossy.",
    'a heading and the origin line'
  );
  const lateEvening = build({ ...NOTEBOOK_CONVERSATIONS.erp(), createdAt: '2026-11-10T23:59:59-08:00' });
  assert(lateEvening.notebook.cells[0].source.includes('on Nov 11, 2026'), 'the date is formatted in UTC, whatever the zone of the input');
  const byMillis = build({ ...NOTEBOOK_CONVERSATIONS.erp(), createdAt: Date.UTC(2026, 0, 2, 3) });
  assert(byMillis.notebook.cells[0].source.includes('on Jan 2, 2026'), 'a millisecond timestamp works');
  const undated = build({ ...NOTEBOOK_CONVERSATIONS.erp(), createdAt: 'not a date' });
  assert(!undated.notebook.cells[0].source.includes(' on '), 'an unusable date is left out rather than printed as Invalid Date');
  const untitled = build({ ...NOTEBOOK_CONVERSATIONS.erp(), title: '  ', communityName: '' });
  assert(untitled.notebook.cells[0].source.startsWith('# Notebook for on002718'), 'a blank title falls back to the dataset');
  assert(untitled.notebook.cells[0].source.includes('From a chat with OSA'), 'a blank community name falls back to OSA, with no article to get wrong');
}

console.log('\nThe setup cell');
{
  const { notebook } = build(NOTEBOOK_CONVERSATIONS.erp());
  const setup = notebook.cells[1];
  assertEqual(setup.cell_type, 'code', 'it is one code cell');
  const setupLines = lines(setup.source);
  assertEqual(setupLines.slice(0, 2), ['%pip install eegprep-lean scipy', '%matplotlib inline'], 'it installs and sets up plotting first');
  assert(setup.source.includes('import eegprep_lean\n'), 'it imports eegprep_lean');
  const indexAt = setupLines.indexOf('index = await eegprep_lean.read_index(');
  assertEqual(
    setupLines.slice(indexAt, indexAt + 3),
    ['index = await eegprep_lean.read_index(', '    "on002718", index_url="https://zarr.nemar.org/on002718/zarr/index.json"', ')'],
    'read_index takes the index_url, as the starter notebook does'
  );
  assert(setupLines.indexOf('import eegprep_lean') < indexAt, 'and it follows import eegprep_lean');
  const noBase = build({ ...NOTEBOOK_CONVERSATIONS.erp(), zarrBase: undefined });
  assert(lines(noBase.notebook.cells[1].source).includes('index = await eegprep_lean.read_index("on002718")'), 'without zarrBase there is no index_url');
  const slash = build({ ...NOTEBOOK_CONVERSATIONS.erp(), zarrBase: 'https://zarr.nemar.org///' });
  assert(slash.notebook.cells[1].source.includes('"https://zarr.nemar.org/on002718/zarr/index.json"'), 'a trailing slash on zarrBase does not double up');
  let thrown = null;
  try {
    build({ ...NOTEBOOK_CONVERSATIONS.erp(), datasetId: '' });
  } catch (err) {
    thrown = err;
  }
  assert(thrown instanceof TypeError, 'a missing datasetId is refused');
  const quoted = build({ ...NOTEBOOK_CONVERSATIONS.erp(), datasetId: 'a"b' });
  assert(quoted.notebook.cells[1].source.includes('read_index(\n    "a\\"b"'), 'a dataset id is written as a quoted Python string, not spliced in');
}

console.log('\nImport hoisting');
{
  const same = 'import numpy as np';
  const { notebook } = build(
    conversation([
      run({ code: `${same}\nx = 1` }),
      run({ code: `${same}\ny = x + 1` }),
      run({ code: `${same}\nz = y + 1` }),
    ])
  );
  const setup = notebook.cells[1].source;
  assertEqual(setup.split(same).length - 1, 1, 'an import repeated in three runs appears once in the setup cell');
  const stepCode = notebook.cells.filter((c) => c.id.endsWith('-code') && c.id !== 'setup').map((c) => c.source);
  assertEqual(stepCode, ['x = 1', 'y = x + 1', 'z = y + 1'], 'and is gone from the step cells');
  assert(
    setup.indexOf('import eegprep_lean') < setup.indexOf('index = await') && setup.indexOf('index = await') < setup.indexOf(same),
    'hoisted imports come after index is defined, so a package the kernel lacks cannot leave every step without it'
  );
}
{
  const { notebook } = build(
    conversation([
      run({ code: 'import zlib\nimport numpy as np\nprint(1)' }),
      run({ code: 'from math import floor\nimport numpy as np\nimport zlib\nprint(2)' }),
    ])
  );
  const setupLines = lines(notebook.cells[1].source);
  assertEqual(
    setupLines.slice(setupLines.indexOf('import zlib')),
    ['import zlib', 'import numpy as np', 'from math import floor'],
    'imports keep the order in which they were first seen'
  );
}
{
  const { notebook } = build(
    conversation([
      run({ code: 'import numpy as np\nimport numpy\nprint(1)' }),
      run({ code: 'import numpy as np  \nimport   numpy as   np\nprint(2)' }),
    ])
  );
  const setup = notebook.cells[1].source;
  assert(setup.includes('import numpy as np') && lines(setup).includes('import numpy'), 'a different alias of one module is a different import');
  assertEqual(setup.split('import numpy as np').length - 1, 1, 'whitespace differences do not defeat the deduplication');
}
{
  const multi = 'from scipy.signal import (\n    butter,\n    sosfiltfilt,  # zero-phase\n)';
  const { notebook } = build(
    conversation([run({ code: `${multi}\nprint(butter)` }), run({ code: `from scipy.signal import (butter,\n    sosfiltfilt)\nprint(2)` }), run({ code: `${multi}\nprint(3)` })])
  );
  assert(notebook.cells[1].source.includes(multi), 'a parenthesized multi-line import is hoisted whole, with its comment');
  assertEqual(notebook.cells[3].source, 'print(butter)', 'and nothing of it is left behind');
  assertEqual(notebook.cells[5].source, 'print(2)', 'the same names spelled over different lines are a different statement, hoisted too');
  assert(notebook.cells[1].source.includes('from scipy.signal import (butter,\n    sosfiltfilt)'), 'both spellings are in the setup cell');
}
{
  const indented = 'try:\n    import scipy.signal as sig\nexcept ImportError:\n    sig = None';
  const { notebook } = build(conversation([run({ code: `${indented}\nprint(sig)` })]));
  assert(!notebook.cells[1].source.includes('import scipy'), 'an indented import is not hoisted');
  assertEqual(notebook.cells[3].source, `${indented}\nprint(sig)`, 'and stays in its step, untouched');
}
{
  const { notebook } = build(
    conversation([
      run({
        code: [
          'from __future__ import annotations',
          'import a, \\',
          '    b',
          'import os; os.getcwd()',
          'import after_code',
          'x = """',
          'import not_really_an_import',
          '"""',
          'print(x)',
        ].join('\n'),
      }),
    ])
  );
  const setup = notebook.cells[1].source;
  assert(!setup.includes('__future__'), 'from __future__ stays in its step (it must be first in its cell)');
  assert(setup.includes('import a, \\\n    b'), 'a backslash continuation is hoisted whole, after a from __future__ line');
  assert(!setup.includes('os.getcwd'), 'an import sharing a line with other code stays in place');
  assert(!setup.includes('after_code'), 'and an import after that code stays in its step');
  assert(!setup.includes('not_really_an_import'), 'text inside a string is never hoisted');
  assertEqual(
    notebook.cells[3].source,
    ['from __future__ import annotations', 'import os; os.getcwd()', 'import after_code', 'x = """', 'import not_really_an_import', '"""', 'print(x)'].join('\n'),
    'those lines are left as they were'
  );
  const star = build(conversation([run({ code: 'from math import *\nprint(pi)' })]));
  assert(!star.notebook.cells[1].source.includes('import *'), 'a star import binds names nobody can list, so it stays in its step');
}
{
  const docstring = '"""Load the data.\nimport inside_docstring\n"""';
  const { notebook } = build(
    conversation([run({ code: `# a comment first\n\n${docstring}\nimport numpy as np\nx = np.zeros(2)` })])
  );
  assert(notebook.cells[1].source.includes('import numpy as np'), 'imports after blank lines, comments and a module docstring are still the leading block');
  assert(!notebook.cells[1].source.includes('inside_docstring'), 'and the docstring is not scanned for imports');
  assertEqual(notebook.cells[3].source, `# a comment first\n\n${docstring}\nx = np.zeros(2)`, 'the step keeps the comment, the docstring and the code');
}
console.log('\nImports that depend on earlier code, or on each other');
{
  const code = 'import sys\nsys.path.insert(0, "/tmp/x")\nimport mymod\nprint(mymod)';
  const { notebook } = build(conversation([run({ code })]));
  const setup = notebook.cells[1].source;
  assert(setup.includes('import sys') && !setup.includes('import mymod'), 'an import after sys.path.insert is not hoisted above it');
  assertEqual(notebook.cells[3].source, 'sys.path.insert(0, "/tmp/x")\nimport mymod\nprint(mymod)', 'it stays in its step, in order');
}
{
  const code = 'import pathlib\npathlib.Path("helper.py").write_text("VALUE = 1")\nimport helper\nprint(helper.VALUE)';
  const { notebook } = build(conversation([run({ code })]));
  assert(!notebook.cells[1].source.includes('import helper'), 'an import of a module the run just wrote is not hoisted');
}
{
  const { notebook } = build(
    conversation([
      run({ code: 'from a import f\nprint(f)' }),
      run({ code: 'from b import f\nprint(f)' }),
      run({ code: 'from a import f\nprint(f)' }),
      run({ code: 'from b import f\nprint(f)' }),
    ])
  );
  const setup = notebook.cells[1].source;
  assert(setup.includes('from a import f') && !setup.includes('from b import f'), 'two different imports of one name: the first is hoisted, the second is not');
  assertEqual(notebook.cells[3].source, 'print(f)', 'step 1 is left with its code only');
  assertEqual(notebook.cells[5].source, 'from b import f\nprint(f)', 'step 2 keeps the import that rebinds the name');
  assertEqual(notebook.cells[7].source, 'from a import f\nprint(f)', 'step 3 keeps its import too, since step 2 changed what f is');
  assertEqual(notebook.cells[9].source, 'from b import f\nprint(f)', 'and step 4, for the same reason');
}
{
  const { notebook } = build(
    conversation([
      run({ code: 'import numpy as np\nprint(1)' }),
      run({ code: 'import numpy as np\nimport numpy.linalg\nimport pandas as np\nprint(2)' }),
      run({ code: 'import eegprep_lean as e\nimport numpy as eegprep_lean\nprint(3)' }),
    ])
  );
  const setup = notebook.cells[1].source;
  assertEqual(setup.split('import numpy as np').length - 1, 1, 'the same import is still hoisted once');
  assert(setup.includes('import numpy.linalg'), 'import a.b and import a bind the same package, so they do not collide');
  assert(!setup.includes('import pandas as np'), 'a different module under the same alias stays in its step');
  assertEqual(notebook.cells[5].source, 'import pandas as np\nprint(2)', 'with the rest of its step');
  assert(setup.includes('import eegprep_lean as e'), 'an alias of the setup import is another name, so it is hoisted');
  assertEqual(notebook.cells[7].source, 'import numpy as eegprep_lean\nprint(3)', 'a name the setup cell needs is never rebound there');
}
{
  const { notebook } = build(conversation([run({ code: 'import numpy as np\nimport eegprep_lean' }), run({ code: 'print(1)' })]));
  assertEqual(notebook.cells[1].source.split('import eegprep_lean').length - 1, 1, 'a step importing eegprep_lean does not repeat the setup import');
  assertEqual(notebook.cells[3].source, '# The imports for this step are in the setup cell.', 'a step that was only imports keeps a cell that says so');
}

console.log('\nStep numbers and headings');
{
  const { notebook, stats } = build(
    conversation([
      run({ code: 'a = 1', status: 'error', stderr: 'NameError: x' }),
      run({ code: 'b = 1', stepTitle: 'From the plan', description: 'From the run' }),
      run({ code: 'c = 1', status: 'denied' }),
      run({ code: 'd = 1', description: '  Only a description\n  over two lines  ' }),
      run({ code: 'e = 1' }),
    ])
  );
  const headings = notebook.cells.filter((c) => c.id.endsWith('-title') && c.id !== 'title').map((c) => c.source);
  assertEqual(
    headings,
    ['## Step 1 · From the plan', '## Step 2 · Only a description over two lines', '## Step 3'],
    'a heading uses stepTitle, else description, else just the step number; failed runs do not take a number'
  );
  assertEqual(stats.steps, 3, 'steps counts the included runs');
}
{
  const { notebook } = build(conversation([run({ code: 'x = 1', local: true, description: 'Reader rerun' }), run({ code: 'y = 1', description: 'Assistant' })]));
  assertEqual(
    notebook.cells[2].source,
    '## Step 1 · Reader rerun\n\n(run by the reader, not the assistant)',
    'a run the reader started says so under its heading, as the zip export does'
  );
  assertEqual(notebook.cells[4].source, '## Step 2 · Assistant', 'an assistant run carries no marker');
}

console.log('\nText from the chat in markdown');
{
  const nasty = 'a|b *x* [l](http://e.com) <b>bold</b> `code` #1 snake_case $x$ www.e.com';
  const { notebook } = build(
    conversation([run({ code: 'a = 1', description: nasty }), run({ code: 'b', status: 'error', description: nasty, stderr: 'boom' })], { title: nasty })
  );
  const escaped = 'a\\|b \\*x\\* \\[l\\](http\\://e.com) \\<b\\>bold\\</b\\> \\`code\\` \\#1 snake\\_case \\$x\\$ www\\.e.com';
  assertEqual(notebook.cells[2].source, `## Step 1 · ${escaped}`, 'a step title with emphasis, a link and a tag is escaped so none of them renders');
  assert(notebook.cells[0].source.startsWith(`# ${escaped}\n`), 'so is the notebook title');
  assert(notebook.cells.at(-1).source.includes(`- Run 2 · ${escaped} (error)`), 'and a title in the closing note');
  const plain = build(conversation([run({ code: 'a = 1', description: 'Plain words, 30 Hz low-pass (zero-phase).' })]));
  assertEqual(plain.notebook.cells[2].source, '## Step 1 · Plain words, 30 Hz low-pass (zero-phase).', 'ordinary words come through unchanged');
}

console.log('\nThe closing note');
{
  const input = conversation([
    run({ code: 'a = 1', description: 'Good one' }),
    run({ code: 'b', status: 'error', description: 'Bad one', stderr: "KeyError: 'Scrambled'\n  line 4: erp[name]" }),
    run({ code: 'c', status: 'denied', description: 'Not allowed' }),
    run({ code: 'd', status: 'timeout' }),
    run({ code: 'e', status: 'cancelled', stderr: 'ignored: only errors repeat stderr' }),
    run({ code: 'f', status: 'oom' }),
    run({ code: 'g', status: 'error', stderr: '' }),
  ]);
  const { notebook, stats } = build(input);
  const note = notebook.cells.at(-1);
  assertEqual(note.id, 'left-out', 'the note is the last cell');
  assertEqual(note.cell_type, 'markdown', 'a markdown cell');
  assert(note.source.startsWith('## Left out\n'), 'titled Left out');
  const items = lines(note.source).filter((line) => line.startsWith('- '));
  assertEqual(
    items,
    [
      "- Run 2 · Bad one (error): `KeyError: 'Scrambled'`",
      '- Run 3 · Not allowed (denied)',
      '- Run 4 (timeout)',
      '- Run 5 (cancelled)',
      '- Run 6 (oom)',
      '- Run 7 (error)',
    ],
    'each left-out run is listed with its position, its title when it has one, and status; an error adds its first stderr line'
  );
  assertEqual(stats.steps, 1, 'one run went in');
  assertEqual(stats.leftOut.map((e) => e.status), ['error', 'denied', 'timeout', 'cancelled', 'oom', 'error'], 'stats.leftOut carries the statuses');
  assertEqual(stats.leftOut[0].error, "KeyError: 'Scrambled'", 'and the first error line');
  const codeSources = notebook.cells.filter((c) => c.cell_type === 'code').map((c) => c.source);
  assert(!codeSources.some((s) => ['b', 'c', 'd', 'e', 'f', 'g'].includes(s)), 'no left-out code appears in a code cell');
}
{
  const { notebook } = build(conversation([run({ code: 'x = 1' })]));
  assertEqual(notebook.cells.at(-1).id, 'step-1-code', 'no closing note when nothing was left out');
  assert(!notebook.cells.some((c) => c.source.includes('Left out')), 'and no mention of one');
  const weird = build(conversation([run({ code: 'a', status: 'error', stderr: 'Error with `backticks` and *stars*' })]));
  assert(weird.notebook.cells.at(-1).source.includes('``Error with `backticks` and *stars*``'), 'an error line is a code span that its own backticks cannot break');
  const long = build(conversation([run({ code: 'a', status: 'error', stderr: `Error: ${'x'.repeat(500)}` })]));
  assert(long.stats.leftOut[0].error.length <= 200, 'a very long error line is cut');
  const empty = build(conversation([run({ code: '   \n', description: 'Nothing' })]));
  assertEqual(empty.stats.steps, 0, 'an ok run with no code is not a step');
  assertEqual(empty.stats.leftOut.map((e) => e.status), ['empty'], 'and is named in the note, not silently dropped');
}

console.log('\nZero successful runs');
for (const name of ['empty', 'onlyFailures']) {
  const { notebook, script, stats } = build(NOTEBOOK_CONVERSATIONS[name]());
  assertEqual(stats.steps, 0, `${name}: stats.steps is 0`);
  const expected = name === 'empty' ? ['markdown', 'code'] : ['markdown', 'code', 'markdown'];
  assertEqual(kinds(notebook), expected, `${name}: still a title and a setup cell${name === 'empty' ? '' : ', and the note'}`);
  assert(script.includes('# %%\n# %pip install eegprep-lean scipy'), `${name}: the script still has its setup cell`);
}

console.log('\nIds');
{
  for (const name of Object.keys(NOTEBOOK_CONVERSATIONS)) {
    const first = build(NOTEBOOK_CONVERSATIONS[name]());
    const second = build(NOTEBOOK_CONVERSATIONS[name]());
    const ids = first.notebook.cells.map((c) => c.id);
    assertEqual(ids, second.notebook.cells.map((c) => c.id), `${name}: the same chat gives the same ids every time`);
    assertEqual(new Set(ids).size, ids.length, `${name}: ids are unique`);
    assert(ids.every((id) => /^[a-zA-Z0-9_-]{1,64}$/.test(id)), `${name}: ids are valid nbformat 4.5 ids`);
    assertEqual(first.script, second.script, `${name}: the script is deterministic too`);
  }
  assertEqual(
    build(NOTEBOOK_CONVERSATIONS.erp()).notebook.cells.map((c) => c.id),
    ['title', 'setup', 'step-1-title', 'step-1-code', 'step-2-title', 'step-2-code', 'step-3-title', 'step-3-code', 'step-4-title', 'step-4-code', 'step-5-title', 'step-5-code', 'left-out'],
    'ids name the cell and its step'
  );
}

console.log('\nThe input is not changed');
{
  const input = NOTEBOOK_CONVERSATIONS.erp();
  const snapshot = JSON.stringify(input);
  build(input);
  assertEqual(JSON.stringify(input), snapshot, 'building leaves the run records as they were');
  const odd = build({ datasetId: 'on1', executions: [null, 'x', 5, {}, { status: 'ok', code: 'a = 1' }] });
  assertEqual(odd.stats.steps, 1, 'non-object records are left out instead of throwing');
  assertEqual(build({ datasetId: 'on1' }).stats.steps, 0, 'missing executions is an empty chat');
  const crlf = build(conversation([run({ code: 'a = 1\r\nb = 2\r\n' })]));
  assertEqual(crlf.notebook.cells[3].source, 'a = 1\nb = 2', 'Windows line endings are normalized and the code is trimmed');
}

console.log('\nThe script');
{
  const { notebook, script } = build(NOTEBOOK_CONVERSATIONS.erp());
  assert(script.endsWith('\n') && !script.endsWith('\n\n'), 'the file ends with one newline');
  const scriptLines = lines(script.trimEnd());
  assertEqual(scriptLines[0], '# ---', 'it opens with a Jupytext header, which carries the header comment');
  const headerEnd = scriptLines.indexOf('# ---', 1);
  const header = scriptLines.slice(0, headerEnd + 1).join('\n');
  assert(header.includes('does not run under plain python') && header.includes('VS Code'), 'the header says the script is for cell runners, not plain python');
  assert(scriptLines.slice(0, headerEnd + 1).every((l) => l.startsWith('#')), 'every header line is a comment');
  const markers = scriptLines.filter((l) => /^# %%( \[markdown\])?$/.test(l));
  assertEqual(markers.length, notebook.cells.length, 'one cell marker per notebook cell');
  assertEqual(
    markers.map((l) => (l === '# %%' ? 'code' : 'markdown')),
    kinds(notebook),
    'in the same order and of the same kinds'
  );
  assert(script.includes('# %%\n# %pip install eegprep-lean scipy\n# %matplotlib inline\n'), 'magics are written as Jupytext does');
  assert(script.includes('# %% [markdown]\n# # Famous vs scrambled ERPs in on002718\n#\n# From a chat with NEMAR Assistant'), 'markdown lines are prefixed with "# " and blank ones with "#"');
  assert(script.includes('# # %% this comment would split a cell'), 'a code line that begins "# %%" is neutralized so it cannot split a cell');
  assert(!scriptLines.some((l) => /^\s*#\s*%%/.test(l) && !/^# %%( \[markdown\])?$/.test(l)), 'no line other than a real marker looks like one');
  assert(script.includes('# # peak?'), 'a comment Jupytext would read as a help magic is escaped, as it does');
  assert(script.includes('await eegprep_lean.read_index('), 'top-level await is expected');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
