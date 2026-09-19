# Continuous Rule Improvement

## Philosophy: Rules Grow from Understanding
**Think deeply:** Why did this pattern emerge? What problem does it solve?
**Learn actively:** Every project teaches something - capture it.
**Evolve thoughtfully:** Rules should guide, not constrain creativity.

## Improvement Triggers
- Pattern used 3+ times → Create rule
- Common failures → Add prevention rule (log them in `.context/scratch_history.md`
  as they happen; that file doesn't exist yet in this repo - create it the
  first time there's a failure worth logging, rather than treating it as an
  existing source to mine)
- Successful .context/research.md solutions → Standardize
- Mature .context/ideas.md concepts → Formalize
- Repeated PR feedback → Document standard
- A significant, hard-to-reverse decision → Write an ADR in `docs/adr/` (see AGENTS.md)

## Analysis Sources
1. **.context/scratch_history.md (create on first use):** Mine for
   anti-patterns once it exists; until then, there's nothing to mine here
2. **.context/research.md:** Extract proven solutions (this is where the
   original architecture decisions behind `docs/adr/0006`-`0009` came from)
3. **.context/ideas.md:** Promote design principles
4. **.context/plan.md:** Identify workflow patterns (also the source for
   `docs/adr/0006`-`0009`'s "Architecture Decisions" section)
5. **Code reviews:** Track common feedback

## Rule Updates

### Add Rules When:
- New pattern appears 3+ times
- Common bug could be prevented
- Better approach discovered
- Security/performance pattern emerges

### Modify Rules When:
- Better examples found in codebase
- Edge cases discovered
- Implementation changed
- Related rules updated

### Remove Rules When:
- Tech stack changed
- Pattern deprecated
- No longer applicable

## Quality Checks
- **Actionable:** Clear what to do
- **Specific:** No ambiguity
- **Examples:** From actual code
- **Cross-referenced:** Link related rules

## Learning-Driven Creation
**Extract wisdom, not just fixes:**
```python
# Hypothetical example of the shape a logged failure takes in
# .context/scratch_history.md (create the file for the first real one):
# "Database connections leaked after 24hrs"
# THINK: Why? Resource management issue.
# LESSON: Explicit cleanup isn't reliable.
# → Rule: Always use context managers

with get_db() as db:  # Guaranteed cleanup
    process(db)
# Not: db = get_db(); process(db); db.close()  # Risky
```

**Ask yourself:**
- What's the root cause?
- Will this prevent future issues?
- Is this a symptom of a bigger pattern?

## Thoughtful Maintenance Process
1. **Weekly:** Review .context/scratch_history.md if it exists yet - What patterns emerged?
2. **After features:** Mine .context/research.md - What worked well?
3. **Post-refactor:** Update rules - What changed fundamentally?
4. **Quarterly:** Audit all - Are rules still serving us?

**Critical questions:**
- Are developers following these rules naturally?
- Do rules prevent issues or create friction?
- What would a new team member need to know?

## The Bigger Picture
**Rules aren't just constraints** - they're collective wisdom.
**Good rules:** Enable creativity within proven patterns.
**Bad rules:** Create busywork without clear value.

**Remember:** You're codifying experience for future developers (including future you).

---
*Rules evolve from understanding. Think deeply, document failures, standardize successes thoughtfully.*
