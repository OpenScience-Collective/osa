"""Known procedures: validated at load from real files, and every shipped community's own.

A community declares ``procedures`` (``ProcedureConfig``), each a Markdown document beside
its ``config.yaml``. ``CommunityConfig.from_yaml`` is where the folder is known, so it is
where a missing, escaping, oversize or blank document is refused, in plain words. The
shipped communities are loaded dynamically: nothing here names NEMAR's procedures.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.core.config.community import (
    MAX_PROCEDURE_CHARS,
    MAX_PROCEDURES,
    CommunityConfig,
)

ASSISTANTS_DIR = Path(__file__).resolve().parents[3] / "src" / "assistants"
SHIPPED_CONFIGS = sorted(ASSISTANTS_DIR.glob("*/config.yaml"))

DOCUMENT = "# Plan\n\n1. **Do the thing**: one run.\n"
PYTHON_FENCE = re.compile(r"^( *)```python\n([\s\S]*?)\n\1```", re.MULTILINE)


def _entry(**overrides: str) -> dict[str, str]:
    entry = {
        "id": "do-the-thing",
        "title": "Do the thing",
        "when": "A request to do the thing, in one line",
        "file": "procedures/do-the-thing.md",
    }
    return entry | overrides


def _write(folder: Path, procedures: list[dict[str, str]], files: dict[str, str | bytes]) -> Path:
    """A community folder with a config.yaml declaring ``procedures`` and the given files."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    config = {
        "id": "demo",
        "name": "Demo",
        "description": "A demo community",
        "procedures": procedures,
    }
    path = folder / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "demo"


class TestAValidEntry:
    def test_loads_and_holds_the_text_in_memory(self, folder: Path) -> None:
        path = _write(folder, [_entry()], {"procedures/do-the-thing.md": DOCUMENT})
        config = CommunityConfig.from_yaml(path)
        assert [p.id for p in config.procedures] == ["do-the-thing"]
        assert config.procedure_documents() == {"do-the-thing": DOCUMENT}

    def test_the_text_is_read_once_at_load(self, folder: Path) -> None:
        path = _write(folder, [_entry()], {"procedures/do-the-thing.md": DOCUMENT})
        config = CommunityConfig.from_yaml(path)
        (folder / "procedures" / "do-the-thing.md").write_text("changed afterwards")
        assert config.procedure_documents() == {"do-the-thing": DOCUMENT}

    def test_a_community_without_procedures_has_none(self, folder: Path) -> None:
        path = _write(folder, [], {})
        config = CommunityConfig.from_yaml(path)
        assert config.procedures == []
        assert config.procedure_documents() == {}

    def test_declaration_order_is_kept(self, folder: Path) -> None:
        entries = [
            _entry(id="second-one", file="procedures/b.md"),
            _entry(id="first-one", file="procedures/a.md"),
        ]
        path = _write(folder, entries, {"procedures/a.md": DOCUMENT, "procedures/b.md": DOCUMENT})
        config = CommunityConfig.from_yaml(path)
        assert list(config.procedure_documents()) == ["second-one", "first-one"]

    def test_a_document_at_the_size_bound_loads(self, folder: Path) -> None:
        text = "x" * (MAX_PROCEDURE_CHARS - 1) + "\n"
        path = _write(folder, [_entry()], {"procedures/do-the-thing.md": text})
        assert len(CommunityConfig.from_yaml(path).procedure_documents()["do-the-thing"]) == len(
            text
        )

    def test_a_config_built_without_from_yaml_refuses_to_serve_procedures(self) -> None:
        config = CommunityConfig(
            id="demo", name="Demo", description="A demo community", procedures=[_entry()]
        )
        with pytest.raises(ValueError, match="documents were not loaded"):
            config.procedure_documents()


