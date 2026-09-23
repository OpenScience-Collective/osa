/**
 * Answer a Range request over `body` the way the data plane does, for tests that
 * serve bytes over real HTTP: the whole body when no range was asked for, a 206
 * for a suffix, a closed or an open-ended range, and a 416 for anything else.
 *
 * @param {Uint8Array} body
 * @param {string|null} range - The request's Range header.
 * @returns {Response}
 */
export function rangeResponse(body, range) {
  if (!range) return new Response(body);
  const suffix = /^bytes=-(\d+)$/.exec(range);
  if (suffix) return new Response(body.slice(body.length - Number(suffix[1])), { status: 206 });
  const bounded = /^bytes=(\d+)-(\d*)$/.exec(range);
  if (bounded) {
    const end = bounded[2] === '' ? body.length : Number(bounded[2]) + 1;
    return new Response(body.slice(Number(bounded[1]), end), { status: 206 });
  }
  return new Response('Range Not Satisfiable', { status: 416 });
}
