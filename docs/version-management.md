# Version Management Workflow

This document explains how version bumping and releases work in the OSA project.

## Overview

- **Develop branch**: Development versions with `.dev` suffix (e.g., `0.5.1.dev0`)
- **Main branch**: Stable releases (e.g., `0.5.1`)
- **Automated tagging**: GitHub Actions creates tags and releases on main
- **Branch protection**: Both branches require PRs, but admins can bypass for version bumps

## Tools

### bump_version.py Script

Located at `scripts/bump_version.py`, this script handles version bumping with semantic versioning and PEP 440 pre-release labels.

**Usage**:
```bash
# Show current version
uv run python scripts/bump_version.py --current

# Bump version and commit (manual push)
uv run python scripts/bump_version.py patch --prerelease dev

# Bump version, commit, and push (requires bypass permissions)
uv run python scripts/bump_version.py patch --prerelease dev --push

# CI mode (no prompts, auto-push, auto-release)
uv run python scripts/bump_version.py patch --ci
```

**Flags**:
- `--push`: Automatically push commit and tag (requires bypass permissions)
- `--ci`: CI mode - skip prompts, auto-push, auto-release
- `--no-git`: Skip git operations entirely
- `--current`: Show current version

**Version Formats** (PEP 440):
- Dev: `0.5.1.dev0`, `0.5.1.dev1` (development pre-release)
- Alpha: `0.5.1a0`, `0.5.1a1` (alpha pre-release)
- Beta: `0.5.1b0`, `0.5.1b1` (beta pre-release)
- RC: `0.5.1rc0`, `0.5.1rc1` (release candidate)
- Stable: `0.5.1` (final release)

## Workflow for Develop Branch

### Manual Approach (with --push)

```bash
# On develop branch
git checkout develop
git pull origin develop

# Bump version with automatic push (requires bypass permissions)
uv run python scripts/bump_version.py patch --prerelease dev --push

# Script will:
# 1. Bump version in src/version.py
# 2. Commit the change
# 3. Create tag (e.g., v0.5.1.dev0)
# 4. Push to origin (bypasses branch protection if you have permissions)
# 5. Push tag to origin
```

**Requirements**:
- You must be a repo/org admin to use `--push` on develop
- Admin users are on the bypass list for develop branch

### PR Approach (traditional)

If you don't have bypass permissions or prefer PRs:

```bash
# Create feature branch
git checkout -b feature/version-bump-0.5.2-dev0
uv run python scripts/bump_version.py patch --prerelease dev
git push -u origin feature/version-bump-0.5.2-dev0

# Create PR
gh pr create --base develop --title "chore: bump version to 0.5.2.dev0"
gh pr merge --squash
```

## Workflow for Main Branch (Releases)

### Step 1: Create Release PR

When ready to release, create a PR from develop to main:

```bash
git checkout develop
git pull origin develop

# Create release branch
git checkout -b release/0.5.1
uv run python scripts/bump_version.py patch --prerelease stable

# Push and create PR
git push -u origin release/0.5.1
gh pr create --base main --title "Release 0.5.1" --body "Release version 0.5.1

## Changes
- Feature 1
- Feature 2
- Bug fix 3"
```

### Step 2: Merge PR

After review and CI passes:

```bash
gh pr merge <PR_NUMBER> --squash
```

### Step 3: Automated Tagging (CI)

The **Tag and Release** workflow (`.github/workflows/tag-release.yml`) automatically:

1. Detects version change in `src/version.py` on main
2. Reads the version number
3. Checks if tag exists
4. If not, creates tag `v{version}` (e.g., `v0.5.1`)
5. Pushes tag to GitHub
6. Generates release notes from commits
7. Creates GitHub Release

**No manual intervention needed** - tagging and release happen automatically!

### Monitoring the Workflow

Check the workflow status:

```bash
# View workflow runs
gh run list --workflow "Tag and Release"

# View specific run details
gh run view <RUN_ID>

# View created releases
gh release list
```

## Branch Protection Settings

### Develop Branch

```yaml
Require pull request: Yes
  - Allow specified actors to bypass: repo admins, org admins
Require status checks: Yes
  - Tests (Python 3.11, 3.12)
  - Build and Test Docker Image
```

**Effect**: Admins can push directly using `--push` flag, bypassing PR requirement.

### Main Branch

```yaml
Require pull request: Yes
  - Require approvals: 1
Require status checks: Yes
  - Tests (Python 3.11, 3.12)
  - Build and Test Docker Image
Do not allow bypassing: Yes
```

**Effect**: All changes require PR + approval. GitHub Actions has elevated permissions to create tags via `contents: write`.

## CI/CD Integration

### GitHub Actions Permissions

The Tag and Release workflow has special permissions:

```yaml
permissions:
  contents: write      # Create tags and releases
  pull-requests: read  # Read PR information
```

These permissions allow the workflow to bypass branch protection for tag creation.

### Using --ci Flag in Workflows

For automated workflows that need to bump versions:

```yaml
- name: Bump version
  run: |
    uv run python scripts/bump_version.py patch --prerelease dev --ci
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

The `--ci` flag:
- Skips all interactive prompts
- Automatically pushes commit and tag
- Automatically creates GitHub release
- Perfect for fully automated workflows

## Examples

### Development Iteration

```bash
# Quick dev version bump on develop (bypass permissions)
uv run python scripts/bump_version.py --prerelease dev --push
# Result: 0.5.1.dev0 -> 0.5.1.dev1

# Patch version with dev label
uv run python scripts/bump_version.py patch --prerelease dev --push
# Result: 0.5.1.dev1 -> 0.5.2.dev0
```

### Release Cycle

```bash
# 1. Feature development on develop
uv run python scripts/bump_version.py --prerelease dev --push
# Develop: 0.5.1.dev0

# 2. More features
uv run python scripts/bump_version.py --prerelease dev --push
# Develop: 0.5.1.dev1

# 3. Ready for release - create PR develop -> main
git checkout -b release/0.5.1
uv run python scripts/bump_version.py patch --prerelease stable
# Version: 0.5.1
git push -u origin release/0.5.1
gh pr create --base main

# 4. After PR merge, CI automatically:
# - Creates tag v0.5.1
# - Creates GitHub Release

# 5. Bump develop to next version
git checkout develop
uv run python scripts/bump_version.py patch --prerelease dev --push
# Develop: 0.5.2.dev0
```

## Troubleshooting

### "Remote rejected: push declined due to repository rule violations"

**Cause**: You don't have bypass permissions for the branch.

**Solutions**:
1. Use PR approach instead of `--push`
2. Ask admin to add you to bypass list
3. For main branch, use the automated workflow (doesn't require bypass)

### "Tag already exists"

**Cause**: Tag was already created.

**Solutions**:
```bash
# Delete local and remote tag
git tag -d v0.5.1
git push origin --delete v0.5.1

# Re-run bump script
uv run python scripts/bump_version.py ...
```

### Workflow doesn't create tag on main

**Checks**:
1. Verify `src/version.py` was modified in the PR
2. Check workflow run: `gh run list --workflow "Tag and Release"`
3. Check workflow has `contents: write` permission
4. Verify tag doesn't already exist: `git tag -l`

## References

- PEP 440: https://peps.python.org/pep-0440/
- Semantic Versioning: https://semver.org/
- GitHub Actions Permissions: https://docs.github.com/en/actions/security-guides/automatic-token-authentication#permissions-for-the-github_token
