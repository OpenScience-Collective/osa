#!/usr/bin/env python3
"""Generate the SRI hash for the OSA chat widget.

Usage:
  # Hash the local file (useful during development / CI before a release is tagged)
  python scripts/widget-sri.py

  # Hash a specific release from jsDelivr (useful after tagging)
  python scripts/widget-sri.py v0.8.3

The script prints the sha384 integrity value and a ready-to-paste embed snippet.
"""

import base64
import hashlib
import sys
import urllib.request
from pathlib import Path


def sri(content: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(content).digest()).decode()


def main() -> None:
    if len(sys.argv) > 1:
        tag = sys.argv[1] if sys.argv[1].startswith("v") else f"v{sys.argv[1]}"
        url = (
            f"https://cdn.jsdelivr.net/gh/OpenScience-Collective/osa@{tag}"
            "/frontend/osa-chat-widget.js"
        )
        print(f"Fetching {url} …", flush=True)
        with urllib.request.urlopen(url) as resp:  # noqa: S310 (trusted CDN URL)
            content = resp.read()
        label = f"jsDelivr {tag}"
    else:
        widget_path = Path(__file__).parent.parent / "frontend" / "osa-chat-widget.js"
        if not widget_path.exists():
            print(f"Error: {widget_path} not found.", file=sys.stderr)
            print("Pass a version tag to fetch from jsDelivr instead:", file=sys.stderr)
            print("  python scripts/widget-sri.py v0.8.3", file=sys.stderr)
            sys.exit(1)
        content = widget_path.read_bytes()
        tag = None
        label = f"local ({widget_path.name})"

    hash_value = sri(content)
    print(f"\nSRI hash ({label}):")
    print(f"  integrity=\"{hash_value}\"")

    if tag:
        print(f"\nVersioned embed snippet for {tag}:")
        print(f"""\
  <script src="https://cdn.jsdelivr.net/gh/OpenScience-Collective/osa@{tag}/frontend/osa-chat-widget.js"
          integrity="{hash_value}"
          crossorigin="anonymous"
          defer></script>""")


if __name__ == "__main__":
    main()
