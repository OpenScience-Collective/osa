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

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return results

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
                # Determine if it's a method or function
                symbol_type = _get_function_type(node, tree)
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


def _get_function_type(func_node: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.Module) -> str:
    """Determine if a function is a method or standalone function.

    Args:
        func_node: The FunctionDef or AsyncFunctionDef node
        tree: The module AST

    Returns:
        'method' if the function is inside a class, otherwise 'function'
    """
    # Check if this function is directly inside a ClassDef
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if item is func_node:
                    return "method"
    return "function"
