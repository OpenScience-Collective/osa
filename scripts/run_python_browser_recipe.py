"""Run one `python_browser` recipe, verbatim, and report what it bound.

    python run_python_browser_recipe.py <recipe_file> <start_sample> <end_sample>

This is the runner `tests/test_assistants/test_nemar_contract_live.py` invokes under
``uv run --isolated --no-project --with "eegprep-lean[zarr] @ file://<wheel>"``, so it
runs with NOTHING from this repository's own dependencies: only the standard library
and whatever the vendored wheel installs. Nothing here may import anything else.

The recipe's own source is never touched: it is a NEMAR MCP `read_window` response's
``recipe.how_to.python_browser`` field, written for a runtime whose top level is
already async (Pyodide's exec harness) and so uses top-level ``await`` with no
wrapping function. `compile` with ``ast.PyCF_ALLOW_TOP_LEVEL_AWAIT`` accepts that
unchanged. When the source awaits, ``eval()`` returns a coroutine, which
``asyncio.run()`` runs; ``exec()`` would discard it unawaited. When it does not,
``eval()`` runs it directly.

Prints one JSON object: the shape, dtype and unit of whichever of `window` (the
physical read, `read_window`/`plot_window` recipes) or `digital` (the raw-counts
read, `open_array`/`getitem` recipes) the recipe bound. Exits non-zero, with the
traceback on stderr, if the recipe raised, and with 3 if it bound neither.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import sys
from pathlib import Path


def _describe(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    # A read_window Window wraps its array in .data; a bare array is described as it
    # is, since an ndarray's own .data is a memoryview with no dtype.
    data = value if hasattr(value, "shape") else getattr(value, "data", value)
    shape = getattr(data, "shape", None)
    description: dict[str, object] = {
        "shape": list(shape) if shape is not None else None,
        "dtype": str(getattr(data, "dtype", "")),
    }
    unit = getattr(value, "unit", None)
    if unit is not None:
        description["unit"] = unit
    return description


def main(argv: list[str]) -> int:
    recipe_file, start_arg, end_arg = argv[0], argv[1], argv[2]
    code = Path(recipe_file).read_text(encoding="utf-8")

    namespace: dict[str, object] = {
        "start_sample": int(start_arg),
        "end_sample": int(end_arg),
        "__name__": "__recipe__",
    }
    compiled = compile(code, recipe_file, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    # A recipe with a top-level await evaluates to a coroutine to run; one without
    # runs as eval evaluates it and returns None.
    result = eval(compiled, namespace)  # noqa: S307 - a code object, not a string
    if inspect.iscoroutine(result):
        asyncio.run(result)

    report = {
        "window": _describe(namespace.get("window")),
        "digital": _describe(namespace.get("digital")),
    }
    print(json.dumps(report))
    if report["window"] is None and report["digital"] is None:
        # A recipe that ran and read nothing is not a working recipe.
        print("the recipe bound neither `window` nor `digital`", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
