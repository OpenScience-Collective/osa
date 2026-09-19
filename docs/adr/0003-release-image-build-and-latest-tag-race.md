# 0003. Serialize release image builds; `:latest` follows releases, not `main`

Date: 2026-09-19 (decision made 2026, issue #379 / PR #380; backfilled)

## Status

Accepted

## Context

Production was found running `0.8.9.dev0` while `main` had already moved to
the stable `0.8.9`, discovered while checking what production actually runs
before proposing an unrelated change. Two independent causes, both verified
against real CI run data (see issue #379 and PR #380 for the full
investigation):

1. **No versioned image had been published since `0.6.2`.** `docker-build.yml`
   only triggered on `push`, so a release (which fires a `release` event, not
   a `push`) never triggered an image build. `publish.yml` was the only
   workflow already subscribed to `release`, which is why *it* fired and
   `docker-build.yml` didn't. Every 0.7.x and 0.8.x release up to and
   including 0.8.9 therefore had no immutable image - nothing to roll back
   to except hunting for the right `sha-*` tag by hand.
2. **`:latest` was written by a race.** The workflow pushed `:latest` on
   every default-branch push with no `concurrency:` guard. The v0.8.9
   release pushed two commits to `main` ten seconds apart; both builds ran,
   both wrote `:latest`, and the **older** commit's build finished last and
   won - a pure last-writer-wins race, not a version comparison.

Together: "promote to production" meant "ship whichever build happened to
finish last," and it could not be reversed without manually finding a
`sha-*` tag.

## Decision

- Add `release: [published]` to `docker-build.yml`'s triggers, so a GitHub
  release actually builds and publishes images (the `push`/`tags` trigger
  stays, for a tag pushed by hand).
- Add a `concurrency` group so builds that can write a moving tag (`latest`,
  `main`, `dev`) cannot interleave. All tag/release builds share **one**
  group rather than one per ref - keying on `github.ref` would let two
  releases published minutes apart race into two different groups and leave
  `latest` on the older one, the same bug with a narrower window. Branch
  builds (`main`, `develop`) keep a per-branch group, since they write
  different tags. `cancel-in-progress` is `true` only for `pull_request`
  events (that path never touches the registry); every other event queues
  instead of cancelling, because a cancelled run could be mid-push.
- `:latest` now means the latest **release**, not the head of `main`.
  Production pulls `:latest` (`deploy/auto-update.sh`), so this is what
  decides what it actually serves. `:main` still follows the branch for
  anyone who wants that instead.
- `workflow_dispatch` was added so a maintainer can rebuild a specific tag by
  hand (`gh workflow run docker-build.yml --ref v0.8.9`) - also the recovery
  path for releases published before this fix, which still have no image.

## Consequences

- A rollback now has a real target: a stable release has an immutable,
  versioned image, and `:latest` cannot regress to an older build by race.
- Never remove or narrow this `concurrency` block without understanding this
  history first - it is not a generic optimization, it's the actual fix for
  a production incident (see `.rules/ci_cd.md`).
- Releases published before this fix (through 0.8.9) still have no
  versioned image and must be rebuilt by hand via `workflow_dispatch` if one
  is ever needed.
