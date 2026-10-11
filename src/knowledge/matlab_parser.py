"""MATLAB docstring parser using regex.

Extracts docstrings from MATLAB files for indexing and search.
Supports: functions (including nested), scripts, and classdef files, each with header comments.

MATLAB documentation conventions:
- Comments start with %
- Function help appears in the comment block after the function definition
  (MATLAB's own convention, used by FieldTrip and Brainstorm), or in the
  block before it (EEGLAB's convention)
- Script and class help appears at the top of the file
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: The first line of a license or copyright notice in a help block (without its ``%``):
#: Brainstorm's "@=====" banner, "Copyright (C) ...", "This file is part of ...".
_LICENSE_START = re.compile(
    r"^\s*(?:@=+|Copyright\b|This (?:file|function|program|software) is (?:part of|distributed))",
    re.IGNORECASE,
)

#: A line continued on the next with "...", whatever comment follows the dots.
_CONTINUED = re.compile(r"^[^%]*\.\.\.")


@dataclass
class MatlabDocstring:
    """Parsed MATLAB docstring with metadata."""

    symbol_name: str
    symbol_type: str  # 'function', or 'script' for a script's or class's header help
    docstring: str
    line_number: int


def parse_matlab_file(content: str, file_path: str) -> list[MatlabDocstring]:
    """Parse MATLAB file and extract all docstrings.

    Args:
        content: File content as string
        file_path: Path to the file (for module name extraction)

    Returns:
        List of extracted docstrings with metadata
    """
    results: list[MatlabDocstring] = []
    lines = content.split("\n")

    # Pattern to match function definitions
    # Matches:
    #   function [out1, out2] = name(in1, in2)  - multiple outputs
    #   function out = name(in1, in2)           - single output
    #   function name(in1, in2)                 - no outputs
    func_pattern = re.compile(r"^\s*function\s+(?:(?:\[[\w,\s]*\]|\w+)\s*=\s*)?(\w+)\s*\(")

    # Look for function definitions and their preceding comment blocks
    for i, line in enumerate(lines):
        match = func_pattern.match(line)
        if match:
            func_name = match.group(1)

            # Look backward for comment block (stop at first non-comment line)
            comments = []
            j = i - 1
            while j >= 0:
                stripped = lines[j].strip()
                if stripped == "%}":
                    # The end of a %{ ... %} block comment, skipped: in the files we
                    # index it holds a license, not help
                    break
                if stripped.startswith("%"):
                    # Remove comment marker and optional space
                    comment = re.sub(r"^\s*%+\s?", "", lines[j])
                    comments.insert(0, comment)
                    j -= 1
                elif not stripped:
                    # Allow empty lines in comment block
                    j -= 1
                else:
                    # Hit non-comment, non-empty line
                    break

            # Calculate the line where the comment block starts
            comment_start_line = i - len(comments) + 1
            # No help above the definition (no comments, or only blank or "%%%%"
            # separator lines): read the block after it
            if not any(c.strip() for c in comments):
                comments, comment_start_line = _help_after_definition(lines, i)

            if comments:
                # Found docstring for this function
                docstring = "\n".join(comments).strip()
                results.append(
                    MatlabDocstring(
                        symbol_name=func_name,
                        symbol_type="function",
                        docstring=docstring,
                        line_number=comment_start_line,
                    )
                )

    # The comment block at the top of a script or classdef file is the file's help (as
    # MATLAB's help shows it), whether or not its functions or methods have their own.
    # When the file starts with a function, that block is the function's, read above.
    script_comments = []
    first_code_line = ""
    first_code_index = -1
    in_block_comment = False
    in_license = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        # Skip %{ ... %} block comments: in the files we index they hold a license
        if in_block_comment or stripped == "%{":
            in_block_comment = stripped != "%}"
            continue
        if stripped.startswith("%"):
            comment = re.sub(r"^\s*%+\s?", "", line)
            # The help ends where a license or copyright notice starts
            in_license = in_license or bool(_LICENSE_START.match(comment))
            if not in_license:
                script_comments.append(comment)
        elif stripped:
            # Hit first non-comment, non-empty line
            first_code_line = line
            first_code_index = index
            break

    is_classdef = first_code_line.lstrip().startswith("classdef")
    if is_classdef and not any(c.strip() for c in script_comments):
        # A class's help may also follow its classdef line, as a function's does
        script_comments, _ = _help_after_definition(lines, first_code_index)

    if any(c.strip() for c in script_comments) and not func_pattern.match(first_code_line):
        script_name = _get_module_name(file_path)
        docstring = "\n".join(script_comments).strip()
        # A classdef constructor shares the file's name; the file's help describes the class
        results = [r for r in results if r.symbol_name != script_name]
        results.insert(
            0,
            MatlabDocstring(
                symbol_name=script_name,
                symbol_type="script",
                docstring=docstring,
                line_number=1,
            ),
        )

    return results


def _help_after_definition(lines: list[str], def_line: int) -> tuple[list[str], int]:
    """Read the help block that follows a function definition, as MATLAB's ``help`` does.

    The block is the first run of comment lines after the definition (after its
    ``...`` continuation lines and any blank lines, as FieldTrip leaves one). It
    ends at the first line that is not a comment, a blank line included, or at the
    start of a license or copyright notice, so the notice that Brainstorm and
    FieldTrip put after the help is left out. A block that is only a notice (a
    function with no help) is no help at all.

    Args:
        lines: The file's lines
        def_line: Index of the line the function definition starts on

    Returns:
        The comment lines without their ``%`` markers, and the 1-based line the
        block starts on
    """
    end = def_line
    while end + 1 < len(lines) and _CONTINUED.match(lines[end]):
        end += 1

    start = end + 1
    while start < len(lines) and not lines[start].strip():
        start += 1

    comments = []
    j = start
    while j < len(lines) and lines[j].strip().startswith("%"):
        comment = re.sub(r"^\s*%+\s?", "", lines[j])
        if _LICENSE_START.match(comment):
            break
        comments.append(comment)
        j += 1

    if not any(c.strip() for c in comments):
        return [], start + 1
    return comments, start + 1


def _get_module_name(file_path: str) -> str:
    """Extract module name from file path.

    Args:
        file_path: Path like 'functions/popfunc/pop_loadset.m'

    Returns:
        Module name like 'pop_loadset' (without extension)
    """
    import os

    return os.path.splitext(os.path.basename(file_path))[0]
