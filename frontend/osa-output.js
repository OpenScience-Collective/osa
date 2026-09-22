/**
 * The execution harness for the browser Python runtime (epic #429, phase #431,
 * steps 4 and 6).
 *
 * Execution without capture returns nothing a reader or a model can use. This is
 * the half that turns a run into a result: stdout and stderr, matplotlib figures
 * as PNG, a `display()` for anything the code hands back explicitly, and a
 * deterministic summary of what the run did.
 *
 * WHAT THE CONVERSATION CARRIES, AND WHAT STAYS IN THE BROWSER
 *
 * The model needs a compact description to choose its next step, not the bytes.
 * So a result carries structured facts (the variables the run created, with
 * dtype, shape, min, max, mean and NaN count; plot metadata; the exception type,
 * message and offending line) and bounded streams. The untruncated streams and
 * the full traceback stay in the browser, behind `get_full_output(call_id)`: the
 * common path stays cheap and the rare one stays possible.
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
  MAX_SUMMARY_CHARS: 8_192,
});

/**
 * Each `RuntimeLimits` field's default and floor, mirrored from
 * `src/core/config/community.py`.
 *
 * The ceilings come from `SERVER_LIMITS`. Like those, these are read back out of
 * the Python by `test-output.js`, which fails if a default or a `ge=` bound moves
 * on one side only.
 */
export const RUNTIME_LIMITS = Object.freeze({
  memory_mb: Object.freeze({ default: 1536, min: 64 }),
  stdout_chars: Object.freeze({ default: SERVER_LIMITS.MAX_STDOUT_CHARS, min: 256 }),
  stderr_chars: Object.freeze({ default: SERVER_LIMITS.MAX_STDERR_CHARS, min: 256 }),
  images: Object.freeze({ default: SERVER_LIMITS.MAX_IMAGES, min: 0 }),
  image_px: Object.freeze({ default: 1024, min: 16 }),
  exec_seconds: Object.freeze({ default: 120, min: 1 }),
});

/**
 * Resolve a community's declared limits against what the server will accept.
 *
 * A community cannot see the server's constants, so `RuntimeLimits` bounds its
 * fields by them. This clamps anyway, in BOTH directions: the config is
 * validated by a different process than the one running here, and a limit the
 * server will reject costs a 422 that reads as the browser misbehaving when it
 * was the config. The floor matters as much as the ceiling: below it, clipping a
 * stream leaves no room for its own omission marker.
 *
 * @param {object} [limits] - A community's `runtime.python.limits`.
 * @returns {{stdout_chars: number, stderr_chars: number, images: number, image_px: number,
 *   image_bytes: number, summary_chars: number, exec_seconds: number, memory_mb: number}}
 */
export function resolveLimits(limits = {}) {
  // One rule for every field. Below the floor, the value can only have come from
  // a config that skipped validation, so the known-good default is used rather
  // than the floor itself: clamping a zero deadline up to one second would time
  // out every run. Above the ceiling, the community asked for more than the
  // server accepts, so it gets the most the server accepts.
  const pick = (field, max = Number.POSITIVE_INFINITY) => {
    const { default: fallback, min } = RUNTIME_LIMITS[field];
    const value = limits[field];
    const valid = typeof value === 'number' && Number.isFinite(value) && Math.floor(value) >= min;
    return Math.min(valid ? Math.floor(value) : fallback, max);
  };
  return {
    stdout_chars: pick('stdout_chars', SERVER_LIMITS.MAX_STDOUT_CHARS),
    stderr_chars: pick('stderr_chars', SERVER_LIMITS.MAX_STDERR_CHARS),
    images: pick('images', SERVER_LIMITS.MAX_IMAGES),
    image_px: pick('image_px', SERVER_LIMITS.MAX_IMAGE_EDGE_PX),
    image_bytes: SERVER_LIMITS.MAX_IMAGE_BYTES,
    summary_chars: SERVER_LIMITS.MAX_SUMMARY_CHARS,
    // Not bounded against a server constant, because the server neither measures
    // nor enforces it: this is the host's own clock, applied as the deadline in
    // `execute`.
    exec_seconds: pick('exec_seconds'),
    // Carried for reporting only. wasm32 cannot be told to stop at a byte count:
    // memory is grown by the instance itself and an exhaustion aborts it, so
    // there is no point at which this number could be checked and enforced. What
    // the runtime does instead is recognize the abort and report `oom` rather
    // than a generic error, which is why `oom` is its own status.
    memory_mb: pick('memory_mb'),
  };
}

