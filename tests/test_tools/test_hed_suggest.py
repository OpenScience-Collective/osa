"""Tests for the suggest_hed_tags tool and how it runs the hed-suggest program.

No mocks. The argument and environment tests put a small real executable named
`hed-suggest` on PATH, run the real tool against it, and read back what the process
received. One more test runs the real hed-lsp program when a checkout of it exists
(HED_LSP_PATH, or the usual local path) and is skipped otherwise; the deployed image is
checked the same way by the Docker job in CI.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

from src.assistants.hed.tools import suggest_hed_tags

_LOCAL_CHECKOUT = Path("~/Documents/git/HED/hed-lsp").expanduser()


def _install_recording_program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a `hed-suggest` on PATH that echoes its arguments and environment as JSON.

    Returns the marker file the program creates each time it runs.
    """
    marker = tmp_path / "ran"
    program = tmp_path / "hed-suggest"
    program.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"open({str(marker)!r}, 'w').close()\n"
        "args = sys.argv[1:]\n"
        "queries = args[3:]\n"
        "print(json.dumps({'__args__': args, '__env__': sorted(os.environ),\n"
        "                  **{q: [q.upper()] for q in queries}}))\n"
    )
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    return marker


class TestTermsReachTheProgram:
    """What suggest_hed_tags hands to hed-suggest."""

    def test_terms_are_passed_after_the_options(self, tmp_path, monkeypatch):
        _install_recording_program(tmp_path, monkeypatch)

        result = suggest_hed_tags.invoke({"search_terms": ["button press", "flash"], "top_n": 4})

        assert result["__args__"] == ["--json", "--top", "4", "button press", "flash"]
        assert result["button press"] == ["BUTTON PRESS"]
        assert result["flash"] == ["FLASH"]

    def test_a_term_that_looks_like_an_option_is_not_passed(self, tmp_path, monkeypatch):
        """hed-suggest reads "--semantic" as a switch, so the tool must not forward it."""
        _install_recording_program(tmp_path, monkeypatch)

        result = suggest_hed_tags.invoke(
            {"search_terms": ["button press", "--semantic", "-s"], "top_n": 3}
        )

        assert result["__args__"] == ["--json", "--top", "3", "button press"]
        # Every term the caller sent still appears in the answer.
        assert result["--semantic"] == []
        assert result["-s"] == []
        assert result["button press"] == ["BUTTON PRESS"]

    def test_only_option_like_terms_do_not_run_the_program(self, tmp_path, monkeypatch):
        marker = _install_recording_program(tmp_path, monkeypatch)

        result = suggest_hed_tags.invoke({"search_terms": ["--semantic", "-x"]})

        assert result == {"--semantic": [], "-x": []}
        assert not marker.exists()


class TestProgramEnvironment:
    def test_the_program_gets_path_and_none_of_the_servers_secrets(self, tmp_path, monkeypatch):
        _install_recording_program(tmp_path, monkeypatch)
        monkeypatch.setenv("OSA_TEST_SECRET", "do-not-leak")
        monkeypatch.setenv("HOME", str(tmp_path))

        result = suggest_hed_tags.invoke({"search_terms": ["press"]})

        names = set(result["__env__"])
        assert "PATH" in names
        assert "OSA_TEST_SECRET" not in names
        assert "HOME" not in names


class TestProgramNotInstalled:
    def test_missing_program_reports_unavailable_with_every_term(self, tmp_path, monkeypatch):
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))
        monkeypatch.delenv("HED_LSP_PATH", raising=False)
        # The tool also looks under ~/Documents/git/HED/hed-lsp; point that at nothing.
        monkeypatch.setenv("HOME", str(tmp_path))

        result = suggest_hed_tags.invoke({"search_terms": ["press", "-x"]})

        assert "not available" in result["error"]
        assert result["press"] == []
        assert result["-x"] == []


def _real_hed_lsp_root() -> Path | None:
    """A checkout of hed-lsp with its server built, if this machine has one."""
    candidates = [os.environ.get("HED_LSP_PATH"), str(_LOCAL_CHECKOUT)]
    for candidate in candidates:
        if candidate and (Path(candidate) / "server" / "out" / "cli.js").exists():
            return Path(candidate)
    return None


@pytest.mark.skipif(
    shutil.which("node") is None or _real_hed_lsp_root() is None,
    reason="needs node and a built hed-lsp checkout (HED_LSP_PATH); CI checks the image instead",
)
def test_real_program_suggests_a_schema_tag(monkeypatch):
    """The real hed-suggest, reached through HED_LSP_PATH, answers "button press"."""
    root = _real_hed_lsp_root()
    assert root is not None
    monkeypatch.setenv("HED_LSP_PATH", str(root))

    result = suggest_hed_tags.invoke({"search_terms": ["button press"], "top_n": 10})

    assert "error" not in result, result
    # "Press" is a tag HED 8.4.0 defines; the keyword map also returns some names it does not.
    assert "Press" in result["button press"]
