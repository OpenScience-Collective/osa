/**
 * Evaluate the length expressions happy-dom hands back from getComputedStyle.
 *
 * happy-dom substitutes var() but leaves calc() unevaluated, so a launcher offset
 * built from custom properties (issue #553) reads "calc(20px + 56px + 14px)" where a
 * browser would report "90px". A test asserts the number, which is what a browser
 * would draw, rather than the arithmetic's spelling. Only plain expressions are
 * accepted: px terms and bare numbers joined by + - * / and parentheses.
 */

/**
 * @param {string} value - A computed length or number, possibly a calc() of them.
 * @returns {number}
 */
export function cssNumber(value) {
  const expression = String(value)
    .trim()
    .replace(/(\d*\.?\d+)px\b/g, '$1')
    .replace(/calc\(/g, '(');
  if (!/^[\d\s.+\-*/()]+$/.test(expression)) {
    throw new Error(`not a plain px expression: ${JSON.stringify(value)}`);
  }
  // eslint-disable-next-line no-new-func
  return new Function(`return (${expression});`)();
}

/**
 * A space-separated list of expressions (the `translate` property), split outside
 * parentheses, each evaluated.
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