/**
 * Build the Python that runs in the INTERNAL namespace before anything else: the
 * import gate, and the captured reference to Pyodide's own code runner.
 *
 * Both need `pyodide.code`, which the namespace seal blocks a moment later, so
 * they are bound here first. They live in a namespace executed code has no
 * reference to: reachable, the gate could be redefined to approve the imports of
 * the NEXT execution.
 *
 * @returns {string} Python source.
 */
export function buildHelpersSource() {
  return String.raw`
import importlib.util as _ilu
from pyodide.code import eval_code_async as _eval_code_async
from pyodide.code import find_imports as _find_imports


def _unavailable_imports(code):
    """Top-level packages the code imports that this runtime does not have.

    Only ROOTS are checked. find_imports also returns submodules, and
    find_spec("numpy.linalg") imports numpy as a side effect of looking, so
    checking a submodule would run a package's initialization inside the gate.
    Whether the submodule exists is the run's own business once the root does.
    """
    try:
        names = _find_imports(code)
    except SyntaxError:
        # Let the run raise it, through the harness, so a syntax error is
        # reported like every other error: type, message and line.
        return []
    missing = []
    for root in sorted({name.partition(".")[0] for name in names}):
        try:
            if _ilu.find_spec(root) is None:
                missing.append(root)
        except ImportError:
            # The namespace seal's finder RAISES for a blocked root rather than
            # returning None, so a blocked import lands here and is denied.
            missing.append(root)
        except ValueError:
            # find_spec raises this for a module already in sys.modules whose
            # __spec__ is None. Such a module is importable, since the import
            # statement returns it from the cache, so it is not missing.
            pass
        # Anything else propagates. Catching it here would report an internal
        # failure as "this runtime has no <package>", a confident and wrong
        # answer the model would act on.
    return missing
`;
}

/**
 * Build the execution harness: capture, the run itself, and the summary.
 *
 * Runs in the internal namespace, so executed code cannot reach `_execute`,
 * `_begin` or `_end`. Only `display` is handed to the user namespace.
 *
 * Written with String.raw, so backslashes reach Python verbatim. The two
 * sequences that would still be interpreted, a backtick and a dollar sign
 * followed by a brace, do not occur in Python source; a test compiles the
 * result with a real interpreter regardless.
 *
 * @param {object} limits - From `resolveLimits`.
 * @returns {string} Python source.
 */
