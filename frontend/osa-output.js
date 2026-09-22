/**
 * Output capture for the browser Python runtime (epic #429, phase #431, step 4).
 *
 * Execution without capture returns nothing a reader or a model can use. This is
 * the half that turns a run into a result: stdout and stderr, matplotlib figures
 * as PNG, and a `display()` for anything the code wants to hand back explicitly.
 *
 * WHY DETERMINISM IS LOAD-BEARING HERE
 *
 * Prompt caching is a byte-exact prefix match, and `_prepare_messages` trims from
 * the FRONT at an 80,000-token budget. A result carrying a memory address does not
 * cost tokens once; it invalidates the prefix for every later turn in the
 * conversation. Phase 1 already keeps its half deterministic, `_fence` omits
 * `elapsed_ms` on purpose. This module must not undo it, which is why captured
 * text has object addresses stripped, artifacts are sorted and de-duplicated, and
 * the JSON is emitted with sorted keys.
 *
 * WHY THE CAPS ARE APPLIED HERE RATHER THAN ONLY ON THE SERVER
 *
 * `ClientToolResult` is `extra="forbid"` and every field is sized, so a result
 * over any cap is rejected WHOLE with a 422. A figure a little too large would
 * therefore lose the entire run, including the stdout that explains it. The
 * browser is still not trusted; the server re-checks. This is about failing
 * legibly rather than about trust.
 */

/**
 * Limits the server enforces on the way in, mirrored from `src/core/limits.py`.
 *
 * Duplicated across a language boundary, so `test-output.js` reads the Python and
 * fails if these drift. Without that test this comment would be the only thing
 * keeping them together, and a comment has never stopped a constant from moving.
 */
export const SERVER_LIMITS = Object.freeze({
  MAX_STDOUT_CHARS: 16_384,
  MAX_STDERR_CHARS: 8_192,
  MAX_IMAGES: 3,
  MAX_IMAGE_BYTES: 2_000_000,
  MAX_IMAGE_EDGE_PX: 8192,
});

/**
 * Resolve a community's declared limits against what the server will accept.
 *
 * A community cannot see the server's constants, so `RuntimeLimits` bounds its
 * fields by them. This clamps anyway: the config is validated by a different
 * process than the one running here, and honoring a limit the server will reject
 * produces a 422 that reads as the browser misbehaving when it was the config.
 *
 * @param {object} [limits] - A community's `runtime.python.limits`.
 * @returns {{stdout_chars: number, stderr_chars: number, images: number, image_px: number, image_bytes: number}}
 */
export function resolveLimits(limits = {}) {
  const positive = (value, fallback) =>
    typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback;
  const bounded = (value, fallback, max) => {
    const n = typeof value === 'number' && Number.isFinite(value) ? Math.floor(value) : fallback;
    return Math.max(0, Math.min(n, max));
  };
  return {
    stdout_chars: bounded(limits.stdout_bytes, SERVER_LIMITS.MAX_STDOUT_CHARS, SERVER_LIMITS.MAX_STDOUT_CHARS),
    stderr_chars: bounded(limits.stderr_bytes, SERVER_LIMITS.MAX_STDERR_CHARS, SERVER_LIMITS.MAX_STDERR_CHARS),
    images: bounded(limits.images, SERVER_LIMITS.MAX_IMAGES, SERVER_LIMITS.MAX_IMAGES),
    image_px: bounded(limits.image_px, 1024, SERVER_LIMITS.MAX_IMAGE_EDGE_PX),
    image_bytes: SERVER_LIMITS.MAX_IMAGE_BYTES,
    // Not bounded against a server constant, because the server neither measures
    // nor enforces either one. `exec_seconds` is the host's own clock; see the
    // deadline in `execute`, which is where it is applied.
    exec_seconds: Math.max(1, positive(limits.exec_seconds, 120)),
    // Carried for reporting only. wasm32 cannot be told to stop at a byte count:
    // memory is grown by the instance itself and an exhaustion aborts it, so
    // there is no point at which this number could be checked and enforced. What
    // the runtime does instead is recognize the abort and report `oom` rather
    // than a generic error, which is why `oom` is its own status.
    memory_mb: Math.max(64, positive(limits.memory_mb, 1536)),
  };
}

/**
 * Build the Python that captures one execution's output.
 *
 * Runs in the internal namespace, so executed code cannot reach `_begin` or
 * `_end`. Only `display` is handed to the user namespace. Tampering here would
 * corrupt nothing but the run's own result, but keeping the seam in one place is
 * what makes that true rather than merely likely.
 *
 * @param {object} limits - From `resolveLimits`.
 * @returns {string} Python source.
 */