class TestAFailureAtLoad:
    def test_a_missing_file(self, folder: Path) -> None:
        path = _write(folder, [_entry()], {})
        with pytest.raises(ValueError, match=r"do-the-thing.*does not exist"):
            CommunityConfig.from_yaml(path)

    def test_an_absolute_path(self, folder: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside.md"
        outside.write_text(DOCUMENT)
        path = _write(folder, [_entry(file=str(outside))], {})
        with pytest.raises(ValidationError, match="relative to the community's folder"):
            CommunityConfig.from_yaml(path)

    def test_a_dot_dot_segment(self, folder: Path, tmp_path: Path) -> None:
        (tmp_path / "outside.md").write_text(DOCUMENT)
        path = _write(folder, [_entry(file="../outside.md")], {})
        with pytest.raises(ValidationError, match="no empty, '.' or '..' segments"):
            CommunityConfig.from_yaml(path)

    def test_a_symlink_resolving_outside_the_folder(self, folder: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside.md"
        outside.write_text(DOCUMENT)
        path = _write(folder, [_entry()], {})
        link = folder / "procedures" / "do-the-thing.md"
        link.parent.mkdir(parents=True)
        os.symlink(outside, link)
        with pytest.raises(ValueError, match="resolves outside the community's folder"):
            CommunityConfig.from_yaml(path)

    def test_a_symlink_to_a_file_inside_the_folder_is_fine(self, folder: Path) -> None:
        path = _write(folder, [_entry()], {"real/document.md": DOCUMENT})
        link = folder / "procedures" / "do-the-thing.md"
        link.parent.mkdir(parents=True)
        os.symlink(folder / "real" / "document.md", link)
        assert CommunityConfig.from_yaml(path).procedure_documents() == {"do-the-thing": DOCUMENT}

    def test_a_non_markdown_file(self, folder: Path) -> None:
        path = _write(
            folder, [_entry(file="procedures/do-the-thing.txt")], {"procedures/do-the-thing.txt": "x"}
        )
        with pytest.raises(ValidationError, match="it must be a .md file"):
            CommunityConfig.from_yaml(path)

    def test_a_directory_named_like_a_document(self, folder: Path) -> None:
        path = _write(folder, [_entry()], {"procedures/do-the-thing.md/inner.md": DOCUMENT})
        with pytest.raises(ValueError, match="does not exist"):
            CommunityConfig.from_yaml(path)

    def test_a_duplicate_id(self, folder: Path) -> None:
        entries = [_entry(), _entry(file="procedures/other.md")]
        path = _write(
            folder, entries, {"procedures/do-the-thing.md": DOCUMENT, "procedures/other.md": DOCUMENT}
        )
        with pytest.raises(ValidationError, match="'do-the-thing' is declared twice"):
            CommunityConfig.from_yaml(path)

    @pytest.mark.parametrize("bad", ["Power Spectrum", "power_spectrum", "-lead", "trail-", "a--b", ""])
    def test_a_non_slug_id(self, folder: Path, bad: str) -> None:
        path = _write(folder, [_entry(id=bad)], {"procedures/do-the-thing.md": DOCUMENT})
        with pytest.raises(ValidationError, match="procedure id"):
            CommunityConfig.from_yaml(path)

    @pytest.mark.parametrize("blank", ["", "  \n\t \n"])
    def test_a_blank_document(self, folder: Path, blank: str) -> None:
        path = _write(folder, [_entry()], {"procedures/do-the-thing.md": blank})
        with pytest.raises(ValueError, match="is blank"):
            CommunityConfig.from_yaml(path)

    def test_an_oversize_document(self, folder: Path) -> None:
        path = _write(
            folder, [_entry()], {"procedures/do-the-thing.md": "x" * (MAX_PROCEDURE_CHARS + 1)}
        )
        with pytest.raises(ValueError, match="is longer than 32,000 characters"):
            CommunityConfig.from_yaml(path)

    def test_an_oversize_document_of_wide_characters(self, folder: Path) -> None:
        """The bound is in characters, not bytes: a document of two-byte characters that is
        over the bound by characters is refused, and one under it by characters is not."""
        over = "é" * (MAX_PROCEDURE_CHARS + 1)
        path = _write(folder, [_entry()], {"procedures/do-the-thing.md": over})
        with pytest.raises(ValueError, match="is longer than"):
            CommunityConfig.from_yaml(path)
        under = "é" * MAX_PROCEDURE_CHARS
        path = _write(folder, [_entry()], {"procedures/do-the-thing.md": under})
        assert CommunityConfig.from_yaml(path).procedure_documents() == {"do-the-thing": under}

    def test_a_document_that_is_not_utf8(self, folder: Path) -> None:
        path = _write(folder, [_entry()], {"procedures/do-the-thing.md": b"\xff\xfe\x00bad"})
        with pytest.raises(ValueError, match="not valid UTF-8"):
            CommunityConfig.from_yaml(path)

    @pytest.mark.parametrize("when", ["", "short", "two\nlines of text here", "x" * 301])
    def test_a_blank_multi_line_or_oversize_when(self, folder: Path, when: str) -> None:
        path = _write(folder, [_entry(when=when)], {"procedures/do-the-thing.md": DOCUMENT})
        with pytest.raises(ValidationError, match="procedure when"):
            CommunityConfig.from_yaml(path)

    @pytest.mark.parametrize("title", ["", "ab", "two\nlines", "x" * 81])
    def test_a_blank_multi_line_or_oversize_title(self, folder: Path, title: str) -> None:
        path = _write(folder, [_entry(title=title)], {"procedures/do-the-thing.md": DOCUMENT})
        with pytest.raises(ValidationError, match="procedure title"):
            CommunityConfig.from_yaml(path)

    def test_more_than_the_most_procedures(self, folder: Path) -> None:
        entries = [
            _entry(id=f"procedure-{n}", file=f"procedures/{n}.md") for n in range(MAX_PROCEDURES + 1)
        ]
        files = {f"procedures/{n}.md": DOCUMENT for n in range(MAX_PROCEDURES + 1)}
        path = _write(folder, entries, files)
        with pytest.raises(ValidationError, match=f"at most {MAX_PROCEDURES}"):
            CommunityConfig.from_yaml(path)

    def test_an_unknown_key(self, folder: Path) -> None:
        path = _write(
            folder, [_entry() | {"url": "https://example.org"}], {"procedures/do-the-thing.md": DOCUMENT}
        )
        with pytest.raises(ValidationError, match="url"):
            CommunityConfig.from_yaml(path)


def _shipped_procedures() -> list[tuple[str, str, str]]:
    """Every (community, procedure id, config path) a shipped config declares."""
    found = []
    for config_path in SHIPPED_CONFIGS:
        config = CommunityConfig.from_yaml(config_path)
        found.extend((config.id, p.id, str(config_path)) for p in config.procedures)
    return found


@pytest.mark.parametrize("config_path", SHIPPED_CONFIGS, ids=lambda p: p.parent.name)
class TestEveryShippedCommunity:
    def test_its_procedures_load(self, config_path: Path) -> None:
        config = CommunityConfig.from_yaml(config_path)
        documents = config.procedure_documents()
        assert list(documents) == [p.id for p in config.procedures]
        for procedure in config.procedures:
            on_disk = (config_path.parent / procedure.file).read_text(encoding="utf-8")
            assert documents[procedure.id] == on_disk

    def test_each_document_opens_with_a_plan_section(self, config_path: Path) -> None:
        config = CommunityConfig.from_yaml(config_path)
        for procedure_id, text in config.procedure_documents().items():
            headings = [line for line in text.splitlines() if line.startswith("#")]
            assert len(headings) >= 2, procedure_id
            assert headings[0].startswith("# "), procedure_id
            assert headings[1].lower().startswith("## plan"), (procedure_id, headings[:2])

    def test_each_documents_plan_lists_numbered_steps(self, config_path: Path) -> None:
        config = CommunityConfig.from_yaml(config_path)
        for procedure_id, text in config.procedure_documents().items():
            plan = text.split("## Plan", 1)[1].split("\n## ", 1)[0]
            titles = re.findall(r"^\d+\. \*\*(.+?)\*\*", plan, flags=re.MULTILINE)
            assert titles, procedure_id
            numbers = re.findall(r"^(\d+)\. \*\*", plan, flags=re.MULTILINE)
            assert numbers == [str(n) for n in range(1, len(numbers) + 1)], procedure_id

    def test_each_documents_code_fences_parse(self, config_path: Path) -> None:
        """Top-level ``await`` is how the browser runtime runs a block, so it is allowed."""
        config = CommunityConfig.from_yaml(config_path)
        for procedure_id, text in config.procedure_documents().items():
            fences = PYTHON_FENCE.findall(text)
            for n, (_, body) in enumerate(fences, start=1):
                compile(
                    body,
                    f"{procedure_id} block {n}",
                    "exec",
                    flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT | ast.PyCF_ONLY_AST,
                )

    def test_ids_are_unique_across_the_config(self, config_path: Path) -> None:
        config = CommunityConfig.from_yaml(config_path)
        ids = [p.id for p in config.procedures]
        assert len(ids) == len(set(ids))


def test_at_least_one_shipped_community_declares_procedures() -> None:
    assert _shipped_procedures(), "no shipped community declares procedures"
