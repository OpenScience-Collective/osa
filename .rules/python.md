# Python Development Standards

## Version & Environment
- **Python 3.11+** minimum (use latest stable)
- **Package Manager:** UV only (not pip, conda, or virtualenv)
- **Virtual Environment:** Managed by UV (`uv venv`, `uv sync`)
- **Project Config:** `pyproject.toml` (no requirements.txt)

## Quick Reference
```bash
# Sync this project's environment
uv sync

# Add dependencies
uv add requests pandas

# Add dev dependencies
uv add --dev pytest ruff

# Run commands in venv
uv run pytest
uv run osa --help

# Re-sync after pulling dependency changes
uv sync
```

## Code Style
- **Formatter:** `ruff format` (Black-compatible)
- **Linter:** `ruff check --fix --unsafe-fixes`
- **Type Checker:** the global policy direction is `ty`, not `mypy` - but as of
  this writing `pyproject.toml` still lists `mypy>=1.19.0` as a dev
  dependency with a full `[tool.mypy]` config block, `ty` isn't present
  anywhere in the repo (not in `pyproject.toml`, `uv.lock`, the pre-commit
  config, or any CI workflow), and CI doesn't actually run either mypy or ty
  today (only `ruff`). Don't assume `ty` is in use just because this file
  says it's the target - check `pyproject.toml` first, and see issue #412
  for the actual migration.
- **Line Length:** 100 characters (`[tool.ruff] line-length = 100` in
  `pyproject.toml` - not the Black default of 88)
- **Imports:** Sorted by ruff (isort-compatible)

## Type Hints
- **Required for:** All public functions and methods
- **Example:**
```python
def process_data(items: list[dict[str, Any]]) -> pd.DataFrame:
    """Process raw data into DataFrame."""
    ...
```

## Project Structure
```
src/
├── api/              # FastAPI backend
├── cli/              # Typer CLI
├── agents/           # LangGraph agents
├── core/services/    # Business logic
├── tools/            # Document retrieval tools
tests/                # Real tests only
pyproject.toml        # Project config (UV + ruff; still mypy pending #412)
```
(See AGENTS.md "Project Structure" for the full layout.)

## Pre-commit Hook
Installed per the global instructions: a ruff hook with `--fix
--unsafe-fixes`, scoped to staged files only.
```bash
#!/bin/bash
# .git/hooks/pre-commit (or via the pre-commit framework)
files=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$')
if [ -n "$files" ]; then
    uv run ruff check --fix --unsafe-fixes $files
    uv run ruff format $files
    git add $files
fi
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

# Never do this:
# except Exception:
#     pass  # Silent failure
```

## Never Do This
- Never use `pip install` directly; use `uv add` or `uv pip install`
- Never use `conda`, `virtualenv`, or `venv`; UV handles environments
- Don't introduce new `mypy`-specific config or lean further into it; the
  direction is `ty` (issue #412 tracks removing `mypy` - it's still in
  `pyproject.toml` today, so don't rip it out unilaterally either)
- Never use bare `except:` or `except Exception: pass`
- Never use `os.path`; use `pathlib.Path`
- Never commit `.env` files or hardcoded secrets

## Documentation
- **Docstrings:** Google or NumPy style
- **Module docs:** At file top
- **Type hints:** Self-documenting code

---
*UV for everything. Ruff for style. Ty for types (target - see #412). Real tests only.*
