# Task Completion Checklist

When a development task is completed, follow these steps:

## 1. Format and Lint
```bash
ruff check --fix --unsafe-fixes .
ruff format .
```

## 2. Type Checking (if applicable)
```bash
mypy src/
```

## 3. Run Tests
```bash
# Run all tests with coverage
pytest --cov

# Check coverage threshold (>70%)
# Review coverage report for missing lines
```

## 4. Pre-commit Hooks
If pre-commit is installed:
```bash
pre-commit run --all-files
```

## 5. Git Operations (if applicable)
### For features requiring commits:
```bash
# Check status
git status

# Stage changes selectively
git add -p  # or git add <files>

# Commit with proper message format
git commit -m "<type>: <description>"
# Types: feat, fix, docs, refactor, test, chore

# Push if needed
git push
```

### Commit Message Rules:
- Format: `<type>: <description>`
- Length: <50 characters
- NO emojis
- NO co-author mentions
- Be concise and descriptive

## 6. For Pull Requests
```bash
# Create PR using gh CLI
gh pr create

# PR should include:
# - Clear, concise title (no emojis)
# - Description with "Fixes #123" if applicable
# - Test results
# - Use [x] for completed tasks, not emojis
```

## 7. Documentation Updates (if needed)
- Update relevant .md files in .context/ if architecture changed
- Update CLAUDE.md if quick start changed
- Update README.md if installation/usage changed

## Quick Command Reference
```bash
# Full check before commit
ruff check --fix --unsafe-fixes . && ruff format . && pytest --cov

# If all pass, commit
git add . && git commit -m "type: description"
```

## Notes
- Make atomic commits (one logical change per commit)
- Test before committing
- Ensure each commit works independently
- Commit frequently to track progress
