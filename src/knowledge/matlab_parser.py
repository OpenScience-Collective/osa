"""MATLAB docstring parser using regex.

Extracts docstrings from MATLAB files for indexing and search.
Supports: functions (including nested) and scripts with header comments.

MATLAB documentation conventions:
- Comments start with %
- Function help appears in comment block before the function definition
- Script help appears at the top of the file
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MatlabDocstring:
    """Parsed MATLAB docstring with metadata."""

    symbol_name: str
    symbol_type: str  # 'function' or 'script'
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

            if comments:
                # Found docstring for this function
                docstring = "\n".join(comments).strip()
                # Calculate the line where the comment block starts
                comment_start_line = i - len(comments) + 1
                results.append(
                    MatlabDocstring(
                        symbol_name=func_name,
                        symbol_type="function",
                        docstring=docstring,
                        line_number=comment_start_line,
                    )
                )

    # If no functions found, check for script header comments
    if not results:
        script_comments = []
        for _i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("%"):
                comment = re.sub(r"^\s*%+\s?", "", line)
                script_comments.append(comment)
            elif stripped:
                # Hit first non-comment, non-empty line
                break

        if script_comments:
            # This is a script with header documentation
            script_name = _get_module_name(file_path)
            docstring = "\n".join(script_comments).strip()
            results.append(
                MatlabDocstring(
                    symbol_name=script_name,
                    symbol_type="script",
                    docstring=docstring,
                    line_number=1,
                )
            )

    return results


def _get_module_name(file_path: str) -> str:
    """Extract module name from file path.

    Args:
        file_path: Path like 'functions/popfunc/pop_loadset.m'

    Returns:
        Module name like 'pop_loadset' (without extension)
    """
    import os

    return os.path.splitext(os.path.basename(file_path))[0]
