# Epic Branch Workflow for Multi-Phase Features

For features with multiple phases (like EEGLAB with 3 phases), use an epic branch worktree as an integration point before merging to develop.

## Why Epic Branches?

**Problem:** Merging Phase 1 to develop before Phase 2/3 are complete means:
- Incomplete feature in develop
- Can't test integrated epic until all phases done
- Hard to iterate on earlier phases once merged

**Solution:** Epic branch worktree as integration point
- Test all phases together in epic worktree
- Can iterate on any phase easily
- Develop stays clean with only complete features

## Worktree Structure Overview

```
/Users/yahya/Documents/git/osa          (develop)
/Users/yahya/Documents/git/osa-epic     (epic/issue-97-eeglab)
/Users/yahya/Documents/git/osa-phase1   (feature/issue-99-phase1)
/Users/yahya/Documents/git/osa-phase2   (feature/issue-100-phase2)
/Users/yahya/Documents/git/osa-phase3   (feature/issue-101-phase3)
```

## Step-by-Step Process

### 1. Create Epic Branch Worktree (DONE ✓)

```bash
cd /Users/yahya/Documents/git/osa

# Create epic branch from develop
git checkout develop
git pull
git checkout -b epic/issue-97-eeglab
git push -u origin epic/issue-97-eeglab

# Switch back to develop in main worktree
git checkout develop

# Create epic worktree
git worktree add ../osa-epic epic/issue-97-eeglab
```

**Current status:** ✓ Epic worktree created at `/Users/yahya/Documents/git/osa-epic`

### 2. Create Phase Worktrees FROM Epic Branch

```bash
# Phase 1 worktree from epic branch (DONE ✓)
git worktree add ../osa-phase1 -b feature/issue-99-phase1 epic/issue-97-eeglab

# When Phase 1 is done, create Phase 2
git worktree add ../osa-phase2 -b feature/issue-100-phase2 epic/issue-97-eeglab

# When Phase 2 is done, create Phase 3
git worktree add ../osa-phase3 -b feature/issue-101-phase3 epic/issue-97-eeglab
```

**Note:** We're branching from `epic/issue-97-eeglab`, not `develop`!

### 3. Develop Each Phase

In each phase worktree:

```bash
cd ../osa-phase1  # or phase2, phase3

# Make changes
# ... develop, test, commit ...

# Run review before PR
/pr-review-toolkit:review-pr

# Address all critical + important issues

# Create PR to EPIC BRANCH (not develop!)
gh pr create --base epic/issue-97-eeglab \
  --title "feat: EEGLAB Phase 1" \
  --body "Closes #99"

# After approval, squash merge
gh pr merge --squash --delete-branch
```

**Critical:** PR base is `epic/issue-97-eeglab`, NOT `develop`!

### 4. Test Epic Branch Locally

After merging phases into epic:

```bash
# Work in epic worktree
cd /Users/yahya/Documents/git/osa-epic
git pull

# Run full test suite
uv run pytest tests/ -v

# Start backend and test
export OPENROUTER_API_KEY="your-key"
uv run uvicorn src.api.main:app --reload --port 38528

# Test in another terminal
uv run osa chat --community eeglab --standalone
```

### 5. Merge Epic to Develop (Final Step)

Only when ALL phases are complete and tested:

```bash
cd /Users/yahya/Documents/git/osa
git checkout develop
git pull

# Create PR from epic to develop
gh pr create --base develop \
  --head epic/issue-97-eeglab \
  --title "feat: EEGLAB community implementation (complete)" \
  --body "Implements full EEGLAB community support.

Includes:
- Phase 1: Basic setup (config, docs, tests)
- Phase 2: Widget integration
- Phase 3: Advanced features

Closes #97"

# After approval, squash merge
gh pr merge --squash --delete-branch
```

## Current EEGLAB Status (FIXED ✓)

- ✓ Epic branch created: `epic/issue-97-eeglab`
- ✓ Epic worktree created: `/Users/yahya/Documents/git/osa-epic`
- ✓ Phase 1 worktree exists: `/Users/yahya/Documents/git/osa-phase1`
- ✓ PR #106 retargeted to epic branch
- ✓ Ready to test and merge Phase 1 → epic

## Worktree Management

### List All Worktrees

```bash
git worktree list
```

Expected output:
```
/Users/yahya/Documents/git/osa         [develop]
/Users/yahya/Documents/git/osa-epic    [epic/issue-97-eeglab]
/Users/yahya/Documents/git/osa-phase1  [feature/issue-99-phase1-basic-setup]
```

### Switch Between Worktrees

```bash
# Work on epic integration
cd /Users/yahya/Documents/git/osa-epic

# Work on Phase 1
cd /Users/yahya/Documents/git/osa-phase1

# Main repo (develop)
cd /Users/yahya/Documents/git/osa
```

### Update Epic Worktree

If develop changes while working on epic:

```bash
cd /Users/yahya/Documents/git/osa-epic
git pull origin develop  # Merge develop into epic
git push
```

### Update Phase Worktrees

If epic branch changes (due to merged phases or develop updates):

```bash
cd /Users/yahya/Documents/git/osa-phase2  # In phase worktree
git pull --rebase origin epic/issue-97-eeglab
```

### Cleaning Up After Epic Merge

```bash
cd /Users/yahya/Documents/git/osa

# Remove worktrees
git worktree remove ../osa-phase1
git worktree remove ../osa-phase2
git worktree remove ../osa-phase3
git worktree remove ../osa-epic

# Delete local epic branch
git branch -d epic/issue-97-eeglab

# Remote branch is deleted by squash merge
```

## Benefits of Worktree Approach

1. **Isolated Environments:** Each phase has its own directory, no branch switching needed
2. **Test Integration Early:** Epic worktree lets you test all phases together
3. **Parallel Work:** Can work on Phase 2 while fixing Phase 1
4. **Clean Main Repo:** Main repo stays on develop, always stable
5. **Easy Navigation:** `cd ../osa-epic` to test integration

## Testing Workflow Example

```bash
# Develop in phase1
cd /Users/yahya/Documents/git/osa-phase1
# ... make changes, commit ...

# Merge phase1 to epic via PR
gh pr merge 106 --squash

# Test in epic worktree
cd /Users/yahya/Documents/git/osa-epic
git pull
uv run pytest tests/
export OPENROUTER_API_KEY="..."
uv run uvicorn src.api.main:app --reload --port 38528
# ... test the integrated epic ...

# Start phase2 from epic
cd /Users/yahya/Documents/git/osa
git worktree add ../osa-phase2 -b feature/issue-100-phase2 epic/issue-97-eeglab

# Develop in phase2
cd /Users/yahya/Documents/git/osa-phase2
# ...
```

## When to Use Epic Worktrees

**Use for:**
- Multi-phase features (3+ phases)
- Features that need integration testing before merging to develop
- Long-running feature development
- Features where early phases may need updates while working on later phases

**Don't use for:**
- Single-phase features (just use feature branch → develop)
- Quick bug fixes
- Documentation updates
- Independent features that can be merged separately

## Summary

For multi-phase features like EEGLAB:
1. Create epic branch and worktree from develop
2. Create phase worktrees from epic (not develop)
3. Merge phases into epic via PRs
4. Test integrated epic in epic worktree
5. Merge epic to develop when complete

**All work happens in worktrees - main repo stays on develop!**
