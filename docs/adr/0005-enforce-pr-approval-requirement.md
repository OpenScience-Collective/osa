# 0005. Require an approving review before merge, with no admin bypass

Date: 2026-09-19

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

Both `protect-main` and `protect-dev` rulesets were updated:

- `required_approving_review_count` raised from `0` to `1` on both.
- `protect-main`'s `Admin` bypass actor was removed
  (`current_user_can_bypass` went from `exempt` to `never` on both
  rulesets).

This was applied directly via the GitHub API (`PUT
/repos/{owner}/{repo}/rulesets/{id}`), not through a code change, since
branch rulesets are repository configuration rather than repository
content.

## Consequences

- Every future PR into `main` or `develop`, including small or urgent ones,
  needs a real approval from someone other than the author before it can
  merge. With two collaborators besides the repository owner, this means
  every merge now depends on one of them being available to review.
- The author of a PR cannot approve their own PR (verified directly against
  PR #403: the account that opened it could not be added as its own
  reviewer). Plan reviewer availability accordingly, especially for release
  PRs.
- No bypass exists for anyone, including admins, on either branch. An
  emergency fix has no faster path than a normal reviewed PR; if that turns
  out to be too rigid in practice, revisit this ADR rather than quietly
  re-adding a bypass actor.
- This is a repository-configuration decision, not a code change - it does
  not show up in `git log` or a diff of this repository's tracked files, so
  this ADR is the only durable record of it. Check the live ruleset state
  (`gh api repos/OpenScience-Collective/osa/rulesets/<id>`) rather than
  assuming this document is still accurate if it's been a while.
