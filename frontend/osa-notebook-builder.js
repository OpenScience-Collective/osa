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
 * Imports are hoisted only from the leading block of a run: column-0 imports
 * (`import x`, `from x import y`, including parenthesized multi-line forms and
 * `as` aliases) that come before any other statement, after at most blank
 * lines, comments and a module docstring. An import after other code stays in
 * its step (`sys.path.insert(...)` may be what makes it work); so does an
 * indented import (inside `try:` or a function), a star import, a statement
 * that shares its line with other code, and `from __future__ import ...`,
 * which must stay the first statement of its cell. An import that binds a name
 * another import already binds to something else (`from a import f`, then
 * `from b import f`) also stays in its step, so each step sees the object it
 * saw in the chat. Not covered: a name a step assigns in plain code, which a
 * hoisted import of the same name would not know about.
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
 * The names a clean import statement binds, each with what it resolves to, or
 * null when the statement cannot be read with certainty (a star import, an
 * odd form). `import a.b` and `import a` both bind `a` to the package `a`, so
 * they do not collide; `from a import f` and `from b import f` bind `f` to two
 * different things, so they do.
 *
 * @param {string} code The statement's text with comments removed.
 * @returns {Array<[string, string]>|null} `[name, target]` pairs.
 */
function importBindings(code) {
  const text = code.replace(/\\/g, ' ').replace(/\s+/g, ' ').trim();
  const plain = /^import (.+)$/.exec(text);
  if (plain) {
    const bindings = [];
    for (const part of plain[1].split(',')) {
      const match = /^\s*([A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)(?:\s+as\s+([A-Za-z_]\w*))?\s*$/.exec(part);
      if (!match) return null;
      const module = match[1].replace(/\s+/g, '');
      const top = module.split('.')[0];
      bindings.push(match[2] ? [match[2], `module ${module}`] : [top, `module ${top}`]);
    }
    return bindings;
  }
  const from = /^from (\S+) import (.+)$/.exec(text);
  if (!from) return null;
  let names = from[2].trim();
  if (names.startsWith('(')) {
    if (!names.endsWith(')')) return null;
    names = names.slice(1, -1);
  }
  const parts = names.split(',').map((part) => part.trim());
  if (parts.at(-1) === '') parts.pop();
  if (parts.length === 0) return null;
  const bindings = [];
  for (const part of parts) {
    const match = /^([A-Za-z_]\w*)(?:\s+as\s+([A-Za-z_]\w*))?$/.exec(part);
    if (!match) return null;
    bindings.push([match[2] || match[1], `${from[1]} ${match[1]}`]);
  }
  return bindings;
}

/** A module docstring opens with a triple quote, optionally prefixed. */
const DOCSTRING_START = /^[rRuU]?("""|''')/;

/** What the setup cell and the kept imports bind, shared by every run of one notebook. */
function newImportScope() {
  return {
    seen: new Set([SETUP_IMPORT]),
    // name -> target, for the imports hoisted into the setup cell
    owners: new Map([['eegprep_lean', 'module eegprep_lean']]),
    // name -> target, for the imports left in a step; the latest one wins, as it does at run time
    rebound: new Map(),
  };
}

/**
 * Split a run's code into the imports to hoist into the setup cell and the
 * remaining lines.
 *
 * Only the leading block is hoisted: column-0 imports that come before any
 * other statement (blank lines, comments and a module docstring may precede
 * them). An import after other code stays in its step, because that code may
 * be what makes it work (`sys.path.insert(...)` before `import mymod`). A
 * statement also stays when it shares its line with other code (`;`), is a
 * star import, starts inside a string, or cannot be read as a plain import;
 * `from __future__` imports stay too, and do not end the leading block.
 *
 * An import is also left in its step when it binds a name that setup or an
 * earlier step already binds to something else (`from a import f`, then
 * `from b import f`), since hoisting it would hand an earlier step the wrong
 * object. An import identical to one already in setup is dropped.
 *
 * @param {string[]} lines
 * @param {{seen: Set<string>, owners: Map<string, string>, rebound: Map<string, string>}} scope
 *   Updated in place so that later runs of the same notebook see this run's imports.
 * @returns {{imports: Array<{key: string, text: string}>, rest: string[]}}
 */
function extractImports(lines, scope) {
  const imports = [];
  const rest = [];
  const quotes = new QuoteTracker();
  let leading = true;
  let docstringSeen = false;
  let i = 0;
  const passThrough = (line) => {
    quotes.readLine(line);
    rest.push(line);
    i += 1;
  };
  while (i < lines.length) {
    const line = lines[i];
    if (quotes.isQuoted()) {
      passThrough(line);
      continue;
    }
    if (leading && (line.trim() === '' || line.trimStart().startsWith('#'))) {
      passThrough(line);
      continue;
    }
    if (leading && !docstringSeen && DOCSTRING_START.test(line)) {
      // A module docstring, however many lines; the leading block goes on after it.
      docstringSeen = true;
      passThrough(line);
      while (quotes.isQuoted() && i < lines.length) passThrough(lines[i]);
      continue;
    }
    if (!IMPORT_START.test(line)) {
      leading = false;
      passThrough(line);
      continue;
    }
    if (FUTURE_IMPORT.test(line)) {
      // Runs nothing and must stay first in its cell, so the leading block goes on past it.
      passThrough(line);
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
    i = j;
    const code = statement.map(stripComment).join(' ');
    const bindings = balance !== 0 || continued || code.includes(';') ? null : importBindings(code);
    const collides =
      bindings !== null &&
      bindings.some(
        ([name, target]) =>
          (scope.owners.has(name) && scope.owners.get(name) !== target) ||
          (scope.rebound.has(name) && scope.rebound.get(name) !== target)
      );
    if (!leading || bindings === null || collides) {
      // Left exactly where it is. Code that is not a clean import ends the leading block.
      if (bindings === null) leading = false;
      for (const kept of statement) {
        quotes.readLine(kept);
        rest.push(kept);
      }
      for (const [name, target] of bindings || []) scope.rebound.set(name, target);
      continue;
    }
    for (const [name, target] of bindings) scope.owners.set(name, target);
    const key = oneLine(code);
    if (scope.seen.has(key)) continue;
    scope.seen.add(key);
    imports.push({ key, text: statement.join('\n') });
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

/**
 * Text from the chat (a run's description or step title, the chat's title) made
 * safe to put in a markdown heading or list item: markdown and HTML
 * metacharacters are backslash-escaped, so it shows as written and cannot make
 * a link, emphasis or a tag. A notebook's markdown cells render HTML and links.
 */
function escapeMarkdown(text) {
  return text.replace(/[\\`*_[\]<>|#&~]/g, '\\$&').replace(/:\/\//g, '\\://');
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
  const scope = newImportScope();
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
    const { imports, rest } = extractImports(code.split('\n'), scope);
    hoisted.push(...imports);
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
  const origin = `From a chat with ${community}${date ? ` on ${date}` : ''}. Data: NEMAR's streaming copy, quantized and lossy.`;

  const cells = [
    { type: 'markdown', id: 'title', source: `# ${escapeMarkdown(notebookTitle)}\n\n${escapeMarkdown(origin)}` },
    { type: 'code', id: 'setup', source: setupSource({ datasetId, zarrBase, imports: hoisted }) },
  ];
  for (const step of included) {
    const heading = step.named ? `## Step ${step.step} · ${escapeMarkdown(step.title)}` : `## Step ${step.step}`;
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
      return `- Run ${entry.position} · ${escapeMarkdown(entry.title)} (${entry.status})${reason}`;
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
