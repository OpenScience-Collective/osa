# 0005. Require an approving review before merge, with a scoped admin bypass and unconditional deletion protection

Date: 2026-09-19 (decision evolved through three revisions in one session -
see History below; this record describes the state as of the last revision)

## Status

Accepted

## Context

`main` and `develop` were already governed by GitHub rulesets
(`protect-main`, `protect-dev`) that blocked direct pushes and required a
pull request for every change. However, both rulesets had
`required_approving_review_count: 0` - a PR was structurally required, but
no human approval was. `protect-main` additionally listed the `Admin`
repository role as an exempt bypass actor, so an admin could skip the PR
requirement on `main` entirely.

In practice this meant every PR in this repo's history, including
large ones like the four-phase Claude Platform migration
([0004](0004-anthropic-claude-platform-migration.md)), could be merged
without another person ever looking at it. This surfaced concretely while
preparing PR #403, a `develop` -> `main` release PR bundling 28 commits: it
was mergeable with zero approvals, by the same account that opened it.

## Decision

**Final state** (both `protect-main`, id `12110625`, and `protect-dev`, id
`12110632`):

- `required_approving_review_count: 1` on both. A PR author cannot approve
  their own PR (verified directly against PR #403).
- `bypass_actors: [{"actor_id": 5, "actor_type": "RepositoryRole",
  "bypass_mode": "exempt"}]` on both - RepositoryRole `5` is `Admin`. An
  admin/owner can bypass this ruleset's rules (including the review
  requirement) for a routine self-merge, so the review gate does not block
  the maintainer who is also the sole active admin from shipping small or
  urgent changes alone.
- Because `bypass_mode: "exempt"` exempts the actor from **every** rule in
  that ruleset, not just the `pull_request` rule, an admin bypassing
  `protect-main`/`protect-dev` would also bypass those rulesets' `deletion`
  rule (`protect-main` and `protect-dev` originally each carried their own
  `deletion` rule). That is not the intended scope of this decision, so:
- A **separate** ruleset, `prevent-branch-deletion` (id `23707055`),
  created 2026-09-19, covers both `~DEFAULT_BRANCH` and
  `refs/heads/develop`, contains only a `deletion` rule, and has
  `bypass_actors: []` / `current_user_can_bypass: "never"` - nobody,
  including admins, can bypass it. This works because GitHub evaluates all
  rulesets matching a ref cumulatively: an actor must satisfy every
  non-bypassed rule across every matching ruleset, so this ruleset's
  deletion protection holds regardless of what `protect-main`/`protect-dev`
  independently allow an admin to bypass.

Applied directly via the GitHub API (`PUT
/repos/{owner}/{repo}/rulesets/{id}` for the two existing rulesets, `POST
.../rulesets` for the new one), not through a code change, since branch
rulesets are repository configuration rather than repository content.

### History (this decision changed twice after its first version)

1. **First version:** raised `required_approving_review_count` to 1 on both
   rulesets and removed `protect-main`'s admin bypass entirely
   (`bypass_actors: []`, `current_user_can_bypass: "never"` on both).
2. **Second version:** the user asked for an admin/owner bypass to be
   restored, so a routine self-merge by an admin doesn't require pulling in
   another reviewer - `bypass_actors` on both rulesets was set back to the
   `RepositoryRole 5 (Admin)` / `exempt` entry.
3. **Third version (current):** the user pointed out that `bypass_mode:
   "exempt"` on a ruleset exempts the actor from every rule in that
   ruleset, not just the review requirement - meaning the restored admin
   bypass also silently reopened the ability to delete `main`/`develop`.
   `prevent-branch-deletion` was created as a second, non-bypassable
   ruleset to close that gap without reintroducing the review-approval
   friction the second revision was meant to relieve.

## Consequences

- Every future PR into `main` or `develop`, from a non-admin account, needs
  a real approval from someone other than the author before it can merge.
  An admin account can self-merge without another reviewer, by design.
- `main` and `develop` cannot be deleted by anyone, admin or not, regardless
  of future changes to `protect-main`/`protect-dev`'s bypass settings -
  that guarantee now lives in a ruleset most people editing branch
  protection will not think to check.
- This is a repository-configuration decision, not a code change - it does
  not show up in `git log` or a diff of this repository's tracked files, so
  this ADR is the only durable record of it, and it has already drifted
  from what an earlier version of this same document claimed within a
  single day. **Do not trust prose in this file over live state.**
  Re-verify with:

  ```bash
  gh api repos/OpenScience-Collective/osa/rulesets/12110625   # protect-main
  gh api repos/OpenScience-Collective/osa/rulesets/12110632   # protect-dev
  gh api repos/OpenScience-Collective/osa/rulesets/23707055   # prevent-branch-deletion
  ```

  Check specifically: `required_approving_review_count`, `bypass_actors`,
  and `current_user_can_bypass` on the first two; that `23707055` still
  exists, still covers both `~DEFAULT_BRANCH` and `refs/heads/develop`, and
  still has `bypass_actors: []`. If any of those don't match this document,
  trust the API output and fix this document, not the other way around.
