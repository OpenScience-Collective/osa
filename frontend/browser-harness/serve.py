#!/usr/bin/env python3
"""Serve the browser runtime harness under a REAL Content-Security-Policy header.

The question this answers, from OSA #431 and the browser-execution design note:
nemar.org grants `wasm-unsafe-eval` globally but confines the strictly stronger
`'unsafe-eval'` to /dataset/*, because website ADR 0009 found wasm-unsafe-eval
alone was not enough to decode Zarr chunks. Whether Pyodide's own loader hits
the same wall has never been measured in a browser, and Bun and Node do not
enforce CSP so no test in the repository can answer it.

Two variants, identical except for the policy:

  /strict/   script-src 'self' 'wasm-unsafe-eval'   <- what nemar.org grants site-wide
  /loose/    script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'

If /strict/ boots Pyodide and runs code, the site-wide grant nemar.org already
has is sufficient and no new CSP decision is needed. If it does not, extending
'unsafe-eval' site-wide is a materially larger decision that needs its own ADR.

Usage: python3 serve.py [port]
"""

from __future__ import annotations

import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent.parent

CDN = "https://cdn.jsdelivr.net"

# Everything the runtime needs, held constant across both variants so the only
# difference is the eval grant itself.
COMMON = (
    f"default-src 'self'; "
    f"connect-src 'self' {CDN} https://zarr.nemar.org https://api.nemar.org; "
    f"worker-src 'self' blob:; "
    f"child-src 'self' blob:; "
    f"img-src 'self' data: blob:; "
    f"style-src 'self' 'unsafe-inline'; "
    f"form-action 'self'"
)

# nemar.org's PRODUCTION connect-src, verbatim as of website v0.2.16, plus the
# jsDelivr entry. Used to answer: can micropip install anything at all under the
# policy that actually ships? PyPI is not in it.
NEMAR_CONNECT = (
    "connect-src 'self' https://*.nemar.org https://cdn.jsdelivr.net "
    "https://widget.osc.earth https://develop-widget.osc.earth"
)
PYPI_CONNECT = NEMAR_CONNECT + " https://pypi.org https://files.pythonhosted.org"

_REST = (
    "default-src 'self'; worker-src 'self' blob:; child-src 'self' blob:; "
    "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; form-action 'self'"
)

POLICIES = {
    # Exactly what nemar.org serves today, plus wasm. Does micropip work?
    "nemarlike": f"script-src 'self' 'wasm-unsafe-eval' {CDN}; {NEMAR_CONNECT}; {_REST}",
    # Same, but PyPI reachable. Isolates "blocked by CSP" from "needs unsafe-eval".
    "withpypi": f"script-src 'self' 'wasm-unsafe-eval' {CDN}; {PYPI_CONNECT}; {_REST}",
    # PyPI reachable AND unsafe-eval granted. If a package works here but not in
    # withpypi, that package genuinely needs the stronger grant.
    "withpypi_eval": f"script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval' {CDN}; {PYPI_CONNECT}; {_REST}",
    "strict": f"script-src 'self' 'wasm-unsafe-eval' {CDN}; {COMMON}",
    "loose": f"script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval' {CDN}; {COMMON}",
    # A control: no wasm grant at all. Pyodide must fail here, which proves the
    # harness is actually applying the policy rather than reporting a pass by
    # accident. A spike whose negative control also passes has measured nothing.
    "control": f"script-src 'self' {CDN}; {COMMON}",
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def _variant(self) -> str | None:
        for name in POLICIES:
            if self.path.startswith(f"/{name}/"):
                return name
        return None

    def translate_path(self, path: str) -> str:
        variant = self._variant()
        if variant:
            path = path[len(variant) + 1 :] or "/"
        return super().translate_path(path)

    def end_headers(self) -> None:
        variant = self._variant()
        if variant:
            self.send_header("Content-Security-Policy", POLICIES[variant])
            self.send_header("X-CSP-Variant", variant)
        # Cross-origin isolation is deliberately NOT set. #431 says not to
        # require it, because it would constrain every embedding page, and the
        # cancellation design terminates the worker instead of relying on
        # SharedArrayBuffer.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write("%s\n" % (format % args))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"serving on http://127.0.0.1:{port}")
    for name in POLICIES:
        print(f"  http://127.0.0.1:{port}/{name}/")
    server.serve_forever()
