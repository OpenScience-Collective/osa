"""The runtime wheel's Cache-Control string is one value, kept in three files.

A community's browser runtime wheels are served immutable (see
``get_runtime_wheel`` in ``src/api/routers/community.py``): the API route, the
worker in front of it (``workers/osa-worker/index.js``), and the browser
harness's local test server (``frontend/browser-harness/serve.js``), which has
to match or a warm-cache measurement there would prove nothing about what
ships. Neither JavaScript file can import the Python constant, so this test
is the drift guard: it reads all three literally and fails the moment any one
of them changes without the others.
"""

import re
from pathlib import Path

from src.api.routers.community import RUNTIME_WHEEL_CACHE_CONTROL

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_literal(path: Path, pattern: str) -> str:
    text = path.read_text()
    match = re.search(pattern, text)
    assert match, f"could not find a Cache-Control literal in {path} (pattern {pattern!r})"
    return match.group(1)


def test_the_worker_and_the_harness_server_match_the_api() -> None:
    worker = _find_literal(
        REPO_ROOT / "workers" / "osa-worker" / "index.js",
        r"const IMMUTABLE = '([^']+)';",
    )
    harness = _find_literal(
        REPO_ROOT / "frontend" / "browser-harness" / "serve.js",
        r"const RUNTIME_WHEEL_CACHE_CONTROL = '([^']+)';",
    )

    assert worker == RUNTIME_WHEEL_CACHE_CONTROL, (
        "workers/osa-worker/index.js's IMMUTABLE has drifted from "
        "src/api/routers/community.py's RUNTIME_WHEEL_CACHE_CONTROL "
        f"({worker!r} != {RUNTIME_WHEEL_CACHE_CONTROL!r})"
    )
    assert harness == RUNTIME_WHEEL_CACHE_CONTROL, (
        "frontend/browser-harness/serve.js's RUNTIME_WHEEL_CACHE_CONTROL has "
        "drifted from src/api/routers/community.py's "
        f"({harness!r} != {RUNTIME_WHEEL_CACHE_CONTROL!r})"
    )


def test_the_value_itself_is_immutable_for_a_year() -> None:
    # Pinned so a change to the shared value is a deliberate edit to this
    # test too, not a silent pass because the drift check above still agrees
    # with whatever the three files were changed to say together.
    assert RUNTIME_WHEEL_CACHE_CONTROL == "public, max-age=31536000, immutable"
