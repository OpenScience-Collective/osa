# Code Style & Conventions

## Formatter & Linter
- **Formatter**: `ruff format` (Black-compatible)
- **Linter**: `ruff check` with aggressive fixes
- **Line Length**: 100 characters (configured in pyproject.toml)
- **Target Version**: Python 3.11+

## Ruff Configuration
Enabled rules:
- E, W: pycodestyle errors and warnings
- F: Pyflakes
- I: isort (import sorting)
- B: flake8-bugbear
- C4: flake8-comprehensions
- UP: pyupgrade
- ARG: flake8-unused-arguments
- SIM: flake8-simplify

Ignored rules:
- E501: line too long (handled by formatter)
- B008: function calls in argument defaults
- B904: raise without from inside except

## Type Hints
- **Required**: All public functions and methods
- **Tool**: mypy for type checking
- **Style**: Use modern Python type hints (e.g., `list[str]` not `List[str]`)
- **Example**:
```python
def process_data(items: list[dict[str, Any]]) -> pd.DataFrame:
    """Process raw data into DataFrame."""
    ...
```

## Import Sorting
- First-party packages: `src`
- Sorted automatically by ruff/isort

## Common Patterns
- **Context Managers**: For resource management
- **Dataclasses/Pydantic**: For data structures
- **Pathlib**: For file operations (not os.path)
- **F-strings**: For string formatting
- **Async/await**: For async operations

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
- **Docstrings**: Google or NumPy style
- **Module docs**: At file top
- **Type hints**: Self-documenting code
- **Comments**: Only when logic isn't self-evident

## Naming Conventions
- **Files/Modules**: lowercase_with_underscores
- **Classes**: PascalCase
- **Functions/Variables**: lowercase_with_underscores
- **Constants**: UPPERCASE_WITH_UNDERSCORES
- **Private**: _leading_underscore

## Project-Specific Guidelines
- NO emojis in code, commits, or PRs
- NO mock tests - only real tests with real data
- Prefer simplicity over abstraction
- Follow existing patterns in the codebase
