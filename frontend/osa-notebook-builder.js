/**
 * Turn a chat's successful runs into a notebook and a script (E4.1, #667).
 *
 * `buildConversationNotebook` is pure: it takes the widget's stored run
 * records and returns an nbformat 4.5 notebook, a percent-format script with
 * the same cells, and a count of what went in and what was left out. It does
 * not touch storage, the DOM or the network, so it runs under Bun in
 * `frontend/test-notebook-builder.js` and is validated by the real `nbformat`
 * and `jupytext` packages in `tests/test_frontend/test_notebook_builder.py`.
 * Nothing here opens a notebook or offers a download: those are E4.2 to E4.4.
 *
 * The cells, in order:
 *   1. a markdown title and a line saying where the notebook came from;
 *   2. one setup code cell: `%pip install`, `%matplotlib inline`, the imports
 *      hoisted out of the steps (once each, first-seen order), `import
 *      eegprep_lean`, and `read_index`;
 *   3. for each run that succeeded and has code: a `## Step N · title`
 *      markdown cell and a code cell with the run's code;
 *   4. when a run was left out (error, denied, timeout, cancelled, oom): a
 *      closing `Left out` markdown list.
 *
 * Cells carry NO outputs, on purpose. A stored run loses its figures when it
 * is saved (the widget drops images from storage), so a notebook that carried
 * the stored stdout but not the figures would show partial outputs that
 * mislead; Run all regenerates every output.
 *
 * Imports are hoisted only from column 0 (`import x`, `from x import y`,
 * including parenthesized multi-line forms and `as` aliases); an indented
 * import (inside `try:` or a function) stays where it is, and so does
 * `from __future__ import ...`, which must stay the first statement of its cell.
 *
 * The script targets cell runners (VS Code, Spyder, Jupytext), not plain
 * `python`: it uses top-level `await`, which only a notebook kernel accepts.
 * Its text follows what Jupytext writes and reads for the percent format, so
 * `jupytext --to notebook` restores the same cells. Two places need care, both
 * handled in the cell sources themselves so the notebook and the script agree:
 *   - a line that Jupytext would read as a cell marker (`# %%` at any indent)
 *     becomes `# # %%`, still a comment, since a marker inside code would split
 *     the cell;
 *   - a line that Jupytext reads as a magic (`%pip`, `!ls`, and a comment such
 *     as `# %pip` or `# what?`) is written with a `# ` prefix, as Jupytext
 *     does, which is how the setup cell's magics become `# %pip ...`.
 *
 * Ids are deterministic (`title`, `setup`, `step-1-title`, `step-1-code`, ...,
 * `left-out`) and unique within a notebook, so the same chat always produces
 * the same file; this is why `cellId` in osa-workspace.js, whose global
 * counter makes ids unstable, is not reused.
 */

/** The marker the zip export's notebook uses for a run the reader started. */
const READER_RUN_NOTE = '(run by the reader, not the assistant)';

/** Longest first line of an error that the closing note repeats. */
const MAX_ERROR_LINE = 200;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const NOTEBOOK_METADATA = Object.freeze({
  kernelspec: { display_name: 'Python (Pyodide)', language: 'python', name: 'python' },
  language_info: {
    codemirror_mode: { name: 'python', version: 3 },
    file_extension: '.py',
    mimetype: 'text/x-python',
    name: 'python',
    nbconvert_exporter: 'python',
    pygments_lexer: 'ipython3',
    version: '3.13',
  },
});

/** The setup cell's own imports, so a step that repeats one does not add it twice. */
const SETUP_IMPORT = 'import eegprep_lean';

function oneLine(value) {
  return String(value == null ? '' : value)
    .replace(/\s+/g, ' ')
    .trim();
}

function normalizeNewlines(text) {
  return String(text == null ? '' : text).replace(/\r\n?/g, '\n');
}

function trimBlankEdges(lines) {
  let start = 0;
  let end = lines.length;
  while (start < end && lines[start].trim() === '') start += 1;
  while (end > start && lines[end - 1].trim() === '') end -= 1;
  return lines.slice(start, end);
}

/** "Nov 10, 2026" in UTC (so the output does not depend on the reader's zone), or '' if unusable. */
function formatUtcDate(createdAt) {
  const date = createdAt instanceof Date ? createdAt : new Date(createdAt);
  if (createdAt == null || Number.isNaN(date.getTime())) return '';
  return `${MONTHS[date.getUTCMonth()]} ${date.getUTCDate()}, ${date.getUTCFullYear()}`;
}

// ---------------------------------------------------------------------------
// Python source scanning: just enough to know whether a line starts inside a
// string. A port of the line-level behavior of jupytext's StringParser.
// ---------------------------------------------------------------------------

