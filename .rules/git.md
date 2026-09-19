# Git & Version Control Standards

## Commit Messages
- **Format:** `<type>: <description>`
- **Length:** <50 characters
- **No emojis** in commits or PR titles
- **No AI attribution** (no "Co-Authored-By: Claude" or similar)
- **Types:**
  - `feat:` New feature
  - `fix:` Bug fix
  - `docs:` Documentation only
  - `refactor:` Code restructuring
  - `test:` Adding tests (real tests only)
  - `chore:` Maintenance tasks

## Branch Strategy
- **Feature branches:** `feature/issue-N-short-description`, created **from `develop`**
- **Bugfix branches:** `fix/issue-N-description`, also from `develop`
- **`main`:** production only, no direct commits - updated exclusively via a `develop` -> `main` release PR
- **No spaces** in branch names, use hyphens
- **Delete after merge**

See AGENTS.md's "Branch Strategy" section for the full `main`/`develop`/`feature` model, version automation, and branch protection rules.

## Commit Practice
- **Atomic commits** - One logical change per commit
- **Test before commit** - Ensure code works
- **No broken commits** - Each commit should work independently
- **Commit frequently** - Track progress effectively

## Pull Request Process
1. Create issue first (for significant changes)
2. `git checkout develop && git pull && git checkout -b feature/issue-N-short-description`
3. Make atomic commits
4. Push branch
5. Run `/pr-review-toolkit:review-pr` and address ALL findings (critical + important) before opening the PR
6. Create PR with `gh pr create --base develop`:
   - Clear, concise title (<70 chars, no emojis)
   - Description with "Fixes #123" if applicable
   - Test results summary
   - Use `[x]` for completed tasks, not emojis
7. Squash merge to `develop`: `gh pr merge --squash --delete-branch`

Release PRs (`develop` -> `main`) are the one exception: use a regular merge,
not squash (`main`'s branch ruleset only allows `allowed_merge_methods:
["merge"]`), and require an approving review from a maintainer other than the
author - see `.rules/code_review.md` and `docs/adr/`.

## Git Commands
```bash
# Start feature from develop
git checkout develop && git pull
git checkout -b feature/issue-123-auth

# Atomic commits
git add -p  # Stage selectively
git commit -m "feat: add user authentication"

# Update branch
git fetch origin
git rebase origin/develop

# Push and create PR
git push -u origin feature/issue-123-auth
gh pr create --base develop
```

## .gitignore Essentials
```
__pycache__/     # Python
node_modules/    # JavaScript
.env             # Secrets
*.log            # Logs
.venv/           # Virtual environments
```

---
*Atomic commits, clear messages, clean history. No emojis, no AI attribution.*
