"""Python docstring parser using AST.

Extracts docstrings from Python files for indexing and search.
Supports: modules, functions, classes, and methods.
"""

import ast
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PythonDocstring:
    """Parsed Python docstring with metadata."""

    symbol_name: str
    symbol_type: str  # 'function', 'class', 'method', 'module'
    docstring: str
    line_number: int


def parse_python_file(content: str, file_path: str) -> list[PythonDocstring]:
    """Parse Python file and extract all docstrings.

    Args:
        content: File content as string
        file_path: Path to the file (for error reporting)

    Returns:
        List of extracted docstrings with metadata

    Raises:
        SyntaxError: If the Python file has syntax errors
    """
    results: list[PythonDocstring] = []

    # Let SyntaxError propagate to caller for proper error handling
    tree = ast.parse(content)

    # Build parent map once for efficient method detection
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    # Module-level docstring
    module_doc = ast.get_docstring(tree)
    if module_doc:
        results.append(
            PythonDocstring(
                symbol_name=_get_module_name(file_path),
                symbol_type="module",
                docstring=module_doc,
                line_number=1,
            )
        )

    # Walk the AST and extract docstrings from functions and classes
    for node in ast.walk(tree):
        # Only process nodes that can have docstrings
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            docstring = ast.get_docstring(node)
            if docstring:
                # Determine if it's a method or function using parent map
                parent = parents.get(node)
                symbol_type = "method" if isinstance(parent, ast.ClassDef) else "function"
                results.append(
                    PythonDocstring(
                        symbol_name=node.name,
                        symbol_type=symbol_type,
                        docstring=docstring,
                        line_number=node.lineno,
                    )
                )

        elif isinstance(node, ast.ClassDef):
            docstring = ast.get_docstring(node)
            if docstring:
                results.append(
                    PythonDocstring(
                        symbol_name=node.name,
                        symbol_type="class",
                        docstring=docstring,
                        line_number=node.lineno,
                    )
                )

    return results


def _get_module_name(file_path: str) -> str:
    """Extract module name from file path.

    Args:
        file_path: Path like 'mne/io/fiff/raw.py'

    Returns:
        Module name like 'raw' (without extension)
    """
    import os

    return os.path.splitext(os.path.basename(file_path))[0]
