"""Tests for Python docstring parser."""

import pytest

from src.knowledge.python_parser import parse_python_file


def test_parse_module_docstring():
    """Test extraction of module-level docstring."""
    code = '''"""Module docstring for test file."""

def foo():
    pass
'''
    results = parse_python_file(code, "test.py")

    assert len(results) == 1
    assert results[0].symbol_name == "test"
    assert results[0].symbol_type == "module"
    assert results[0].docstring == "Module docstring for test file."
    assert results[0].line_number == 1


def test_parse_function_docstring():
    """Test extraction of function docstring."""
    code = '''
def my_function(x, y):
    """Add two numbers.

    Args:
        x: First number
        y: Second number

    Returns:
        Sum of x and y
    """
    return x + y
'''
    results = parse_python_file(code, "test.py")

    assert len(results) == 1
    assert results[0].symbol_name == "my_function"
    assert results[0].symbol_type == "function"
    assert "Add two numbers" in results[0].docstring
    assert results[0].line_number > 0


def test_parse_class_docstring():
    """Test extraction of class docstring."""
    code = '''
class MyClass:
    """A test class.

    This class does useful things.
    """

    def __init__(self):
        pass
'''
    results = parse_python_file(code, "test.py")

    assert len(results) == 1
    assert results[0].symbol_name == "MyClass"
    assert results[0].symbol_type == "class"
    assert "A test class" in results[0].docstring


def test_parse_method_docstring():
    """Test extraction of method docstrings."""
    code = '''
class Calculator:
    """Calculator class."""

    def add(self, x, y):
        """Add two numbers."""
        return x + y

    def subtract(self, x, y):
        """Subtract y from x."""
        return x - y
'''
    results = parse_python_file(code, "test.py")

    # Should find class and 2 methods
    assert len(results) == 3

    # Check class
    class_doc = [r for r in results if r.symbol_type == "class"][0]
    assert class_doc.symbol_name == "Calculator"

    # Check methods
    method_docs = [r for r in results if r.symbol_type == "method"]
    assert len(method_docs) == 2
    method_names = {m.symbol_name for m in method_docs}
    assert "add" in method_names
    assert "subtract" in method_names


def test_parse_async_function():
    """Test extraction of async function docstring."""
    code = '''
async def fetch_data(url):
    """Fetch data asynchronously.

    Args:
        url: The URL to fetch from
    """
    pass
'''
    results = parse_python_file(code, "test.py")

    assert len(results) == 1
    assert results[0].symbol_name == "fetch_data"
    assert results[0].symbol_type == "function"
    assert "Fetch data asynchronously" in results[0].docstring


def test_parse_no_docstrings():
    """Test file with no docstrings."""
    code = """
def foo():
    pass

class Bar:
    pass
"""
    results = parse_python_file(code, "test.py")

    assert len(results) == 0


def test_parse_mixed_documented_undocumented():
    """Test file with some documented and some undocumented items."""
    code = '''
"""Module doc."""

def documented():
    """This has a docstring."""
    pass

def undocumented():
    pass

class DocumentedClass:
    """Class with docstring."""
    pass

class UndocumentedClass:
    pass
'''
    results = parse_python_file(code, "test.py")

    # Should find module, 1 function, and 1 class
    assert len(results) == 3
    types = {r.symbol_type for r in results}
    assert types == {"module", "function", "class"}


def test_parse_syntax_error():
    """Test handling of syntax errors."""
    code = '''
def invalid syntax here:
    """This won't parse."""
    pass
'''
    # Should raise SyntaxError (not return empty list)
    with pytest.raises(SyntaxError):
        parse_python_file(code, "test.py")


def test_parse_nested_functions():
    """Test that nested functions are extracted."""
    code = '''
def outer():
    """Outer function."""

    def inner():
        """Inner function."""
        pass

    return inner
'''
    results = parse_python_file(code, "test.py")

    # ast.walk() will find both functions
    assert len(results) == 2
    names = {r.symbol_name for r in results}
    assert "outer" in names
    assert "inner" in names