export function buildOutputCaptureSource(limits) {
  const json = JSON.stringify(limits);
  return [
    'import base64 as _b64',
    'import io as _io',
    'import json as _json',
    'import os as _os',
    'import re as _re',
    'import sys as _sys',
    '',
    '# Set before matplotlib is ever imported. Pyodide defaults to a backend that',
    "# draws into the page's DOM, and a worker has no DOM: the import succeeds and",
    '# the first plot fails somewhere unrelated. MPLBACKEND is honored at import',
    '# time, so this works even though matplotlib is imported later, by code we',
    '# have not seen yet.',
    '_os.environ["MPLBACKEND"] = "Agg"',
    '',
    `_LIMITS = _json.loads(${JSON.stringify(json)})`,
    '',
    '_state = {"stdout": None, "stderr": None, "saved": None}',
    '_pending = {"images": [], "artifacts": []}',
    '_notes = []',
    '',
    '',
    'def _note(message):',
    '    """Record an explanation once.',
    '',
    '    Appending directly repeated the same sentence for every image a cap',
    '    dropped, which spends the stderr budget saying one thing three times.',
    '    """',
    '    if message not in _notes:',
    '        _notes.append(message)',
    '',
    '# Default object reprs carry a heap address, which differs on every run and',
    '# would invalidate the conversation prefix for every later turn.',
    '_ADDRESS = _re.compile(r" at 0x[0-9a-fA-F]+")',
    '',
    '',
    'def _png_size(data):',
    '    """Width and height from the PNG IHDR chunk.',
    '',
    '    Read from the bytes rather than from whatever produced them: a figure',
    '    saved with bbox_inches="tight" is not the size its dpi implies, and the',
    '    server validates what it actually receives.',
    '    """',
    '    if len(data) < 24 or data[:8] != b"\\x89PNG\\r\\n\\x1a\\n":',
    '        return (0, 0)',
    '    return (',
    '        int.from_bytes(data[16:20], "big"),',
    '        int.from_bytes(data[20:24], "big"),',
    '    )',
    '',
    '',
    'def _add_image(data, mime="image/png"):',
    '    """Attach one image, or say why it was refused.',
    '',
    '    Never drops one silently. A figure that vanishes with no explanation',
    '    reads as the plotting code being wrong, and the model then rewrites',
    '    working code to fix a cap it was never told about.',
    '    """',
    '    if _LIMITS["images"] == 0:',
    '        _note("[runtime] images are disabled for this community.")',
    '        return False',
    '    if len(_pending["images"]) >= _LIMITS["images"]:',
    '        _note(',
    '            "[runtime] only the first %d images are returned; later ones were dropped."',
    '            % _LIMITS["images"]',
    '        )',
    '        return False',
    '    width, height = _png_size(data)',
    '    if width < 1 or height < 1:',
    '        # ToolResultImage requires width and height >= 1 and refuses the',
    '        # WHOLE result on a violation, so bytes that are not a readable PNG',
    '        # would cost the run its stdout as well. Refused here, with a reason.',
    '        _note(',
    '            "[runtime] display() was given %d bytes that are not a readable PNG, "',
    '            "so nothing was attached. Pass PNG bytes, a matplotlib figure, or an "',
    '            "object with _repr_png_." % len(data)',
    '        )',
    '        return False',
    '    if width > _LIMITS["image_px"] or height > _LIMITS["image_px"]:',
    '        _note(',
    '            "[runtime] an image was %dx%d, over the %dpx limit, and was dropped."',
    '            % (width, height, _LIMITS["image_px"])',
    '        )',
    '        return False',
    '    if len(data) > _LIMITS["image_bytes"]:',
    '        _note(',
    '            "[runtime] an image was %d bytes, over the %d-byte limit, and was dropped. "',
    '            "Try fewer points, a smaller figsize, or a lower dpi."',
    '            % (len(data), _LIMITS["image_bytes"])',
    '        )',
    '        return False',
    '    _pending["images"].append(',
    '        {',
    '            "mime": mime,',
    '            "data_base64": _b64.b64encode(data).decode("ascii"),',
    '            "width": width,',
    '            "height": height,',
    '        }',
    '    )',
    '    return True',
    '',
    '',
    'def _figure_png(figure):',
    '    """Render a matplotlib figure, scaled to fit the pixel cap."""',
    '    inches = figure.get_size_inches()',
    '    longest = max(float(inches[0]), float(inches[1]))',
    '    dpi = float(figure.dpi)',
    '    if longest > 0:',
    '        dpi = min(dpi, _LIMITS["image_px"] / longest)',
    '    buffer = _io.BytesIO()',
    '    figure.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight")',
    '    return buffer.getvalue()',
    '',
    '',
    'def _collect_figures():',
    '    """Attach any figure the code left open, then close them all.',
    '',
    '    Looked up in sys.modules rather than imported, so this costs nothing when',
    '    the code never plotted, and never drags matplotlib in by itself.',
    '    """',
    '    plt = _sys.modules.get("matplotlib.pyplot")',
    '    if plt is None:',
    '        return',
    '    try:',
    '        numbers = list(plt.get_fignums())',
    '    except Exception:',
    '        return',
    '    for number in numbers:',
    '        try:',
    '            _add_image(_figure_png(plt.figure(number)))',
    '        except Exception as exc:',
    '            _note("[runtime] a figure could not be rendered: %s" % type(exc).__name__)',
    '    try:',
    '        plt.close("all")',
    '    except Exception:',
    '        pass',
    '',
    '',
    'def display(obj):',
    '    """Hand an image or an HTML table back alongside this run\'s output."""',
    '    if isinstance(obj, (bytes, bytearray)):',
    '        _add_image(bytes(obj))',
    '        return',
    '    renderer = getattr(obj, "_repr_png_", None)',
    '    if callable(renderer):',
    '        data = renderer()',
    '        if isinstance(data, str):',
    '            data = _b64.b64decode(data)',
    '        if data:',
    '            _add_image(bytes(data))',
    '            return',
    '    if callable(getattr(obj, "savefig", None)):',
    '        _add_image(_figure_png(obj))',
    '        return',
    '    renderer = getattr(obj, "_repr_html_", None)',
    '    if callable(renderer):',
    '        print(renderer())',
    '        return',
    '    print(obj)',
    '',
    '',
    'def _clip(text, limit):',
    '    """Bound a stream, keeping BOTH ends.',
    '',
    '    Which end matters is not knowable here: a long run\'s first lines say what',
    '    it set out to do and its last say how it ended, and a traceback is all',
    '    tail. Keeping one end would silently discard the useful half for whichever',
    '    case it guessed wrong about, so it keeps both and says what it dropped.',
    '    """',
    '    if limit <= 0:',
    '        return ""',
    '    if len(text) <= limit:',
    '        return text',
    '    marker_budget = 64',
    '    if limit <= marker_budget:',
    '        return text[:limit]',
    '    head = (limit - marker_budget) * 3 // 5',
    '    tail = limit - marker_budget - head',
    '    dropped = len(text) - head - tail',
    '    return "%s\\n... %d characters omitted ...\\n%s" % (text[:head], dropped, text[-tail:])',
    '',
    '',
    'def _begin():',
    '    _state["saved"] = (_sys.stdout, _sys.stderr)',
    '    _state["stdout"] = _io.StringIO()',
    '    _state["stderr"] = _io.StringIO()',
    '    _sys.stdout = _state["stdout"]',
    '    _sys.stderr = _state["stderr"]',
    '    _pending["images"].clear()',
    '    _pending["artifacts"].clear()',
    '    del _notes[:]',
    '',
    '',
    'def _end(error_text=""):',
    '    """Restore the streams and return this run\'s output as JSON.',
    '',
    '    JSON rather than a proxied object, so the host parses one value and holds',
    '    no Python references it has to remember to destroy.',
    '    """',
    '    if _state["saved"] is not None:',
    '        _sys.stdout, _sys.stderr = _state["saved"]',
    '        _state["saved"] = None',
    '    _collect_figures()',
    '    out = _state["stdout"].getvalue() if _state["stdout"] is not None else ""',
    '    err = _state["stderr"].getvalue() if _state["stderr"] is not None else ""',
    '    parts = [p for p in (err, error_text, "\\n".join(_notes)) if p]',
    '    err = "\\n".join(parts)',
    '    return _json.dumps(',
    '        {',
    '            "stdout": _clip(_ADDRESS.sub("", out), _LIMITS["stdout_chars"]),',
    '            "stderr": _clip(_ADDRESS.sub("", err), _LIMITS["stderr_chars"]),',
    '            "images": list(_pending["images"]),',
    '            "artifacts": sorted(set(_pending["artifacts"]))[:32],',
    '        },',
    '        sort_keys=True,',
    '    )',
  ].join('\n');
}
