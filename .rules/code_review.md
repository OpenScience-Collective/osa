# Code Review Standards

## PR Review Toolkit
Use the `pr-review-toolkit` plugin (`/pr-review-toolkit:review-pr`) before creating a PR, and again on any PR that bundles multiple already-merged commits (e.g. a `develop` -> `main` release PR), per the Development Workflow in AGENTS.md.

### Available Agents
- `code-reviewer` - Review for style, best practices, project guidelines
- `silent-failure-hunter` - Find inadequate error handling, silent failures
- `code-simplifier` - Simplify code while preserving functionality
- `comment-analyzer` - Check comment accuracy and maintainability
- `pr-test-analyzer` - Review test coverage quality
- `type-design-analyzer` - Analyze type design and invariants

### Workflow
**No technical debt carried forward.** Address all findings, not just critical ones.
1. Create PR with `gh pr create`
2. Run code review agents on the changes (all agents in parallel)
3. Address ALL findings: critical, important, suggestions, and nice-to-haves
4. Only skip genuine false positives or intentionally different design choices
5. Document skipped findings with clear reasoning (false positive / intended behavior)

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
