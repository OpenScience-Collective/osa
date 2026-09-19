# CI/CD Workflow Standards

## Purpose: Automated Quality Gates
**Why CI/CD?** Catch issues before users do.
**Think:** Every pipeline failure is a production bug prevented.
**Goal:** Fast feedback, high confidence, zero surprises.

## Actual Workflows in This Repo (`.github/workflows/`)

### Required status checks (enforced by branch rulesets)
- `Lint` - `ruff check` / `ruff format --check` (both `test.yml` and `tests.yml` have a job literally named `Lint`, so either satisfies this)
- `Test (3.11)`, `Test (3.12)` - required on `main`; `develop` only requires `Test (3.12)`. **These check names are produced only by `tests.yml`'s `test:` job** (`name: Test`, matrix `['3.11', '3.12']` → checks `Test (3.11)`/`Test (3.12)`). `test.yml`'s equivalent job is named `Unit Tests (Python ${{ matrix.python-version }})`, which produces differently-named checks (`Unit Tests (Python 3.11)` etc.) that do **not** match what the ruleset requires - verify against the live ruleset with `gh api repos/OpenScience-Collective/osa/rulesets/12110625` / `.../12110632` (`required_status_checks[].context`) rather than trusting this file if the workflows change.

### Testing
- **`tests.yml`** (name: "Tests") - **this is the workflow branch protection actually enforces.** `lint` -> `test` (job name `Test`, Python 3.11/3.12 matrix, coverage upload) -> `frontend-tests` (Bun: citation marker tests, streaming tests, widget syntax check) -> `all-tests` gate. Triggers on push to `[main, develop]` and on `pull_request` with no branch restriction, so it's the only one of the two that runs on a PR into `develop`.
- **`test.yml`** (also named "Tests" - confusingly, both workflows share the display name "Tests") - triggers only on push/PR to `main`, not `develop`. Runs `lint` -> `unit-tests` (job name `Unit Tests (Python ...)`, Python 3.11/3.12/3.13 matrix, coverage to Codecov) -> `frontend-tests` -> `integration-tests` (skips gracefully without Anthropic credentials) -> `all-tests`. Broader (extra Python version, an `integration-tests` job `tests.yml` lacks) but its checks don't match what branch protection requires and it never runs on a `develop` PR at all.
- Unreconciled duplication between the two - when editing test jobs, check both files, and consider consolidating them (see `self_improve.md`'s guidance on promoting recurring friction into a fix). If you only fix one, fix `tests.yml` first - it's the one that actually gates a merge.

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
- **Matrix testing:** the gating workflow (`tests.yml`) tests Python 3.11 and 3.12; `test.yml` additionally covers 3.13 but isn't what branch protection checks against - see "Required status checks" above
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
