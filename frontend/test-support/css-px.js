/**
 * Evaluate the length expressions happy-dom hands back from getComputedStyle.
 *
 * happy-dom substitutes var() but leaves calc() unevaluated, so a launcher offset
 * built from custom properties (issue #553) reads "calc(20px + 56px + 14px)" where a
 * browser would report "90px". A test asserts the number, which is what a browser
 * would draw, rather than the arithmetic's spelling. Only plain expressions are
 * accepted: px terms and bare numbers joined by + - * / and parentheses, and min() and
 * max() of them.
 */

/**
 * @param {string} value - A computed length or number, possibly a calc(), min() or max()
 *   of them.
 * @returns {number} Always a finite number: anything else throws, so an assertion on it
 *   can never pass because the value was blank or nonsense.
 */
export function cssNumber(value) {
  const expression = String(value)
    .trim()
    .replace(/(\d*\.?\d+)px\b/g, '$1')
    .replace(/calc\(/g, '(')
    .replace(/\b(min|max)\(/g, 'Math.$1(');
  const plain = expression.replace(/Math\.(min|max)/g, '');
  if (!/^[\d\s.+\-*/(),]+$/.test(plain) || /\*\*|\/\*|\*\//.test(plain) || !/\d/.test(plain)) {
    throw new Error(`not a plain px expression: ${JSON.stringify(value)}`);
  }
  // eslint-disable-next-line no-new-func
  const number = new Function(`return (${expression});`)();
  if (!Number.isFinite(number)) throw new Error(`${JSON.stringify(value)} is not a finite number (${number})`);
  return number;
}

/**
 * A space-separated list of expressions (the `translate` property), split outside
 * parentheses, each evaluated. A blank list throws: an empty `translate` is a rule that
 * stopped matching, not a translate of nothing.
 *
 * @param {string} value
 * @returns {number[]}
 */
export function cssNumbers(value) {
  const parts = [];
  let depth = 0;
  let current = '';
  for (const char of String(value).trim()) {
    if (char === '(') depth += 1;
    if (char === ')') depth -= 1;
    if (/\s/.test(char) && depth === 0) {
      if (current) parts.push(current);
      current = '';
    } else {
      current += char;
    }
  }
  if (current) parts.push(current);
  if (parts.length === 0) throw new Error(`no values in ${JSON.stringify(value)}`);
  return parts.map(cssNumber);
}

/**
 * Replace each var(--name) in a declaration read straight from a stylesheet rule (not
 * from getComputedStyle, which substitutes them itself) with the value the element's
 * computed style gives that custom property.
 *
 * @param {string} text
 * @param {CSSStyleDeclaration} computed - getComputedStyle of the element the rule applies to.
 * @returns {string}
 */
export function withVars(text, computed) {
  let out = String(text);
  for (let pass = 0; pass < 10 && /var\(/.test(out); pass += 1) {
    out = out.replace(/var\((--[\w-]+)(?:\s*,[^)]*)?\)/g, (_, name) => {
      const value = computed.getPropertyValue(name).trim();
      if (!value) throw new Error(`custom property ${name} has no computed value`);
      return value;
    });
  }
  return out;
}
