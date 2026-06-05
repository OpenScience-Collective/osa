# Community Maintainer Merge Command

Lets a community maintainer merge a pull request (PR) into `develop` that touches
ONLY their own community's directory, by leaving an explicit comment. Nothing
merges on open; the maintainer comments when they are ready to ship. The normal
required status checks (ruff + tests) still gate the actual merge, so this only
removes the human-approval step, not the CI gate.

**Scope is `develop` only.** Releases (`develop -> main`) are done by an OSA admin,
so `main` never diverges from the integrated `develop` branch. A community PR that
targets `main` is ignored by this workflow.

Workflow file: `.github/workflows/community-admin-pr-merge.yml`

## How a community maintainer uses it

1. Open a PR against **`develop`** that changes only files inside your community
   directory, `src/assistants/<your-id>/` (`config.yaml`, `tools.py`, prompts,
   `logo.svg`, etc.). It does not have to be authored by you.
2. When you (a listed maintainer of that community) are ready to merge it, post a
   PR comment containing both keywords **`LGTM`** and **`merge`**, for example:

   > LGTM, please merge

   Casing does not matter (`lgtm`/`LGTM`), and any phrasing works as long as both
   words appear.
3. The bot approves the PR and enables **squash** auto-merge. The PR merges
   automatically once ruff and the tests pass. A confirmation comment is posted
   (and updated in place, not duplicated).

That's it. If the PR is not eligible (see below), the comment simply does
nothing and the PR follows the normal review process.

## What it does

When a comment is posted on a PR, the workflow:

1. Checks the comment contains both `LGTM` and `merge` (case-insensitive). If not,
   it stops immediately.
2. Reads the PR via the GitHub API and confirms it is **open**, not a draft, and
   targets **`develop`**.
3. Confirms every changed path (including a rename's old path) is under a single
   `src/assistants/<id>/` tree (one community, nothing outside it).
4. Reads the `maintainers:` list from the **base-branch** copy of
   `src/assistants/<id>/config.yaml`.
5. Confirms the PR does NOT change the `maintainers:` field itself (a covert
   admin-list change is excluded and needs human review).
6. Confirms the **commenter** is in that maintainers list (case-insensitive).
7. If all checks pass: approves the PR and enables squash auto-merge using a
   dedicated GitHub App token, **pinned to the exact commit the comment was made
   on**. The merge fires only after required checks pass.

If any check fails, or anything is ambiguous, the workflow no-ops (logs a notice
and exits cleanly). Default posture is "do not merge".

## Trust model

- **Authorization is the commenter**, who must be in the `maintainers:` list of
  the target community's `config.yaml`, read from the **base branch** (the
  already-merged, trusted code), NEVER from the PR head. This prevents a PR from
  granting itself merge rights by adding someone to the list in the same change.
- **Scope** is path-restricted to exactly one `src/assistants/<id>/` directory.
  A PR that touches two communities, or anything outside a community directory
  (including `src/version.py`, CI, top-level files), is ineligible.
- **Maintainers-field edits are excluded.** Changing who holds merge power always
  requires human review. The workflow parses the base vs head copies of
  `config.yaml` and compares the normalized maintainers lists.
- **No approve-then-push bypass.** The merge is pinned to the head commit the
  command was made on (`gh pr merge --match-head-commit`). If new commits are
  pushed after the comment, the pinned auto-merge is cancelled and a fresh
  `LGTM ... merge` comment is required. (The `protect-dev` ruleset does not
  dismiss stale reviews on push, so the workflow enforces this itself.)
- **CI still gates.** The merge waits for all required checks (`--auto`); the App
  does not bypass them.
- **`issue_comment` is privileged** (it runs in the base-repo context with
  secrets, like `pull_request_target`), so the workflow NEVER checks out or runs
  PR-head code. Everything is read through the API; the head copy of `config.yaml`
  is fetched only to compare the maintainers field, never executed.

## Token model

All write actions (approve + merge + confirmation comment) use a SHORT-LIVED
installation token minted from a dedicated GitHub App via
`actions/create-github-app-token@v1`. This App is independent from the
`CI_ADMIN_TOKEN` personal access token (PAT) used by the version-automation
workflows. The workflow's own `GITHUB_TOKEN` is kept read-only, so the workflow
has no standing write power; every write is attributable to the App.

