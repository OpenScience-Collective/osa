# Git & Version Control Standards

## Commit Messages
- **Format:** `<type>: <description>`
- **Length:** <50 characters
- **No emojis** in commits or PR titles
- **No co-author mentions**
- **Types:**
  - `feat:` New feature
  - `fix:` Bug fix
  - `docs:` Documentation only
  - `refactor:` Code restructuring
  - `test:` Adding tests (real tests only)
  - `chore:` Maintenance tasks

## Branch Strategy
- **Feature branches:** `feature/short-description`
- **Bugfix branches:** `fix/issue-description`
- **No spaces** in branch names, use hyphens
- **Delete after merge**

## Commit Practice
- **Atomic commits** - One logical change per commit
- **Test before commit** - Ensure code works
- **No broken commits** - Each commit should work independently
- **Commit frequently** - Track progress effectively

## Pull Request Process
1. Create issue first (for significant changes)
2. Branch from main
3. Make atomic commits
4. Push branch
5. Create PR with:
   - Clear, concise title (no issue numbers in title)
   - Description with "Fixes #123" if applicable
   - Test results
   - Use [x] for completed tasks, not emojis

## Git Commands
```bash
# Start feature
git checkout -b feature/new-thing

# Atomic commits
git add -p  # Stage selectively
git commit -m "feat: add user authentication"

# Update branch
git fetch origin
git rebase origin/main

# Push and create PR
git push -u origin feature/new-thing
gh pr create
```

---
*Atomic commits, clear messages, clean history.*
