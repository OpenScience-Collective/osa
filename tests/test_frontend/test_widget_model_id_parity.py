"""The widget's model id check and the server's are the same check (issue #552).

The widget's Settings dialog refused `openai/gpt-oss-120b:nitro`, an OpenRouter slug the
server accepts, because `isValidModelId` had no `:variant` in it. These read the widget as
text, like `test_widget_drift.py`, so they run in the pytest job whether or not the Bun
frontend job does: the widget's pattern and length limit are the server's, and the widget's
own pattern classifies the shared corpus the way the server's does.
"""

import json
import re
from pathlib import Path

import pytest

from src.core.config.community import _MODEL_ID_MAX_LENGTH, _MODEL_ID_PATTERN

REPO_ROOT = Path(__file__).resolve().parents[2]
WIDGET = (REPO_ROOT / "frontend" / "osa-chat-widget.js").read_text()
CORPUS = json.loads((REPO_ROOT / "tests" / "fixtures" / "model_ids.json").read_text())


def _widget_pattern_source() -> str:
    match = re.search(r"const MODEL_ID_PATTERN = /(.+)/;", WIDGET)
    assert match, "the widget declares MODEL_ID_PATTERN as a regular expression literal"
    return match.group(1)


def _widget_max_length() -> int:
    match = re.search(r"const MODEL_ID_MAX_LENGTH = (\d+);", WIDGET)
    assert match, "the widget declares MODEL_ID_MAX_LENGTH"
    return int(match.group(1))


def test_the_widget_pattern_is_the_servers() -> None:
    """Same source, apart from the escaped slash a JavaScript literal needs."""
    assert _widget_pattern_source().replace("\\/", "/") == _MODEL_ID_PATTERN.pattern


def test_the_widget_length_limit_is_the_servers() -> None:
    assert _widget_max_length() == _MODEL_ID_MAX_LENGTH


def _widget_accepts(model_id: str) -> bool:
    """The widget's own rule, read from its source: no empty id, no id over the limit."""
    pattern = re.compile(_widget_pattern_source().replace("\\/", "/"))
    return (
        bool(model_id) and len(model_id) <= _widget_max_length() and bool(pattern.match(model_id))
    )


@pytest.mark.parametrize("model_id", CORPUS["valid"])
def test_the_widget_accepts_every_valid_id(model_id: str) -> None:
    assert _widget_accepts(model_id)


@pytest.mark.parametrize("model_id", CORPUS["invalid"])
def test_the_widget_refuses_every_invalid_id(model_id: str) -> None:
    assert not _widget_accepts(model_id)


def test_the_saved_model_and_the_custom_field_use_the_one_check() -> None:
    """Both places a model name enters the widget go through isValidModelId."""
    assert WIDGET.count("isValidModelId(") >= 3, "the definition and its two call sites"
