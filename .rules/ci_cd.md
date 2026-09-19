# CI/CD Workflow Standards

## Purpose: Automated Quality Gates
**Why CI/CD?** Catch issues before users do.
**Think:** Every pipeline failure is a production bug prevented.
**Goal:** Fast feedback, high confidence, zero surprises.

## Actual Workflows in This Repo (`.github/workflows/`)

### Required status checks (enforced by branch rulesets)
- `Lint` - `ruff check` / `ruff format --check`
- `Test (3.11)`, `Test (3.12)` - `pytest` matrix (required on `main`; `develop` only requires 3.12)

### Testing
- **`test.yml`** (name: "Tests") - `lint` -> `unit-tests` (Python 3.11/3.12 matrix, coverage to Codecov) -> `frontend-tests` (Bun: citation marker tests, streaming tests, widget syntax check) -> `integration-tests` (skips gracefully without Anthropic credentials) -> `all-tests` gate
- **`tests.yml`** (also named "Tests") - a near-duplicate of `test.yml` without the `integration-tests` job. Unreconciled duplication - when editing test jobs, check both files, and consider consolidating them (see `self_improve.md`'s guidance on promoting recurring friction into a fix).

### Docker / Release Images
- **`docker-build.yml`** - builds and pushes to GHCR. Uses a `concurrency` group keyed on `${{ github.workflow }}-<ref>` with `cancel-in-progress` only for `pull_request` events, specifically to stop two builds racing to write the `:latest` tag (see `docs/adr/` for the incident this fixed). Never remove the concurrency group without understanding that history.
- **`tests/test_latest_tag_policy.py`** - tests the semver/prerelease logic that decides whether a build is allowed to move `:latest`.

### Versioning & Release (see AGENTS.md "Version Management" for the full automated flow)
- `auto-bump-dev.yml` - bumps `.devN` on every push to `develop`
- `ensure-stable-version.yml` - strips `.dev` suffix if `src/version.py` changes on `main`
- `tag-release.yml` - tags `main` when the version is stable
- `release.yml` - creates the GitHub release
- `sync-develop.yml` - merges `main` back into `develop` and bumps to the next `.dev0` after a release

### Other
- `claude.yml` / `claude-code-review.yml` - Claude-based PR assistance/review bot
- `deploy-pages.yml`, `deploy-dashboard.yml` - docs/dashboard deployment
- `community-admin-pr-merge.yml` - community-maintainer "LGTM/merge" comment command (see `.context/community-admin-merge.md`)
- `publish.yml` / `publish-testpypi.yml` - package publishing
- `sync-worker-cors.yml`, `cleanup-preview-dns.yml`, `notify-docs.yml` - infra housekeeping

## Key Practices (Think About Pipeline Flow)
- **Pin versions:** `actions/checkout@v4` (reproducibility)
- **Cache deps:** `astral-sh/setup-uv` and `oven-sh/setup-bun` have built-in caching
- **Fail fast:** Lint -> Test -> Build -> Deploy (catch cheap failures first)
- **Matrix testing:** Test all supported Python versions (3.11, 3.12)
- **Secrets:** Never commit credentials; use GitHub Secrets
- **Conditional:** Deploy only from protected branches (`main`, `develop`)
- **Concurrency:** think about what races when a workflow can trigger twice in quick succession (see `docker-build.yml` above) before adding or removing a `concurrency` block

## Pipeline Philosophy
**Fast feedback:** Developers should know in <5 min
**Clear failures:** Error messages should guide fixes
**No surprises:** If it passes CI, it works in production

**Ask yourself:**
- Will this catch real issues?
- Is the feedback loop fast enough?
- Are we testing what actually matters?
