# Essential Development Commands

## Environment Setup
```bash
# Activate conda environment
source ~/miniconda3/etc/profile.d/conda.sh
conda activate osa

# Install in development mode
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

## Development Server
```bash
# Run FastAPI server with hot reload (port 38528 = OSA prod)
uvicorn src.api.main:app --reload --port 38528

# Access at: http://localhost:38528
# Health check: http://localhost:38528/health
```

## CLI Usage
```bash
# Show help
osa --help

# Interactive chat
osa chat

# Single question
osa ask "your question here"

# Start server
osa serve --port 38528
```

## Code Quality
```bash
# Format code
ruff format .

# Lint and fix
ruff check --fix .

# Lint with unsafe fixes
ruff check --fix --unsafe-fixes .

# Type check
mypy src/

# Combined: format, lint, fix
ruff check --fix --unsafe-fixes . && ruff format .
```

## Testing
```bash
# Run all tests with coverage
pytest --cov

# Run specific test file
pytest tests/test_agents/test_hed.py

# Skip slow tests
pytest -m "not slow"

# Skip integration tests
pytest -m "not integration"

# Run only LLM tests (requires OPENROUTER_API_KEY_FOR_TESTING)
pytest -m llm

# Verbose output
pytest -v

# Stop on first failure
pytest -x
```

## Pre-commit
```bash
# Run on all files
pre-commit run --all-files

# Run on staged files only
pre-commit run

# Update hooks
pre-commit autoupdate
```

## Git Workflow
```bash
# Create feature branch
git checkout -b feature/short-description

# Check status
git status

# Stage selectively
git add -p

# Commit (atomic, <50 chars, no emojis)
git commit -m "feat: add new feature"

# Push branch
git push -u origin feature/short-description

# Create PR
gh pr create

# View PR status
gh pr status

# Merge PR
gh pr merge
```

## Versioning (via scripts/bump_version.py)
```bash
# Bump patch version (0.1.0 -> 0.1.1)
python scripts/bump_version.py patch

# Bump minor version (0.1.0 -> 0.2.0)
python scripts/bump_version.py minor

# Bump major version (0.1.0 -> 1.0.0)
python scripts/bump_version.py major
```

## Conda Management
```bash
# List environments
conda env list

# Create new environment
conda create -n osa python=3.12 -y

# Activate environment
conda activate osa

# Deactivate
conda deactivate

# Remove environment
conda env remove -n osa
```

## Useful System Commands (macOS/Darwin)
```bash
# Get current date
date

# Find files
find . -name "*.py"

# Search in files (prefer using Grep tool in editor)
grep -r "search_term" src/

# List directory tree
tree -L 2 src/

# Disk usage
du -sh *

# Process info
ps aux | grep python
```

## Quick Checks Before Commit
```bash
# One-liner: format, lint, test
ruff check --fix --unsafe-fixes . && ruff format . && pytest --cov

# If passing, commit
git add . && git commit -m "type: description"
```
