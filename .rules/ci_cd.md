# CI/CD Workflow Standards

## Purpose: Automated Quality Gates
**Why CI/CD?** Catch issues before users do.
**Think:** Every pipeline failure is a production bug prevented.
**Goal:** Fast feedback, high confidence, zero surprises.

## Actual Workflows in This Repo (`.github/workflows/`)

### Required status checks (enforced by branch rulesets)
- `Lint` - `ruff check .` / `ruff format --check src tests`, plus actionlint on the notebook site's workflows; the `lint` job of `tests.yml`
- `Test (3.11)`, `Test (3.12)` - required on `main`; `develop` only requires `Test (3.12)`. Produced by `tests.yml`'s `test:` job (`name: Test`, so each matrix leg reports as `Test (<version>)`). Renaming that job, or adding a `name:` with its own matrix expression, changes the check names and blocks every merge - verify against the live ruleset with `gh api repos/OpenScience-Collective/osa/rulesets/12110625` / `.../12110632` (`required_status_checks[].context`) rather than trusting this file if the workflows change.

### Testing
- **`tests.yml`** (name: "Tests") - the only test workflow, and the one branch protection enforces. Triggers on push to `[main, develop]`, on every `pull_request`, and by hand (`workflow_dispatch`). `lint` -> `test` (job name `Test`; Python 3.11/3.12, plus 3.13 on a pull request into `main`, a push to `main` and a manual run; coverage to Codecov from 3.12; the headless-Chrome widget checks on 3.12) and `frontend-tests` (every Bun suite, the runtime bundle staleness check, the browser harness) -> `all-tests` gate. Its `integration-tests` job (live model calls, paid) is not part of that chain: it runs only when started by hand, with an optional `model` filter such as `haiku`, and fails when none of the Claude Platform, Bedrock or OpenRouter test credentials is set.
- **`changes` job** (in `tests.yml`) - classifies what the pull request or push changed. When only documentation changed (Markdown outside the code directories, `docs/`, `.context/`, `.rules/`, `LICENSE`), `Lint` and the `Test` legs still run and report success, but skip their steps, except that `Test` runs `tests/test_frontend/test_embed_snippet_hygiene.py`, the one test that reads documentation; `Frontend Tests` is skipped. Never add trigger-level `paths`/`paths-ignore` to `tests.yml`: a workflow that does not start leaves its required checks "expected" forever, and a matrix job skipped by a job-level `if:` reports as `Test`, not `Test (3.12)`. Skip work with step-level conditions in `Lint` and `Test`.
- There used to be a second workflow, `test.yml`, also named "Tests", that re-ran the same jobs on every pull request into and push to `main`. It was merged into `tests.yml`; do not reintroduce a second workflow that runs the same tests.

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
- `claude.yml` - Claude-based PR assistance, answering `@claude` mentions (there is no automated review bot; pull requests are reviewed locally)
- `deploy-pages.yml`, `deploy-dashboard.yml` - docs/dashboard deployment
- `community-admin-pr-merge.yml` - community-maintainer "LGTM/merge" comment command (see `.context/community-admin-merge.md`)
- `publish.yml` / `publish-testpypi.yml` - package publishing
- `sync-worker-cors.yml`, `cleanup-preview-dns.yml`, `notify-docs.yml` - infra housekeeping

## Key Practices (Think About Pipeline Flow)
- **Pin versions:** `actions/checkout@v4` (reproducibility)
- **Cache deps:** `astral-sh/setup-uv` and `oven-sh/setup-bun` have built-in caching
- **Fail fast:** Lint -> Test -> Build -> Deploy (catch cheap failures first)
- **Matrix testing:** `tests.yml` tests Python 3.11 and 3.12 on every run, and 3.13 on runs bound for `main`; only `Test (3.11)` and `Test (3.12)` are required - see "Required status checks" above
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