class QuoteTracker {
  constructor() {
    this.single = null;
    this.triple = null;
    this.tripleStart = -1;
  }

  isQuoted() {
    return Boolean(this.single || this.triple);
  }

  readLine(line) {
    if (!this.isQuoted() && line.trimStart().startsWith('#')) return;
    this.tripleStart = -1;
    for (let i = 0; i < line.length; i += 1) {
      const char = line[i];
      if (this.single === null && this.triple === null && char === '#') break;
      if (char !== '"' && char !== "'") continue;
      if (i > 0 && line[i - 1] === '\\') continue;
      if (this.single === char) {
        this.single = null;
        continue;
      }
      if (this.single !== null) continue;
      if (i >= 2 && line.slice(i - 2, i + 1) === char.repeat(3) && i >= this.tripleStart + 3) {
        if (this.triple === char) {
          this.triple = null;
          this.tripleStart = i;
          continue;
        }
        if (this.triple !== null) continue;
        this.triple = char;
        this.tripleStart = i;
        continue;
      }
      if (this.triple !== null) continue;
      this.single = char;
    }
    this.single = null;
  }
}

// ---------------------------------------------------------------------------
// Import hoisting
// ---------------------------------------------------------------------------

const IMPORT_START = /^(?:import\s+\S|from\s+\S+\s+import\b)/;
const FUTURE_IMPORT = /^from\s+__future__\s+import\b/;

function stripComment(line) {
  // Safe for an import statement: no string literal can hold a '#'.
  const at = line.indexOf('#');
  return at === -1 ? line : line.slice(0, at);
}

function bracketBalance(text) {
  let balance = 0;
  for (const char of text) {
    if (char === '(') balance += 1;
    else if (char === ')') balance -= 1;
  }
  return balance;
}

/**
 * Split a run's code into the column-0 import statements to hoist and the
 * remaining lines. A statement is skipped (left in place) when it shares its
 * line with other code (`;`), is a `from __future__` import, or starts inside
 * a string.
 *
 * @returns {{imports: Array<{key: string, text: string}>, rest: string[]}}
 */
function extractImports(lines) {
  const imports = [];
  const rest = [];
  const quotes = new QuoteTracker();
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (quotes.isQuoted() || !IMPORT_START.test(line) || FUTURE_IMPORT.test(line)) {
      quotes.readLine(line);
      rest.push(line);
      i += 1;
      continue;
    }
    const statement = [line];
    let balance = bracketBalance(stripComment(line));
    let continued = /\\\s*$/.test(stripComment(line));
    let j = i + 1;
    while ((balance > 0 || continued) && j < lines.length) {
      const next = lines[j];
      statement.push(next);
      balance += bracketBalance(stripComment(next));
      continued = /\\\s*$/.test(stripComment(next));
      j += 1;
    }
    const code = statement.map(stripComment).join(' ');
    if (balance !== 0 || continued || code.includes(';')) {
      // Not a clean, self-contained import: leave it exactly where it is.
      for (const kept of statement) {
        quotes.readLine(kept);
        rest.push(kept);
      }
      i = j;
      continue;
    }
    imports.push({ key: oneLine(code), text: statement.join('\n') });
    i = j;
  }
  return { imports, rest };
}

// ---------------------------------------------------------------------------
// Cell sources
// ---------------------------------------------------------------------------

