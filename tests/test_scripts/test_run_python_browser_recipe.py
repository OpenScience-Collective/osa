"""scripts/run_python_browser_recipe.py, offline: what it reports for each kind of recipe.

The network test in tests/test_assistants/test_nemar_contract_live.py runs a live recipe
through it daily; these run small recipes in-process, so the runner's own contract is
checked in every CI sweep.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_python_browser_recipe import main


def _run(tmp_path: Path, code: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    recipe = tmp_path / "recipe.py"
    recipe.write_text(code)
    status = main([str(recipe), "0", "5"])
    return status, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_a_bare_array_is_described_with_its_dtype(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The v0.10.5 recipe binds `window` to the array getitem returns."""
    code = "import numpy as np\nwindow = np.zeros((4, end_sample - start_sample), dtype=np.int16)\n"

    status, report = _run(tmp_path, code, capsys)

    assert status == 0
    assert report["window"] == {"shape": [4, 5], "dtype": "int16"}
    assert report["digital"] is None


def test_a_physical_read_is_described_through_its_data_with_its_unit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = (
        "import numpy as np\n"
        "class Window:\n"
        "    def __init__(self, data, unit):\n"
        "        self.data, self.unit = data, unit\n"
        "window = Window(np.zeros((4, 5)), 'uV')\n"
        "digital = np.zeros((4, 5), dtype=np.int16)\n"
    )

    status, report = _run(tmp_path, code, capsys)

    assert status == 0
    assert report["window"] == {"shape": [4, 5], "dtype": "float64", "unit": "uV"}
    assert report["digital"] == {"shape": [4, 5], "dtype": "int16"}


def test_top_level_await_runs_as_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = (
        "import asyncio\nimport numpy as np\nawait asyncio.sleep(0)\ndigital = np.zeros((1, 5))\n"
    )

    status, report = _run(tmp_path, code, capsys)

    assert status == 0
    assert report["digital"]["shape"] == [1, 5]


def test_a_recipe_that_reads_nothing_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, report = _run(tmp_path, "x = 1\n", capsys)

    assert status == 3
    assert report == {"window": None, "digital": None}


def test_a_recipe_that_raises_propagates(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.py"
    recipe.write_text("raise ValueError('boom')\n")

    with pytest.raises(ValueError, match="boom"):
        main([str(recipe), "0", "5"])
