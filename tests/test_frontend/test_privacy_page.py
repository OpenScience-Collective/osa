"""PRIVACY.md names the controls the widget actually shows (#706).

The privacy page is where the widget's Privacy choices dialog links by default, so a
control renamed in the widget and not on the page leaves readers a page that describes a
control they cannot find. This reads the dialog's real labels from the widget source and
requires the page to use each.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
WIDGET = (ROOT / "frontend" / "osa-chat-widget.js").read_text(encoding="utf-8")


def _dialog_labels() -> list[str]:
    """The text of each checkbox label and the title in the Privacy choices dialog."""
    start = WIDGET.index("function privacyOverlayHtml()")
    end = WIDGET.index("function validPrivacyUrl", start)
    dialog = WIDGET[start:end]
    labels = re.findall(r"<label for=\"osa-privacy-[a-z]+\">([^<]+)</label>", dialog)
    title = re.findall(r"id=\"osa-privacy-title\">([^<]+)<", dialog)
    return [*title, *labels]


def test_the_page_uses_every_label_the_dialog_shows() -> None:
    labels = _dialog_labels()
    assert len(labels) == 3, labels
    for label in labels:
        assert f'"{label}"' in PAGE, f"PRIVACY.md does not name the control {label!r}"


def test_the_page_no_longer_describes_the_footer_checkbox() -> None:
    assert "Share page URL" not in PAGE
