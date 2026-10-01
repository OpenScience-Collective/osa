"""The model ids the community config validator accepts and refuses (issue #552).

`tests/fixtures/model_ids.json` is the one list of valid and invalid ids; the widget's
Settings dialog is held to the same list in `frontend/test-widget-settings.js`, and
`tests/test_frontend/test_widget_model_id_parity.py` holds the widget's pattern to this
module's. An id one side accepts and the other refuses is a slug a reader cannot save,
or a config that loads and never runs.
"""

import json
from pathlib import Path

import pytest

from src.core.config.community import _MODEL_ID_MAX_LENGTH, _validate_model_id

CORPUS = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "model_ids.json").read_text()
)


@pytest.mark.parametrize("model_id", CORPUS["valid"])
def test_a_valid_id_is_accepted_unchanged(model_id: str) -> None:
    assert _validate_model_id(model_id) == model_id


@pytest.mark.parametrize("model_id", CORPUS["invalid"])
def test_an_invalid_id_is_refused(model_id: str) -> None:
    with pytest.raises(ValueError):
        _validate_model_id(model_id)


def test_surrounding_whitespace_is_trimmed_before_the_check() -> None:
    """The widget trims what a reader types, and the server strips: neither is in the corpus."""
    assert _validate_model_id("  openai/gpt-5\n") == "openai/gpt-5"


def test_the_corpus_covers_every_character_class_the_pattern_allows() -> None:
    """Dropping a class (upper case, underscore, dot, digit) from either side must fail."""
    valid = "".join(CORPUS["valid"])
    assert any(ch.isupper() for ch in valid)
    assert "_" in valid and "." in valid and any(ch.isdigit() for ch in valid)


def test_the_corpus_holds_the_ids_the_issue_is_about() -> None:
    """OpenRouter's routing variants and its :free entries, and the longest id allowed."""
    valid = set(CORPUS["valid"])
    assert {"openai/gpt-oss-120b:nitro", "openai/gpt-oss-120b:floor"} <= valid
    assert "qwen/qwen3-next-80b-a3b-instruct:free" in valid
    assert "openai/gpt-5.2:nitro:exacto" in valid, "OpenRouter lets variants be stacked"
    assert any(len(i) == _MODEL_ID_MAX_LENGTH for i in valid)
    assert any(len(i) == _MODEL_ID_MAX_LENGTH + 1 for i in CORPUS["invalid"])
