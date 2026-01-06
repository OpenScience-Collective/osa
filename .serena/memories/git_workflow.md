# Git & Version Control Workflow

## Commit Message Format
```
<type>: <description>
```

### Types
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `refactor`: Code restructuring
- `test`: Adding tests (real tests only)
- `chore`: Maintenance tasks
- `ci`: CI/CD changes

### Rules
- Length: <50 characters
- NO emojis
- NO co-author mentions (especially not Claude/Anthropic)
- Be concise yet descriptive
- If tested, can mention in commit body (not header)

### Examples
```bash
✅ Good:
git commit -m "feat: add HED validation tool"
git commit -m "fix: handle missing schema gracefully"
git commit -m "docs: update installation steps"
git commit -m "test: add real HED annotation tests"

❌ Bad:
git commit -m "Add stuff"  # Too vague
git commit -m "✨ feat: add feature"  # Has emoji
git commit -m "This is a really long commit message that exceeds the character limit"  # Too long
```

## Branch Strategy
```bash
# Feature branches
git checkout -b feature/short-description

# Bugfix branches
git checkout -b fix/issue-description

# Examples
git checkout -b feature/bids-assistant
git checkout -b fix/streaming-timeout
```

Rules:
- No spaces in branch names (use hyphens)
- Keep names short and descriptive
- Delete branch after merge

## Atomic Commits
One logical change per commit:
```bash
# Stage selectively
git add -p  # Interactive staging

# Or stage specific files
git add src/agents/hed.py tests/test_agents/test_hed.py

# Commit
git commit -m "feat: add HED agent"
```

## Typical Workflow
```bash
# 1. Create feature branch
git checkout -b feature/new-thing

# 2. Make changes, test, format
ruff check --fix --unsafe-fixes . && ruff format . && pytest --cov

# 3. Stage and commit atomically
git add -p
git commit -m "feat: add new thing"

# 4. Push branch
git push -u origin feature/new-thing

# 5. Create PR
gh pr create

# 6. After merge, delete branch
git checkout main
git pull
git branch -d feature/new-thing
```

## Pull Request Process

### Creating PRs
```bash
# Using gh CLI
gh pr create

# PR should include:
# - Clear title (no emojis, no issue numbers)
# - Description with "Fixes #123" if applicable
# - Test results or confirmation
# - Use [x] for completed tasks, NOT emojis
```

### PR Description Template
```markdown
## Summary
Brief description of changes

## Changes
- Change 1
- Change 2

## Testing
- [x] Unit tests pass
- [x] Integration tests pass
- [x] Manual testing completed

Fixes #123
```

## Git Commands Reference

### Branch Management
```bash
# List branches
git branch -a

# Switch branch
git checkout branch-name

# Create and switch
git checkout -b new-branch

# Delete local branch
git branch -d branch-name

# Delete remote branch
git push origin --delete branch-name
```

### Staging and Committing
```bash
# Check status
git status

# Stage all changes
git add .

# Stage specific files
git add file1 file2

# Interactive staging
git add -p

# Unstage
git reset HEAD file

# Commit
git commit -m "type: description"

# Amend last commit (use sparingly)
git commit --amend
```

### Syncing
```bash
# Fetch from remote
git fetch origin

# Pull with rebase
git pull --rebase origin main

# Push
git push

# Push new branch
git push -u origin branch-name
```

### History
```bash
# View log
git log --oneline

# View recent commits
git log -5

# View changes
git diff

# View staged changes
git diff --staged
```

## Important Notes
- **Always** test before committing
- **Never** commit broken code
- **Commit frequently** to track progress
- Each commit should work independently
- Use atomic commits (one logical change)
- NO emojis in commits or PRs
- NO Claude/Anthropic co-author mentions