export function buildOutputCaptureSource(limits) {
  // A JSON string literal is also a valid Python string literal.
  const limitsLiteral = JSON.stringify(JSON.stringify(limits));
  return String.raw`
import ast as _ast
import base64 as _b64
import io as _io
import json as _json
import math as _math
import os as _os
import re as _re
import sys as _sys
import traceback as _traceback
import types as _types
import warnings as _warnings

# Set before matplotlib is ever imported. Pyodide defaults to a backend that
# draws into the page's DOM, and a worker has no DOM: the import succeeds and
# the first plot fails somewhere unrelated. MPLBACKEND is honored at import
# time, so this works even though matplotlib is imported later, by code we have
# not seen yet.
_os.environ["MPLBACKEND"] = "Agg"

_LIMITS = _json.loads(${limitsLiteral})

# How much of each stream the BROWSER keeps for get_full_output. Bounded because
# a print loop can produce hundreds of megabytes and this lives in a tab.
_FULL_CHARS = 262144

# The summary lists at most this many variables, then says how many it left out.
_MAX_VARIABLES = 24

# Statistics are O(n). Past this, describing the result would spend the run's
# own deadline, so the summary says it skipped them instead.
_MAX_STAT_ELEMENTS = 10000000

# Names the runtime placed in the namespace, which are not the run's variables.
_HIDDEN = frozenset({"osa", "display"})

_state = {
    "stdout": None,
    "stderr": None,
    "saved": None,
    "call_id": "",
    "code": "",
    "exception": None,
    "traceback": "",
    "displayed": set(),
}
_pending = {"images": [], "artifacts": []}
_figures = []
_notes = []

# Default object reprs carry a heap address, which differs on every run and
# would invalidate the conversation prefix for every later turn.
_ADDRESS = _re.compile(r" at 0x[0-9a-fA-F]+")


def _note(message):
    """Record an explanation once.

    Appending directly repeated the same sentence for every image a cap dropped,
    which spends the stderr budget saying one thing three times.
    """
    if message not in _notes:
        _notes.append(message)


def _fmt(value):
    """Six significant figures, the same way every time."""
    if isinstance(value, bool):
        return str(value)
    try:
        return "%.6g" % value
    except (TypeError, ValueError):
        return str(value)


def _png_size(data):
    """Width and height from the PNG IHDR chunk.

    Read from the bytes rather than from whatever produced them: a figure saved
    with bbox_inches="tight" is not the size its dpi implies, and the server
    validates what it actually receives.
    """
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def _add_image(data, mime="image/png"):
    """Attach one image, or say why it was refused.

    Never drops one silently. A figure that vanishes with no explanation reads
    as the plotting code being wrong, and the model then rewrites working code
    to fix a cap it was never told about.
    """
    if _LIMITS["images"] == 0:
        _note("[runtime] images are disabled for this community.")
        return False
    if len(_pending["images"]) >= _LIMITS["images"]:
        _note(
            "[runtime] only the first %d images are returned; later ones were dropped."
            % _LIMITS["images"]
        )
        return False
    width, height = _png_size(data)
    if width < 1 or height < 1:
        # ToolResultImage requires width and height >= 1 and refuses the WHOLE
        # result on a violation, so bytes that are not a readable PNG would cost
        # the run its stdout as well. Refused here, with a reason.
        _note(
            "[runtime] display() was given %d bytes that are not a readable PNG, "
            "so nothing was attached. Pass PNG bytes, a matplotlib figure, or an "
            "object with _repr_png_." % len(data)
        )
        return False
    if width > _LIMITS["image_px"] or height > _LIMITS["image_px"]:
        _note(
            "[runtime] an image was %dx%d, over the %dpx limit, and was dropped."
            % (width, height, _LIMITS["image_px"])
        )
        return False
    if len(data) > _LIMITS["image_bytes"]:
        _note(
            "[runtime] an image was %d bytes, over the %d-byte limit, and was dropped. "
            "Try fewer points, a smaller figsize, or a lower dpi."
            % (len(data), _LIMITS["image_bytes"])
        )
        return False
    _pending["images"].append(
        {
            "mime": mime,
            "data_base64": _b64.b64encode(data).decode("ascii"),
            "width": width,
            "height": height,
        }
    )
    return True


def _figure_png(figure):
    """Render a matplotlib figure, scaled to fit the pixel cap."""
    inches = figure.get_size_inches()
    longest = max(float(inches[0]), float(inches[1]))
    dpi = float(figure.dpi)
    if longest > 0:
        dpi = min(dpi, _LIMITS["image_px"] / longest)
    buffer = _io.BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight")
    return buffer.getvalue()


def _figure_meta(figure, width, height, attached):
    """What a figure shows, as text that survives the image being stripped.

    Stored history replaces every image with a placeholder, so after one turn
    this line is ALL the model knows about the plot. Data limits come from the
    axes' own record of what was drawn rather than from the view, which carries
    matplotlib's padding.
    """
    parts = ["%dx%d png" % (width, height) if attached else "not attached, see stderr"]
    suptitle = getattr(figure, "_suptitle", None)
    if suptitle is not None and suptitle.get_text():
        parts.append("suptitle=%r" % suptitle.get_text())
    for index, axes in enumerate(figure.get_axes(), 1):
        bits = []
        for label, text in (
            ("title", axes.get_title()),
            ("x", axes.get_xlabel()),
            ("y", axes.get_ylabel()),
        ):
            if text:
                bits.append("%s=%r" % (label, text))
        for label, items in (
            ("lines", axes.lines),
            ("collections", axes.collections),
            ("images", axes.images),
            ("patches", axes.patches),
        ):
            if len(items):
                bits.append("%s=%d" % (label, len(items)))
        labels = [text for text in axes.get_legend_handles_labels()[1] if text][:6]
        if labels:
            bits.append("series=%r" % labels)
        box = axes.dataLim
        edges = (box.x0, box.x1, box.y0, box.y1)
        if all(_math.isfinite(edge) for edge in edges):
            bits.append(
                "data x=[%s, %s] y=[%s, %s]" % tuple(_fmt(edge) for edge in edges)
            )
        parts.append("axes %d: %s" % (index, " ".join(bits) if bits else "empty"))
    return "; ".join(parts)


def _attach_figure(figure):
    """Render, attach and describe one figure.

    Described even when a cap refuses the image, because the description is
    small and is the only record of the plot the model will have.
    """
    _state["displayed"].add(id(figure))
    data = _figure_png(figure)
    width, height = _png_size(data)
    attached = _add_image(data)
    try:
        _figures.append(_figure_meta(figure, width, height, attached))
    except Exception as exc:
        _figures.append("could not be described: %s" % type(exc).__name__)


def _collect_figures():
    """Attach any figure the code left open, then close them all.

    Looked up in sys.modules rather than imported, so this costs nothing when the
    code never plotted and never drags matplotlib in by itself. A figure already
    handed to display() is skipped: it is still open in pyplot's registry, and
    collecting it again returned the same plot twice.
    """
    plt = _sys.modules.get("matplotlib.pyplot")
    if plt is None:
        return
    try:
        numbers = list(plt.get_fignums())
    except Exception as exc:
        # Returning quietly here made a run that plotted look exactly like one
        # that did not: ok, no images, no stderr. Said out loud instead, like a
        # figure that fails to render one line below.
        _note("[runtime] figures could not be collected: %s" % type(exc).__name__)
        return
    for number in numbers:
        figure = plt.figure(number)
        if id(figure) in _state["displayed"]:
            continue
        try:
            _attach_figure(figure)
        except Exception as exc:
            _note("[runtime] a figure could not be rendered: %s" % type(exc).__name__)
    try:
        plt.close("all")
    except Exception:
        pass


def display(obj):
    """Attach an image or a matplotlib figure; print anything else as text.

    HTML is deliberately not emitted. ClientToolResult has no channel for it, so
    it would land in stdout as markup the model pays for token by token, and
    pandas' own text rendering is already a readable table.
    """
    if isinstance(obj, (bytes, bytearray)):
        _add_image(bytes(obj))
        return
    if callable(getattr(obj, "savefig", None)) and callable(getattr(obj, "get_axes", None)):
        _attach_figure(obj)
        return
    renderer = getattr(obj, "_repr_png_", None)
    if callable(renderer):
        data = renderer()
        if isinstance(data, str):
            data = _b64.b64decode(data)
        if data:
            _add_image(bytes(data))
            return
    print(obj)


def _is_plot_return(value):
    """True for what a plotting call returns.

    A run ending in plt.plot(...) returns Line2D objects, and displaying them
    prints a list of artist reprs that says nothing the figure does not. Any
    matplotlib object in a returned tuple counts, since plt.hist returns its
    counts and bins alongside the patches.
    """
    items = value if isinstance(value, (list, tuple)) else (value,)
    return any(type(item).__module__.partition(".")[0] == "matplotlib" for item in items)


def _qualname(kind):
    module = getattr(kind, "__module__", "")
    name = getattr(kind, "__qualname__", repr(kind))
    return name if module in ("builtins", "") else "%s.%s" % (module, name)


def _array_stats(array, numpy):
    """Statistics for a real, in-memory numeric numpy array, as a suffix."""
    if array.size == 0:
        return " empty"
    if array.dtype.kind not in "biuf":
        return ""
    if array.size > _MAX_STAT_ELEMENTS:
        return " stats skipped (%d elements)" % array.size
    if array.dtype.kind == "f":
        nan = int(numpy.isnan(array).sum())
        finite = array[numpy.isfinite(array)]
        infinite = int(array.size - nan - finite.size)
        tail = " nan=%d" % nan + (" inf=%d" % infinite if infinite else "")
        if finite.size == 0:
            return " no finite values" + tail
        return " min=%s max=%s mean=%s%s" % (
            _fmt(finite.min()),
            _fmt(finite.max()),
            _fmt(finite.mean()),
            tail,
        )
    return " min=%s max=%s mean=%s" % (_fmt(array.min()), _fmt(array.max()), _fmt(array.mean()))


def _describe(value):
    """One line of facts about a value, never its repr.

    A default repr carries a heap address, and a repr of anything large is the
    bulk this summary exists to avoid.
    """
    numpy = _sys.modules.get("numpy")
    pandas = _sys.modules.get("pandas")
    try:
        if value is None or isinstance(value, bool):
            return repr(value)
        if isinstance(value, int):
            if abs(value) < 10**15:
                return "int = %d" % value
            return "int (%d bits)" % value.bit_length()
        if isinstance(value, float):
            return "float = %s" % _fmt(value)
        if isinstance(value, complex):
            return "complex = %s%+gj" % (_fmt(value.real), value.imag)
        if isinstance(value, str):
            return "str len=%d = %r%s" % (len(value), value[:80], "..." if len(value) > 80 else "")
        if isinstance(value, (bytes, bytearray)):
            return "%s len=%d" % (type(value).__name__, len(value))
        if isinstance(value, (list, tuple, set, frozenset, dict)):
            return "%s len=%d" % (type(value).__name__, len(value))
        if numpy is not None:
            if isinstance(value, numpy.ndarray):
                return "ndarray %s shape=%s%s" % (
                    value.dtype,
                    tuple(value.shape),
                    _array_stats(value, numpy),
                )
            if isinstance(value, numpy.generic):
                return "%s = %s" % (type(value).__name__, _fmt(value.item()))
        if pandas is not None:
            if isinstance(value, pandas.DataFrame):
                columns = ["%s:%s" % (name, dtype) for name, dtype in list(value.dtypes.items())[:12]]
                more = " +%d more" % (value.shape[1] - 12) if value.shape[1] > 12 else ""
                return "DataFrame shape=%s columns=[%s%s]" % (
                    tuple(value.shape),
                    ", ".join(columns),
                    more,
                )
            if isinstance(value, pandas.Series):
                stats = _array_stats(value.to_numpy(), numpy) if numpy is not None else ""
                return "Series len=%d dtype=%s%s" % (len(value), value.dtype, stats)
        if isinstance(value, _types.FunctionType):
            return "function"
        if isinstance(value, type):
            return "class"
        shape = getattr(value, "shape", None)
        if isinstance(shape, tuple):
            # An array-like that is NOT an in-memory numpy array: a zarr array,
            # a lazy or remote view. Its values are never read here, because
            # reading them could mean fetching every chunk over the network.
            return "%s shape=%s dtype=%s (values not read)" % (
                _qualname(type(value)),
                shape,
                getattr(value, "dtype", "?"),
            )
        return _qualname(type(value))
    except Exception as exc:
        return "%s (could not be described: %s)" % (_qualname(type(value)), type(exc).__name__)


def _error_line(exc):
    """The line of the RUN's own code the exception came from.

    The deepest frame in the cell, not the deepest frame overall: an error raised
    inside numpy is reported at the line that called numpy, which is the line the
    model can change. A syntax error has no frame and carries its own line.
    """
    if isinstance(exc, SyntaxError) and exc.filename == "<cell>":
        return exc.lineno
    line = None
    for frame, number in _traceback.walk_tb(exc.__traceback__):
        if frame.f_code.co_filename == "<cell>":
            line = number
    return line


def _error_text(exc):
    """Exception type, message and offending line, in place of a traceback.

    The full traceback stays in the browser. Most of it is Pyodide's own frames
    under /lib/python3*.zip, which differ between Pyodide versions and say
    nothing about the code that failed.
    """
    message = _ADDRESS.sub("", str(exc)).strip()
    if len(message) > 500:
        message = message[:500] + "..."
    text = "%s: %s" % (type(exc).__name__, message) if message else type(exc).__name__
    line = _error_line(exc)
    if line:
        source = _state["code"].splitlines()
        code = source[line - 1].strip() if 0 < line <= len(source) else ""
        text += "\n  line %d: %s" % (line, code) if code else "\n  line %d" % line
    return text


def _clip(text, limit):
    """Bound a stream, keeping BOTH ends.

    Which end matters is not knowable here: a long run's first lines say what it
    set out to do and its last say how it ended, and a traceback is all tail.
    Keeping one end would silently discard the useful half for whichever case it
    guessed wrong about, so it keeps both and says what it dropped.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    marker_budget = 64
    if limit <= marker_budget:
        return text[:limit]
    head = (limit - marker_budget) * 3 // 5
    tail = limit - marker_budget - head
    dropped = len(text) - head - tail
    return "%s\n... %d characters omitted ...\n%s" % (text[:head], dropped, text[-tail:])


def _summary(changed, namespace, truncated):
    """Structured facts about the run, which is what the model reasons over."""
    lines = []
    call_id = _state["call_id"]
    handle = _json.dumps(call_id)
    if call_id and (truncated or _state["traceback"] or _figures):
        # Printed only when there is something to retrieve. The handle is fixed
        # for the life of this result, so it costs no cache stability.
        lines.append("call_id: %s" % call_id)

    names = []
    for name in changed or ():
        if name.startswith("_") or name in _HIDDEN:
            continue
        if isinstance(namespace.get(name), _types.ModuleType):
            continue
        names.append(name)
    if names:
        lines.append("variables:")
        for name in names[:_MAX_VARIABLES]:
            lines.append("  %s: %s" % (name, _describe(namespace.get(name))))
        if len(names) > _MAX_VARIABLES:
            lines.append("  ... and %d more" % (len(names) - _MAX_VARIABLES))

    if _figures:
        lines.append("figures:")
        for index, meta in enumerate(_figures, 1):
            lines.append("  %d: %s" % (index, meta))

    exc = _state["exception"]
    if exc is not None:
        line = _error_line(exc)
        where = " at line %d" % line if line else ""
        lines.append("error: %s%s (stderr has the message)" % (type(exc).__name__, where))

    for stream, total in truncated:
        text = "%s: %d characters, clipped here; get_full_output(call_id=%s, stream=%s) reads them" % (
            stream,
            total,
            handle,
            _json.dumps(stream),
        )
        if total > _FULL_CHARS:
            text += " (the browser kept %d of them)" % _FULL_CHARS
        lines.append(text)
    if _state["traceback"]:
        lines.append("traceback: get_full_output(call_id=%s, stream=\"traceback\")" % handle)

    return _clip("\n".join(lines), _LIMITS["summary_chars"])


def _assigned_names(code):
    """Module-level names the code assigns to, including through a subscript or
    an attribute.

    Identity alone misses the most common update in data code: signal[3] = nan,
    signal -= signal.mean(), frame["z"] = ... all change a value in place and
    keep its id, so a summary built on identity reported nothing about them.
    Read from the code, so it is cheap and never touches the data.

    Function and class bodies, lambdas and comprehensions are not entered: a
    name bound there is local to that scope. A mutating method call such as
    items.append(x) is not detected, since from the syntax alone it cannot be
    told apart from a call that reads.
    """
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return set()
    names = set()
    scopes = (
        _ast.FunctionDef,
        _ast.AsyncFunctionDef,
        _ast.ClassDef,
        _ast.Lambda,
        _ast.ListComp,
        _ast.SetComp,
        _ast.DictComp,
        _ast.GeneratorExp,
    )
    pending = list(_ast.iter_child_nodes(tree))
    while pending:
        node = pending.pop()
        if isinstance(node, scopes):
            continue
        if isinstance(node, _ast.Name) and isinstance(node.ctx, _ast.Store):
            names.add(node.id)
        elif isinstance(node, (_ast.Subscript, _ast.Attribute)) and isinstance(node.ctx, _ast.Store):
            base = node.value
            while isinstance(base, (_ast.Subscript, _ast.Attribute)):
                base = base.value
            if isinstance(base, _ast.Name):
                names.add(base.id)
        pending.extend(_ast.iter_child_nodes(node))
    return names


def _begin(call_id="", code=""):
    _state["saved"] = (_sys.stdout, _sys.stderr)
    _state["stdout"] = _io.StringIO()
    _state["stderr"] = _io.StringIO()
    _state["call_id"] = call_id
    _state["code"] = code
    _state["exception"] = None
    _state["traceback"] = ""
    _state["displayed"] = set()
    _sys.stdout = _state["stdout"]
    _sys.stderr = _state["stderr"]
    _pending["images"].clear()
    _pending["artifacts"].clear()
    del _figures[:]
    del _notes[:]


def _record(exc):
    _state["exception"] = exc
    try:
        _state["traceback"] = "".join(_traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        _state["traceback"] = "%s: %s" % (type(exc).__name__, exc)


def _end(error_text="", status="ok", changed=None, namespace=None):
    """Restore the streams and return this run's result as JSON.

    JSON rather than a proxied object, so the host parses one value and holds no
    Python references it has to remember to destroy.
    """
    if _state["saved"] is not None:
        _sys.stdout, _sys.stderr = _state["saved"]
        _state["saved"] = None
    _collect_figures()

    out = _ADDRESS.sub("", _state["stdout"].getvalue() if _state["stdout"] is not None else "")
    parts = [
        _state["stderr"].getvalue() if _state["stderr"] is not None else "",
        error_text,
        _error_text(_state["exception"]) if _state["exception"] is not None else "",
        "\n".join(_notes),
    ]
    err = _ADDRESS.sub("", "\n".join(part.rstrip("\n") for part in parts if part))

    truncated = []
    if len(out) > _LIMITS["stdout_chars"]:
        truncated.append(("stdout", len(out)))
    if len(err) > _LIMITS["stderr_chars"]:
        truncated.append(("stderr", len(err)))

    return _json.dumps(
        {
            "status": status,
            "stdout": _clip(out, _LIMITS["stdout_chars"]),
            "stderr": _clip(err, _LIMITS["stderr_chars"]),
            "summary": _summary(changed, namespace if namespace is not None else {}, truncated),
            "images": list(_pending["images"]),
            "artifacts": sorted(set(_pending["artifacts"]))[:32],
            "full": {
                "stdout": _clip(out, _FULL_CHARS),
                "stderr": _clip(err, _FULL_CHARS),
                "traceback": _clip(_ADDRESS.sub("", _state["traceback"]), _FULL_CHARS),
            },
        },
        sort_keys=True,
    )


async def _execute(call_id, code, namespace):
    """Run one block of code and return its result as JSON.

    Every Python exception, SystemExit included, is caught HERE, so executed
    code can neither end the worker nor leave the streams swapped. What escapes
    to the host is the instance itself failing, which is what an out-of-memory
    abort looks like from outside.
    """
    before = {name: id(value) for name, value in namespace.items()}
    _begin(call_id, code)
    status = "ok"
    try:
        with _warnings.catch_warnings():
            # plt.show() under Agg warns that the backend cannot show anything.
            # The figure is collected after the run regardless, so the warning
            # is noise on every plot.
            _warnings.filterwarnings("ignore", message=".*non-interactive.*cannot be shown")
            value = await _eval_code_async(
                code,
                namespace,
                filename="<cell>",
                return_mode="last_expr",
                quiet_trailing_semicolon=True,
            )
            # The value of a trailing expression is shown, as a notebook would.
            # A trailing semicolon suppresses it, as it does there.
            if value is not None and not _is_plot_return(value):
                display(value)
    except MemoryError as exc:
        status = "oom"
        _record(exc)
    except BaseException as exc:
        status = "error"
        _record(exc)
    # Rebound or new (by identity), or assigned to in place (by the code). A name
    # the code assigned but that no longer exists, because the run failed first
    # or deleted it, is not listed.
    touched = _assigned_names(code)
    changed = sorted(
        name
        for name, value in namespace.items()
        if before.get(name) != id(value) or name in touched
    )
    return _end(status=status, changed=changed, namespace=namespace)
`;
}