## One-time setup (human)

These steps are done once by a repo admin. Until they are complete, an
authorized `LGTM ... merge` comment will fail at the token-mint step (the PR is
not merged). Finish all steps before relying on it.

### 1. Create the GitHub App

GitHub -> Settings (org `OpenScience-Collective`) -> Developer settings ->
GitHub Apps -> New GitHub App.

- GitHub App name: e.g. `OSA Community Auto-Merge`.
- Homepage URL: the repo URL is fine (`https://github.com/OpenScience-Collective/osa`).
- Webhook: UNCHECK "Active". No webhook is needed; the Action mints a token, the
  App does not receive events.
- Repository permissions (set ONLY these; leave everything else "No access"):
  - Pull requests: Read and write  (needed to approve and to enable auto-merge)
  - Contents: Read and write       (needed for the squash merge to write the commit)
  - Metadata: Read-only            (mandatory; GitHub sets this automatically)
- Organization permissions: none. Account permissions: none.
- "Where can this GitHub App be installed?": Only on this account.

Create the App. Note the App ID shown on the App's settings page.

### 2. Install the App on this repo

From the App's settings page -> Install App -> install on
`OpenScience-Collective`, restricted to the `osa` repository only
("Only select repositories" -> `osa`).

### 3. Generate a private key

On the App's settings page -> "Private keys" -> "Generate a private key". This
downloads a `.pem` file. Keep it secret. You cannot retrieve it again later, only
generate a new one.

### 4. Add the two repo secrets

Repo -> Settings -> Secrets and variables -> Actions -> New repository secret:

- `COMMUNITY_MERGE_APP_ID` = the numeric App ID from step 1.
- `COMMUNITY_MERGE_APP_PRIVATE_KEY` = the full contents of the `.pem` file
  (including the `-----BEGIN ... KEY-----` / `-----END ... KEY-----` lines).

CLI alternative for the secrets:

```bash
gh secret set COMMUNITY_MERGE_APP_ID --repo OpenScience-Collective/osa --body "<APP_ID>"
gh secret set COMMUNITY_MERGE_APP_PRIVATE_KEY --repo OpenScience-Collective/osa < /path/to/key.pem
```

### 5. Enable "Allow auto-merge" on the repo

The workflow uses `gh pr merge --auto`, which requires the repository-level
auto-merge feature. Repo -> Settings -> General -> "Pull Requests" -> check
"Allow auto-merge".

### 6. Registration: the workflow must live on the default branch (`main`)

GitHub discovers/registers workflows from the **default branch** only. Because
this workflow triggers on `issue_comment`, the workflow file must be present on
`main` for comments to fire it. Keep `.github/workflows/community-admin-pr-merge.yml`
in sync on both `develop` and `main`.

### 7. Branch protection / rulesets

`develop` is protected by the `protect-dev` ruleset (required checks `Lint` +
`Test (3.12)`, 0 required reviews). The App merges *through* this gate (it waits
for the checks via `--auto`); it does NOT need to be added to any bypass list. If
you later enable "restrict who can push/merge" on `develop`, add the App to that
allowlist.

## Security notes and limitations

- The `maintainers:` list is hand-maintained in each `config.yaml`; there is no
  sync with GitHub Teams or org membership. Whoever is listed is trusted to merge
  changes to that community's directory.
- The merge is **pinned to the commented commit**. A push after the `LGTM ... merge`
  comment cancels the pending merge; a maintainer must re-comment. This is the
  guard against getting an LGTM on a clean diff and then pushing changes.
- Adding/removing a maintainer, introducing a brand-new community (no base
  `config.yaml` yet), or a `config.yaml` that fails to parse are all "not
  eligible" and need a human-reviewed PR.
- A maintainer may comment on their **own** PR (GitHub allows self-comments, even
  though it disallows self-approval), so a maintainer's own scoped PR is merged
  when they comment the command.
- Community changes never reach `main` automatically; an OSA admin merges
  `develop -> main` as part of a release. Community PRs are path-restricted and
  never touch `src/version.py`, so they never cut a release on their own.
