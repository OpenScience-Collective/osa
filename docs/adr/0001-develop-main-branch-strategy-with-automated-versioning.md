# 0001. `develop`/`main` branch strategy with fully automated version bumping

Date: 2026-09-19 (decision predates this ADR; backfilled from `AGENTS.md`
and `.github/workflows/`)

## Status

Accepted

## Context

OSA needed a way to keep a deployed `develop` integration environment moving
fast while giving `main` a stable, always-releasable state, without a human
having to manually resolve `src/version.py` merge conflicts on every release
PR - the kind of toil that gets skipped under time pressure and then causes
drift (see [0002](0002-sync-develop-to-main-via-pull-request.md) and
[0003](0003-release-image-build-and-latest-tag-race.md) for two concrete
incidents that came from exactly that kind of drift).

## Decision

- `main` carries only stable versions (no `.dev` suffix) and only changes via
  a `develop` -> `main` pull request. Merging `main` does **not** deploy;
  publishing a stable release does (issue #379).
- `develop` is the integration branch, carries a `.devN` suffix, and
  auto-deploys to the dev environment.
- `feature/*` branches are created from and merged back into `develop`.
- Version bumping is fully automated, not manual:
  - `auto-bump-dev.yml` increments `.devN` on every push to `develop`
    (skipping bot commits, `Bump version to ...` messages, `[skip ci]`,
    `[skip-bump]`).
  - `ensure-stable-version.yml` strips the `.dev` suffix on `main` if one
    slips through.
  - `tag-release.yml` tags `main` when the version is stable.
  - `release.yml` creates the GitHub release.
  - `sync-develop.yml` merges `main` back into `develop` after a release and
    bumps `develop` to the next `<patch>.dev0`, closing the loop without a
    manual "resolve `src/version.py` conflict in the release PR" step.
- `scripts/bump_version.py` exists for the rare manual bump but is not part
  of the normal release flow.

## Consequences

- Contributors never hand-edit `src/version.py` in a feature or release PR;
  doing so fights the automation rather than helping it.
- The automation is itself the thing that can silently drift or fail (see
  [0002](0002-sync-develop-to-main-via-pull-request.md)): a workflow that
  pushes directly instead of through a reviewed PR, or that only triggers on
  a narrow event, can leave `develop` and `main` disagreeing for a long time
  before anyone notices. Treat changes to these workflows with the same
  scrutiny as changes to the release process itself.
- Because `main` only allows a regular merge (not squash) per its branch
  ruleset, the individual commit history of a release survives on `main` -
  useful for `git bisect` and for the "what shipped in this release" story,
  at the cost of a noisier `main` history than a squashed one would have.
