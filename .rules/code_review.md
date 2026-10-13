# Code Review Standards

## PR Review Toolkit
Use the `pr-review-toolkit` plugin (`/pr-review-toolkit:review-pr`) before creating a PR, per the Development Workflow in AGENTS.md. A PR that bundles already-reviewed commits (e.g. a `develop` -> `main` release PR) gets one integration-scoped reviewer, not a second full panel.

### Available Agents
- `code-reviewer` - Review for style, best practices, project guidelines
- `silent-failure-hunter` - Find inadequate error handling, silent failures
- `code-simplifier` - Simplify code while preserving functionality
- `comment-analyzer` - Check comment accuracy and maintainability
- `pr-test-analyzer` - Review test coverage quality
- `type-design-analyzer` - Analyze type design and invariants

### Workflow
**No technical debt carried forward, and no review loops.** Review converges in at most two rounds, per [review-convergence](https://github.com/neuromechanist/research-skills/blob/main/plugins/project/skills/pr-review-toolkit/references/review-convergence.md).
1. **Rounds:** Round 1 is a full review: one reviewer for docs-only PRs or under 50 changed lines, otherwise a panel of at most 3. Round 2 is one reviewer on the fix commits only, and runs only if round 1 was substantial: an accepted Critical; fixes over 150 non-test, non-doc lines; a public contract change; authorization, encryption, data integrity, or concurrency touched; or the approach replaced. It reports only Critical or Important defects the fixes introduce or fail to resolve. No third round unless the owner asks.
2. **Outcomes:** Every finding gets one outcome: fixed (suggestions included), rejected with a one-sentence reason, or filed as an issue (only for code the PR did not touch). Accepted findings are fixed BEFORE merge, never in a follow-up PR or the next release.
3. **Feature PRs:** Fix commits go on the feature branch, pushed once per round. No new PR.
4. **Release PRs (`develop` -> `main`):** One integration-scoped reviewer, since the feature PRs were already reviewed. `develop` blocks direct pushes, so all fixes from a round go in ONE fix PR into `develop`, never one PR per finding; it gets the round-2 delta review only if the fixes were substantial. Open the release PR only when it is ready, and merge it promptly. Merge methods are unchanged (squash into `develop`, regular merge into `main`).
5. **Merge gate:** The round-1 report is in, every finding has an outcome, and CI is green. Never merge while a review is still running.

Use the Sonnet model for PR review agents (see AGENTS.md).

## Manual Code Review Checklist

### Before Committing
- [ ] Code compiles/runs without warnings
- [ ] Tests pass (real tests, no mocks)
- [ ] No debug code left (print statements, TODO hacks)
- [ ] No sensitive data in code or logs

### Logic & Safety
- [ ] Error cases handled with specific exceptions
- [ ] No silent failures (empty catch blocks, bare except)
- [ ] Resource cleanup (files, connections, context managers)
- [ ] Input validation at system boundaries

### Code Quality
- [ ] Functions do one thing
- [ ] Clear naming (no abbreviations)
- [ ] No magic numbers (use constants)
- [ ] No premature abstraction (three uses before extracting)

### Never Do This
- [ ] No `except Exception: pass` or empty catch blocks
- [ ] No `# type: ignore` without explanation
- [ ] No commented-out code (delete it; git has history)
- [ ] No `TODO` without a linked issue
- [ ] No backward-compatibility shims; replace, don't deprecate

## Review Comments
When leaving review comments:
- Be specific about the issue
- Suggest a fix when possible
- Distinguish blocking vs. non-blocking issues
- Reference documentation or examples

## Branch Protection

`main` and `develop` both require at least one approving review before merge
(GitHub rulesets `protect-main` / `protect-dev`; see
`docs/adr/0005-enforce-pr-approval-requirement.md` for why and for the exact
current state). A PR author cannot approve their own PR — as a non-admin,
request review from another maintainer rather than assuming a bypass is
available to you. An Admin-role account can bypass the review requirement
for a routine self-merge, but branch deletion is protected separately by a
non-bypassable ruleset regardless of admin status — don't treat "I have
admin" as license to force through a deletion or a review-less merge on a
change that actually warrants review.

---
*No technical debt carried forward. Review early, review often.*
