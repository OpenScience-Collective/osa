# Epic Branch Workflow for Multi-Phase Features

For features with multiple phases (like EEGLAB with 3 phases), use an epic branch as an integration point before merging to develop.

## Why Epic Branches?

**Problem:** Merging Phase 1 to develop before Phase 2/3 are complete means:
- Incomplete feature in develop
- Can't test integrated epic until all phases done
- Hard to iterate on earlier phases once merged

**Solution:** Epic branch as integration point
- Test all phases together before merging to develop
- Can iterate on any phase easily
- Develop stays clean with only complete features

## Workflow Overview

```
develop
  │
  └─> epic/issue-97-eeglab      (Epic branch)
        ├─> feature/issue-99-phase1    (Phase 1)
        ├─> feature/issue-100-phase2   (Phase 2)
        └─> feature/issue-101-phase3   (Phase 3)
```

## Step-by-Step Process

### 1. Create Epic Branch (Do This First)

```bash
cd /Users/yahya/Documents/git/osa

# Create epic branch from develop
git checkout develop
git pull
git checkout -b epic/issue-97-eeglab
git push -u origin epic/issue-97-eeglab
```

### 2. Create Phase Worktrees FROM Epic Branch

```bash
# Phase 1 worktree from epic branch (not develop!)
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
# Switch to epic branch in main worktree
cd /Users/yahya/Documents/git/osa
git checkout epic/issue-97-eeglab
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

## Current EEGLAB Status

We're currently in a WRONG state:
- ✗ Phase 1 branched from `develop`
- ✗ PR #106 targets `develop`
- ✗ No epic branch exists

**Fix This:**

### Option A: Start Fresh (Recommended)

```bash
# 1. Close PR #106
gh pr close 106

# 2. Delete phase1 worktree
cd /Users/yahya/Documents/git/osa
git worktree remove ../osa-phase1

# 3. Create epic branch
git checkout develop
git pull
git checkout -b epic/issue-97-eeglab
git push -u origin epic/issue-97-eeglab

# 4. Cherry-pick phase1 commits to epic
git cherry-pick <commit-hash-of-phase1-work>
git push

# 5. Create phase1 worktree from epic
git worktree add ../osa-phase1 -b feature/issue-99-phase1 epic/issue-97-eeglab

# 6. Recreate PR #106 with base epic/issue-97-eeglab
cd ../osa-phase1
gh pr create --base epic/issue-97-eeglab
```

### Option B: Convert Current PR (Easier)

```bash
# 1. Change PR #106 base from develop to epic branch
# (Unfortunately gh cli doesn't support this, need to do via GitHub UI)
# Go to: https://github.com/OpenScience-Collective/osa/pull/106
# Click "Edit" next to branch info
# Change base from "develop" to "epic/issue-97-eeglab"

# 2. Create epic branch from develop
cd /Users/yahya/Documents/git/osa
git checkout develop
git pull
git checkout -b epic/issue-97-eeglab
git push -u origin epic/issue-97-eeglab
```

### Option C: Proceed As-Is (Not Recommended)

Merge Phase 1 to develop now, then:
- Phase 2/3 still branch from develop
- Test integration only after ALL phases merged
- Can't easily iterate on Phase 1 once in develop

## Epic Branch Management

### Keeping Epic Updated

If develop changes while working on epic:

```bash
cd /Users/yahya/Documents/git/osa
git checkout epic/issue-97-eeglab
git pull origin develop  # Merge develop into epic
git push
```

### Rebasing Phases

If epic branch changes (due to merged phases or develop updates):

```bash
cd ../osa-phase2  # In phase worktree
git pull --rebase origin epic/issue-97-eeglab
```

### Cleaning Up After Epic Merge

```bash
# Remove worktrees
git worktree remove ../osa-phase1
git worktree remove ../osa-phase2
git worktree remove ../osa-phase3

# Delete local epic branch
git branch -d epic/issue-97-eeglab

# Delete remote (already done by squash merge)
```

## Benefits of This Approach

1. **Test Integration Early:** Test all phases together in epic branch
2. **Iterate Freely:** Can go back and fix Phase 1 even while working on Phase 3
3. **Clean Develop:** Develop only gets complete, tested features
4. **Clear History:** One squash commit to develop with all phases
5. **Easy Rollback:** If epic has issues, just don't merge it

## Example: HED Would Benefit Too

If HED had multiple phases:
```
develop
  │
  └─> epic/hed-advanced-features
        ├─> feature/hed-definitions
        ├─> feature/hed-temporal-scope
        └─> feature/hed-library-schemas
```

## When NOT to Use Epic Branches

- Single-phase features (just use feature branch → develop)
- Quick bug fixes
- Documentation updates
- Features where phases are truly independent (can be merged separately)

## Summary

For multi-phase features like EEGLAB:
1. Create epic branch from develop
2. Branch phases from epic (not develop)
3. Merge phases into epic
4. Test integrated epic
5. Merge epic to develop when complete

**Current Action:** Decide Option A, B, or C for EEGLAB Phase 1
