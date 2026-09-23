/**
 * Python syntax highlighting for model-written code shown to the person: in the
 * permission gate, and in the record of each run on a reply (epic #429, phase
 * #431). It is the one path that code takes into the page.
 *
 * WHY THIS EXISTS AND IS THIS SMALL
 *
 * The code is model-written, so it is untrusted text that ends up in innerHTML. A
 * highlighting library would be a second dependency to pin and review for a
 * job whose safety property is one line: every character of the code is
 * escaped, and the only markup is ours. So the code is split into tokens FIRST,
 * on the raw text, and each token is escaped on its own and then wrapped. Never
 * the reverse: highlighting escaped text means matching patterns against
 * `&lt;` and `&quot;`, and a pattern that splits an entity breaks the escaping.
 *
 * It is a scanner rather than one large regular expression so that its running
 * time is linear by construction. The input is as long as the model makes it,
 * and a backtracking pattern over an unterminated string is the classic way to
 * hang a page on a hostile input.
 *
 * Highlighting is cosmetic. Where Python's grammar is subtle (nested f-string
 * expressions, soft keywords) the scanner is simply less colorful, never
 * lossy: stripping the markup and unescaping always gives back the input
 * exactly, and the test suite checks that on generated input.
 */

const KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue',
  'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in',
  'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
]);

// The names a reader of analysis code most often meets. Not the full builtins
// module: a longer list colors more of the page without telling anyone more.
const BUILTINS = new Set([
  'abs', 'all', 'any', 'bool', 'bytes', 'dict', 'display', 'enumerate', 'filter', 'float',
  'format', 'getattr', 'hasattr', 'int', 'isinstance', 'len', 'list', 'map', 'max', 'min',
  'next', 'object', 'open', 'print', 'range', 'repr', 'reversed', 'round', 'set', 'setattr',
  'sorted', 'str', 'sum', 'super', 'tuple', 'type', 'zip',
]);

/** The CSS classes the output may contain, each prefixed `osa-py-`. */
export const HIGHLIGHT_CLASSES = Object.freeze(['kw', 'bi', 'str', 'com', 'num', 'dec']);

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/**
 * Escape text for an HTML text node or a quoted attribute.
 *
 * @param {string} text
 * @returns {string}
 */
export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
}

const STRING_START = /[rRbBuUfF]{0,2}(?:'''|"""|'|")/y;
const NUMBER = /0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?[jJ]?/y;
const IDENTIFIER = /[\p{L}\p{Nl}_][\p{L}\p{Nl}\p{Mn}\p{Mc}\p{Nd}\p{Pc}]*/uy;
const SPACE = /[ \t\f\r]+/y;

function matchAt(pattern, text, at) {
  pattern.lastIndex = at;
  const found = pattern.exec(text);
  return found === null ? '' : found[0];
}

/**
 * Where a string literal that opens at `at` ends.
 *
 * A single-quoted literal ends at its closing quote or, unterminated, at the
 * end of the line; a triple-quoted one at its closing triple or the end of the
 * text. A backslash always consumes the next character, which is what keeps
 * `'it\'s'` one literal.
 *
 * @returns {number} The index just past the literal.
 */
function stringEnd(text, at, quote) {
  let i = at;
  const triple = quote.length === 3;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '\\') {
      i += 2;
      continue;
    }
    if (!triple && ch === '\n') return i;
    if (text.startsWith(quote, i)) return i + quote.length;
    i++;
  }
  return text.length;
}

/**
 * Split Python source into classified tokens.
 *
 * @param {string} code
 * @returns {Array<{text: string, kind: string|null}>} `kind` is one of
 *   HIGHLIGHT_CLASSES, or null for text shown uncolored.
 */
export function tokenizePython(code) {
  const text = typeof code === 'string' ? code : '';
  const tokens = [];
  const push = (value, kind) => {
    const last = tokens[tokens.length - 1];
    if (kind === null && last && last.kind === null) {
      last.text += value;
    } else {
      tokens.push({ text: value, kind });
    }
  };

  // A decorator is `@name` as the first thing on its line. Anywhere else `@` is
  // matrix multiplication, which is common in exactly this kind of code.
  let lineStart = true;
  let i = 0;
  while (i < text.length) {
    const ch = text[i];

    if (ch === '\n') {
      push(ch, null);
      lineStart = true;
      i++;
      continue;
    }
    const space = matchAt(SPACE, text, i);
    if (space) {
      push(space, null);
      i += space.length;
      continue;
    }

    const atLineStart = lineStart;
    lineStart = false;

    if (ch === '#') {
      const newline = text.indexOf('\n', i);
      const end = newline === -1 ? text.length : newline;
      push(text.slice(i, end), 'com');
      i = end;
      continue;
    }

    const opening = matchAt(STRING_START, text, i);
    if (opening) {
      const quote = opening.endsWith('"""') || opening.endsWith("'''") ? opening.slice(-3) : opening.slice(-1);
      const end = stringEnd(text, i + opening.length, quote);
      push(text.slice(i, end), 'str');
      i = end;
      continue;
    }

    if (ch === '@' && atLineStart) {
      const name = matchAt(IDENTIFIER, text, i + 1);
      if (name) {
        // A dotted decorator such as `@functools.lru_cache` is one token.
        let end = i + 1 + name.length;
        while (text[end] === '.') {
          const part = matchAt(IDENTIFIER, text, end + 1);
          if (!part) break;
          end += 1 + part.length;
        }
        push(text.slice(i, end), 'dec');
        i = end;
        continue;
      }
    }

    const word = matchAt(IDENTIFIER, text, i);
    if (word) {
      push(word, KEYWORDS.has(word) ? 'kw' : BUILTINS.has(word) ? 'bi' : null);
      i += word.length;
      continue;
    }

    const number = matchAt(NUMBER, text, i);
    if (number) {
      push(number, 'num');
      i += number.length;
      continue;
    }

    // One code point, so an astral character is never split into halves that
    // are then escaped and emitted as two separate tokens.
    const point = String.fromCodePoint(text.codePointAt(i));
    push(point, null);
    i += point.length;
  }
  return tokens;
}

/**
 * Highlight Python source as HTML.
 *
 * The only markup in the result is `<span class="osa-py-KIND">` and `</span>`,
 * with KIND from HIGHLIGHT_CLASSES. Everything else is escaped text, so the
 * result is safe to assign to innerHTML whatever the code contains.
 *
 * @param {string} code
 * @returns {string}
 */
export function highlightPython(code) {
  let html = '';
  for (const token of tokenizePython(code)) {
    const escaped = escapeHtml(token.text);
    html += token.kind === null ? escaped : `<span class="osa-py-${token.kind}">${escaped}</span>`;
  }
  return html;
}
