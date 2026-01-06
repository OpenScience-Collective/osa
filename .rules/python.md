# Python Development Standards

## Version & Environment
- **Python 3.11+** minimum (use latest stable)
- **Virtual Environment:** `conda` (preferred) or `venv`
- **Package Management:** `pip` with `pyproject.toml`

## Code Style
- **Formatter:** `ruff format` (Black-compatible)
- **Linter:** `ruff check` with aggressive fixes
- **Line Length:** 88 characters (Black standard)
- **Imports:** Sorted with `isort` (via ruff)

## Type Hints
- **Required for:** All public functions and methods
- **Tool:** `mypy` for type checking
- **Example:**
```python
def process_data(items: list[dict[str, Any]]) -> pd.DataFrame:
    """Process raw data into DataFrame."""
    ...
```

## Project Structure
```
project/
├── src/project/       # Source code
│   ├── __init__.py
│   └── module.py
├── tests/            # Real tests only
├── pyproject.toml    # Project config
└── .gitignore
```

## Common Patterns
- **Context Managers:** For resource management
- **Dataclasses:** For data structures
- **Pathlib:** For file operations (not os.path)
- **F-strings:** For string formatting

## Error Handling
```python
# Be specific with exceptions
try:
    result = risky_operation()
except SpecificError as e:
    logger.error(f"Operation failed: {e}")
    raise  # Re-raise or handle appropriately
```

## Documentation
- **Docstrings:** Google or NumPy style
- **Module docs:** At file top
- **Type hints:** Self-documenting code

---
*Follow PEP 8 with ruff enforcement. Real tests only.*
