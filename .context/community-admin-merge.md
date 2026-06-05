# Community Admin Auto-Merge

Lets a community maintainer auto-merge a pull request (PR) that touches ONLY
their own community's directory, without waiting on a human reviewer. The normal
required status checks (ruff + tests) still gate the actual merge, so this only
removes the human-approval step, not the CI gate.

Workflow file: `.github/workflows/community-admin-pr-merge.yml`

## What it does

When a PR is opened, synchronized, reopened, or marked ready-for-review against
`develop` or `main`, the workflow:

1. Reads the PR's changed-file list via the GitHub API.
2. Confirms every changed path is under a single `src/assistants/<id>/` tree
   (one community, nothing outside it).
3. Reads the `maintainers:` list from the BASE branch copy of
   `src/assistants/<id>/config.yaml`.
4. Confirms the PR does NOT change the `maintainers:` field itself (a covert
   admin-list change is rejected before the author is even considered).
5. Confirms the PR author is in that list (case-insensitive).
6. If all checks pass: approves the PR and enables a SQUASH auto-merge using a
   dedicated GitHub App token. The merge fires only after required checks pass.

If any check fails, or anything is ambiguous, the workflow no-ops (logs a notice
and exits cleanly). The PR then follows the normal human-review path. Default
posture is "do not merge".

## Trust model

- Source of truth for authorization is the `maintainers:` list in the
  community's `config.yaml`, read from the BASE branch (the already-merged,
  trusted code), NEVER from the PR head. This prevents a PR from granting itself
  merge rights by adding its author to the list in the same change.
- Scope is path-restricted to exactly one `src/assistants/<id>/` directory.
  A PR that touches two communities, or touches anything outside a community
  directory (including `src/version.py`, CI, top-level files), is ineligible.
- Edits to the `maintainers:` field are excluded from auto-merge. Changing who
  holds admin power always requires human review. The workflow detects this by
  parsing the base vs head copies of `config.yaml` and comparing the normalized
  maintainers lists.
- The actual merge still requires all branch-protection required checks to be
  green. Auto-merge only removes the manual-approval gate.
- The workflow uses `pull_request_target` so it has access to secrets even for
  fork PRs, but it NEVER checks out or executes PR-head code. The only checkout
  is the trusted base branch; the PR diff is read through the API only. The head
  copy of `config.yaml` is fetched and parsed solely to compare the maintainers
  field; it is never executed.

## Token model

All write actions (approve + merge + confirmation comment) use a SHORT-LIVED
installation token minted from a dedicated GitHub App via
`actions/create-github-app-token@v1`. This App is independent from the
`CI_ADMIN_TOKEN` personal access token (PAT) used by the version-automation
workflows. The workflow's own `GITHUB_TOKEN` is kept at `contents: read` only,
so the workflow has no standing write power; every write is attributable to the
App.

## One-time setup (human)

These steps are done once by a repo admin. Until they are complete, the workflow
evaluates every PR but skips the write steps (mint, approve, merge) for
ineligible PRs. If an *eligible* PR is opened before the secrets exist, the
token-mint step fails with an error (the PR is not merged). Finish all steps
before relying on it.

### 1. Create the GitHub App

GitHub -> Settings (your org `OpenScience-Collective`) -> Developer settings ->
GitHub Apps -> New GitHub App.

- GitHub App name: e.g. `OSA Community Auto-Merge`.
- Homepage URL: the repo URL is fine (`https://github.com/OpenScience-Collective/osa`).
- Webhook: UNCHECK "Active". No webhook is needed; the Action invokes the App by
  minting a token, the App does not need to receive events.
- Repository permissions (set ONLY these; leave everything else "No access"):
  - Pull requests: Read and write  (needed to approve and to enable auto-merge)
  - Contents: Read and write       (needed for the squash merge to write the commit)
  - Metadata: Read-only            (mandatory; GitHub sets this automatically)
- Organization permissions: none.
- Account permissions: none.
- "Where can this GitHub App be installed?": Only on this account.

Create the App. Note the App ID shown on the App's settings page.

### 2. Install the App on this repo

From the App's settings page -> Install App -> install it on
`OpenScience-Collective`, and restrict it to the `osa` repository only
("Only select repositories" -> `osa`).

### 3. Generate a private key

On the App's settings page -> "Private keys" -> "Generate a private key". This
downloads a `.pem` file. Keep it secret; you will paste its full contents into a
repo secret in the next step. You cannot retrieve it again later, only generate
a new one.

### 4. Add the two repo secrets

Repo -> Settings -> Secrets and variables -> Actions -> New repository secret:

- `COMMUNITY_MERGE_APP_ID` = the numeric App ID from step 1.
- `COMMUNITY_MERGE_APP_PRIVATE_KEY` = the full contents of the `.pem` file from
  step 3 (including the `-----BEGIN ... KEY-----` / `-----END ... KEY-----`
  lines).

### 5. Enable "Allow auto-merge" on the repo

The workflow uses `gh pr merge --auto`, which requires the repository-level
auto-merge feature to be turned on. Repo -> Settings -> General -> "Pull
Requests" section -> check "Allow auto-merge". Without this, the merge step
fails (the PR will still be approved, but it will not merge automatically).

### 6. Let the App merge past branch protection

Branch protection on `develop` and `main` is configured in the GitHub UI (not in
the repo). Make sure the App is allowed to merge:

- Required status checks must stay ON (ruff + tests). The App does NOT bypass
  these; auto-merge waits for them. Do not add the App to any
  "allow specified actors to bypass required pull requests / status checks"
  allowlist; it should merge through the same gate everyone else uses.
- If a branch has "Restrict who can push to matching branches" (a push/merge
  allowlist) enabled, add the GitHub App to that allowlist so its merge is
  permitted. If that restriction is not enabled, no action is needed.
- "Require approvals" can stay on; the App's approval satisfies it. If you use
  "Dismiss stale approvals", note that a new push will dismiss the App's
  approval and the workflow will re-approve on the next `synchronize` event.

## How a community admin uses it

1. Open a PR against `develop` (or `main`) that changes only files inside your
   community directory, `src/assistants/<your-id>/` (config.yaml, tools.py,
   prompts, logo, etc.).
2. Make sure you are a listed maintainer in that directory's `config.yaml`.
3. Do not change the `maintainers:` field in the same PR; that needs human
   review.
4. The workflow approves the PR and enables squash auto-merge. The PR merges
   automatically once ruff and the test suite pass. A confirmation comment is
   posted (and updated in place on later pushes, not duplicated).

If your PR is not eligible (touches more than one community, touches files
outside your directory, changes the maintainers list, or you are not a listed
maintainer), the workflow simply does nothing and the PR follows the normal
review process.

## Security notes and limitations

- The `maintainers:` list is hand-maintained in each `config.yaml`. There is no
  sync with GitHub Teams or org membership; whoever is listed there is trusted to
  merge changes to that community's directory.
- Adding or removing a maintainer is intentionally excluded from auto-merge and
  always requires a human-reviewed PR.
- A brand-new community (no `config.yaml` on the base branch yet) cannot be
  auto-merged, because there is no trusted maintainers list to authorize
  against. The first PR introducing a community needs human review.
- A `config.yaml` that fails to parse (malformed YAML, missing or empty
  maintainers, non-string entries) is treated as "not eligible".
- Auto-merging community changes into `main` will NOT cut a release. Releases
  are triggered by changes to `src/version.py`, and community PRs are
  path-restricted so they never touch it.
- The workflow uses `pull_request_target` but never runs PR-head code; see the
  Trust model section and the comment block at the top of the workflow file.