// What Jupytext's percent reader takes for a cell marker, at any indent.
const MARKER_LIKE = /^(\s*)(#\s*%%)/;

function neutralizeMarkers(lines) {
  return lines.map((line) => line.replace(MARKER_LIKE, '$1# $2'));
}

/** Python code in a step cell, minus hoisted imports, with no marker-like lines. */
function stepSource(lines) {
  return trimBlankEdges(neutralizeMarkers(lines)).join('\n');
}

function codeSpan(text) {
  const longest = Math.max(0, ...(text.match(/`+/g) || []).map((run) => run.length));
  const fence = '`'.repeat(longest + 1);
  const pad = text.startsWith('`') || text.endsWith('`') ? ' ' : '';
  return `${fence}${pad}${text}${pad}${fence}`;
}

function setupSource({ datasetId, zarrBase, imports }) {
  const lines = ['%pip install eegprep-lean scipy', '%matplotlib inline'];
  for (const item of imports) lines.push(...neutralizeMarkers(item.text.split('\n')));
  lines.push(SETUP_IMPORT, '');
  const dataset = JSON.stringify(datasetId);
  if (zarrBase) {
    const indexUrl = JSON.stringify(`${zarrBase.replace(/\/+$/, '')}/${datasetId}/zarr/index.json`);
    lines.push('index = await eegprep_lean.read_index(', `    ${dataset}, index_url=${indexUrl}`, ')');
  } else {
    lines.push(`index = await eegprep_lean.read_index(${dataset})`);
  }
  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// The planner: one list of cells, from which both outputs are rendered
// ---------------------------------------------------------------------------

function firstErrorLine(stderr) {
  const line = normalizeNewlines(stderr)
    .split('\n')
    .find((candidate) => candidate.trim() !== '');
  if (!line) return '';
  const clean = line.trim();
  return clean.length > MAX_ERROR_LINE ? `${clean.slice(0, MAX_ERROR_LINE - 1)}…` : clean;
}

function planCells({ datasetId, title, createdAt, communityName, zarrBase, executions }) {
  const included = [];
  const leftOut = [];
  const seenImports = new Set([SETUP_IMPORT]);
  const hoisted = [];

  (Array.isArray(executions) ? executions : []).forEach((run, index) => {
    const position = index + 1;
    const record = run && typeof run === 'object' ? run : {};
    const status = typeof record.status === 'string' && record.status ? record.status : 'unknown';
    const code = normalizeNewlines(record.code);
    const named = oneLine(record.stepTitle) || oneLine(record.description);
    if (status !== 'ok' || code.trim() === '') {
      const entry = { position, title: named || `Run ${position}`, status: status === 'ok' ? 'empty' : status };
      if (status === 'error') {
        const reason = firstErrorLine(record.stderr);
        if (reason) entry.error = reason;
      }
      leftOut.push(entry);
      return;
    }
    const { imports, rest } = extractImports(code.split('\n'));
    for (const item of imports) {
      if (seenImports.has(item.key)) continue;
      seenImports.add(item.key);
      hoisted.push(item);
    }
    const step = included.length + 1;
    included.push({
      step,
      title: named || `Step ${step}`,
      named: Boolean(named),
      local: record.local === true,
      lines: rest,
    });
  });

  const notebookTitle = oneLine(title) || `Notebook for ${datasetId}`;
  const community = oneLine(communityName) || 'OSA';
  const date = formatUtcDate(createdAt);
  const origin = `From a ${community} chat${date ? ` on ${date}` : ''}. Data: NEMAR's streaming copy, quantized and lossy.`;

  const cells = [
    { type: 'markdown', id: 'title', source: `# ${notebookTitle}\n\n${origin}` },
    { type: 'code', id: 'setup', source: setupSource({ datasetId, zarrBase, imports: hoisted }) },
  ];
  for (const step of included) {
    const heading = step.named ? `## Step ${step.step} · ${step.title}` : `## Step ${step.step}`;
    cells.push({
      type: 'markdown',
      id: `step-${step.step}-title`,
      source: step.local ? `${heading}\n\n${READER_RUN_NOTE}` : heading,
    });
    const source = stepSource(step.lines) || '# The imports for this step are in the setup cell.';
    cells.push({ type: 'code', id: `step-${step.step}-code`, source });
  }
  if (leftOut.length > 0) {
    const items = leftOut.map((entry) => {
      const reason = entry.error ? `: ${codeSpan(entry.error)}` : '';
      return `- Run ${entry.position} · ${entry.title} (${entry.status})${reason}`;
    });
    cells.push({
      type: 'markdown',
      id: 'left-out',
      source: [
        '## Left out',
        '',
        'These runs did not succeed, so their code is not in this notebook. Positions count every run in the chat.',
        '',
        ...items,
      ].join('\n'),
    });
  }
  return { cells, stats: { steps: included.length, leftOut }, title: notebookTitle, origin };
}

// ---------------------------------------------------------------------------
// The notebook
// ---------------------------------------------------------------------------

function toNotebook(cells) {
  return {
    nbformat: 4,
    nbformat_minor: 5,
    metadata: structuredClone(NOTEBOOK_METADATA),
    cells: cells.map((cell) =>
      cell.type === 'markdown'
        ? { cell_type: 'markdown', id: cell.id, metadata: {}, source: cell.source }
        : { cell_type: 'code', id: cell.id, metadata: {}, execution_count: null, source: cell.source, outputs: [] }
    ),
  };
}

// ---------------------------------------------------------------------------
// The percent-format script
//
// The magic rules below are the ones Jupytext 1.19 applies to a Python code
// cell with an explicit `# %%` marker (jupytext/magics.py). A line they match
// is written with a `# ` prefix and read back without it, so a source line
// that matches one comes back unchanged only if it is written the same way.
// ---------------------------------------------------------------------------

const MAGIC_START = /^\s*(# |#)*(%|%%|%%%)[a-zA-Z]/;
const MAGIC_FORCE_ESCAPE = /^\s*(# |#)*(%|%%|%%%)[a-zA-Z](.*)#\s*escape/;
const MAGIC_NO_ESCAPE = /^\s*(# |#)*(%|%%|%%%)[a-zA-Z](.*)#\s*noescape/;
const HELP_OR_SHELL = /^\s*(?:(?:# |#)+\s*)?(\?|!)\s*[A-Za-z.~$\\/{}]/;
const SHELL_COMMAND = /^(# |#)*(cat|cd|cp|mv|rm|rmdir|copy|ddir|echo|ls|ldir|mkdir|ren)($|\s$|\s[^=,])/;
const HELP_SUFFIX = /^\s*(# )*[^\s]*\?\s*$/;
const MAGIC_ASSIGN = /^(# |#)*\s*([a-zA-Z_][a-zA-Z_$0-9]*)\s*=\s*(%|%%|%%%|!)[a-zA-Z](.*)/;
const LINE_CONTINUATION = /.*\\\s*$/;

function isMagicLine(line) {
  if (MAGIC_FORCE_ESCAPE.test(line)) return true;
  if (MAGIC_NO_ESCAPE.test(line)) return false;
  return (
    MAGIC_START.test(line) ||
    HELP_OR_SHELL.test(line) ||
    MAGIC_ASSIGN.test(line) ||
    HELP_SUFFIX.test(line) ||
    SHELL_COMMAND.test(line)
  );
}

/** What Jupytext writes for a code cell's lines: magics commented, outside strings. */
function commentMagics(lines) {
  const quotes = new QuoteTracker();
  let nextIsMagic = false;
  return lines.map((line) => {
    let written = line;
    if (!quotes.isQuoted() && (nextIsMagic || isMagicLine(line))) {
      if (nextIsMagic) {
        written = `# ${line}`;
      } else {
        const unindented = line.trimStart();
        written = `${line.slice(0, line.length - unindented.length)}# ${unindented}`;
      }
      nextIsMagic = LINE_CONTINUATION.test(line);
    }
    quotes.readLine(line);
    return written;
  });
}

function markdownLines(source) {
  return source.split('\n').map((line) => (line === '' ? '#' : `# ${line}`));
}

function toScript(cells, { title, origin }) {
  const out = [
    '# ---',
    '# jupyter:',
    `#   # ${title}`,
    `#   # ${origin}`,
    '#   # Percent-format script: each "# %%" starts a cell. Open it in a cell runner (VS Code,',
    '#   # Spyder, or Jupytext, which turns it back into a notebook). It uses top-level await, so it',
    '#   # does not run under plain python. Lines starting "# %pip" are notebook magics.',
    '#   kernelspec:',
    '#     display_name: Python (Pyodide)',
    '#     language: python',
    '#     name: python',
    '# ---',
  ];
  for (const cell of cells) {
    out.push('');
    if (cell.type === 'markdown') {
      out.push('# %% [markdown]', ...markdownLines(cell.source));
    } else {
      out.push('# %%', ...commentMagics(cell.source.split('\n')));
    }
  }
  return `${out.join('\n')}\n`;
}

/**
 * Build the notebook and the script for one conversation.
 *
 * @param {object} input
 * @param {string} input.datasetId Dataset the chat was about, e.g. `on002718`.
 * @param {string} input.title Notebook title (the chat's title).
 * @param {string|number|Date} input.createdAt When the chat started; shown as a UTC date.
 * @param {string} input.communityName Name of the assistant, e.g. `NEMAR Assistant`.
 * @param {string} [input.zarrBase] Base URL of the Zarr copies; adds `index_url` to `read_index`.
 * @param {Array<object>} input.executions The widget's stored run records in message
 *   order: `{callId, tool, description, stepTitle?, code, status, stdout, stderr,
 *   traceback, local, images}`. `stepTitle`, when present, names the step before
 *   `description` does.
 * @returns {{notebook: object, script: string,
 *   stats: {steps: number, leftOut: Array<{position: number, title: string, status: string, error?: string}>}}}
 */
export function buildConversationNotebook(input) {
  const { datasetId, title, createdAt, communityName, zarrBase, executions } = input || {};
  if (typeof datasetId !== 'string' || datasetId.trim() === '') {
    throw new TypeError('buildConversationNotebook needs a datasetId');
  }
  const plan = planCells({
    datasetId: datasetId.trim(),
    title,
    createdAt,
    communityName,
    zarrBase: typeof zarrBase === 'string' ? zarrBase.trim() : '',
    executions,
  });
  return {
    notebook: toNotebook(plan.cells),
    script: toScript(plan.cells, plan),
    stats: plan.stats,
  };
}
