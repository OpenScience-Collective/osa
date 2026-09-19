# 0002. Sync `develop` from `main` through a pull request, not a direct push

Date: 2026-09-19 (decision made 2026, PR #373; backfilled)

## Status

Accepted

## Context

`sync-develop.yml` merges `main` into `develop` after every stable release
and bumps `develop` to the next `.dev0`. It used to do this with a direct
`git push`. That push started failing on every release once a ruleset
requiring status checks was added to `develop`:

```
remote: error: GH013: Repository rule violations found for refs/heads/develop.
remote: - 2 of 2 required status checks are expected.
! [remote rejected] develop -> develop (push declined due to repository rule violations)
```

No token permission fixes this - a ruleset requiring status checks cannot be
satisfied by a direct push at all, regardless of what the token is allowed
to do. The workflow had therefore failed on every release since the ruleset
was added, and `develop` quietly fell behind `main` each time.

A second, independent cause compounded the drift: the sync workflow only
fires after a stable release tag, so a commit made directly to `main` with
no tag (e.g. a docs commit) never reached `develop` at all. The two causes
together meant `git diff main..develop` showed real content as deletions,
and a naive `develop` -> `main` merge would have removed it from the public
repository. This was caught by hand once (PR #371, ahead of v0.8.9) before
it was fixed structurally. See PR #373 (closes #372) for the full incident
writeup, including the corrected understanding of what `CI_ADMIN_TOKEN` is
actually for - pushing the sync branch and opening the PR, not bypassing the
ruleset.

## Decision

`sync-develop.yml`'s final step opens a pull request (branch
`chore/sync-develop-after-v<version>`, pushed with `--force-with-lease`,
reusing an already-open PR for that branch if one exists) and enables
auto-merge, instead of pushing directly to `develop`. Auto-merge uses a
regular merge, not squash, so `develop` stays aware that `main`'s commits
are its ancestors - a squash here would reintroduce the same class of drift
this fix exists to prevent. If auto-merge cannot be enabled, the step warns
and leaves the PR open rather than failing silently.

## Consequences

- The sync PR faces the same required checks as any other change to
  `develop`, which is the entire point of the ruleset - `develop` is no
  longer protected from every author except automation.
- This does not fix a commit made directly to `main` with no release tag
  bypassing the sync (the trigger is still `workflow_run` on Tag/Release).
  PR #373 deliberately left that out to keep the fix reviewable; a scheduled
  check that fails when `main..develop` contains deletions was suggested as
  a follow-up and, as of this writing, has not been implemented - anyone
  picking this up should check current state before assuming it's still
  open.
- The end-to-end path (a real release tag firing the workflow) cannot be
  exercised before merging a change to this workflow; each change to it
  should be treated as untested until the next real release proves it.
