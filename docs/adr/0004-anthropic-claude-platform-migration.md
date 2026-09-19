# 0004. Serve platform-funded requests from the Claude Platform on AWS, not OpenRouter

Date: 2026-09-19 (decision made 2026, epic #360 / PR #395 and phases
#365, #368, #381, #383; backfilled)

## Status

Accepted

## Context

OSA originally routed platform-funded (non-BYOK) requests through
OpenRouter. The migration to the Claude Platform on AWS - the
Anthropic-operated Messages API billed through AWS Marketplace, explicitly
**not** Amazon Bedrock - was driven by wanting native capabilities OpenRouter
couldn't give a straight passthrough of: extended thinking, prompt caching
with real cost accounting, and, most importantly for a project whose first
design principle is "researchers need accurate, citation-backed answers,"
Anthropic's native `search_result` citation blocks, which cite the exact
span of retrieved text a claim came from rather than relying on the model to
mention a URL in prose.

The Claude Platform on AWS is also where HEDit (this project's sibling
deployment) already runs, and OSA already had its own key for it.

## Decision

Migrate platform-funded routing to the Claude Platform on AWS in four
reviewed phases, merged into an epic branch and landed on `develop` as one
regular (non-squash) merge so the phase history survives:

1. **Phase 1 - provider layer** (PR #365): `src/core/services/anthropic_llm.py`
   adds the offered-model registry (`claude-haiku-4-5` default,
   `claude-sonnet-5`), per-model thinking policy, two credential modes
   (server key pinned to the AWS endpoint + workspace header, vs. BYOK
   pinned to `api.anthropic.com`), and prompt caching via a `ChatAnthropic`
   subclass. Routing itself is untouched - OpenRouter keeps serving traffic
   until Phase 2.
2. **Phase 2 - routing and model policy** (PR #368): flips `_resolve_provider`
   /`_select_model` to prefer Anthropic; BYOK still wins, then a community
   key, then the platform key. Bare first-party model ids are mapped to
   equivalent OpenRouter slugs so an OpenRouter-funded community isn't
   silently switched to a different model family depending on which key
   paid - deliberately corrected mid-implementation once that risk was
   spotted, see PR #368's description for the specific near-miss.
3. **Phase 3 - widget and deployment** (PR #381): widget model menu is
   served from `offered_models`, not hard-coded; Anthropic BYOK key field
   added; `.env.example` / `deploy/docker-compose.yml` / `deploy/Dockerfile`
   brought in line; cross-language drift tests added so the widget's model
   list and the Cloudflare Worker's header allowlist can't silently diverge
   from Python again.
4. **Phase 4 - inline citations** (PR #383): retrieval tools return native
   `search_result` blocks (Anthropic-only; gated per-request on provider so
   the OpenRouter BYOK path is byte-for-byte unchanged and falls back to a
   prompt-based citation rule instead).

OpenRouter is retained as a BYOK-only option, so a user bringing their own
OpenRouter key keeps access to arbitrary models; it is fully retired as a
platform-funded route.

## Consequences

- Every offered model must be one of the two Anthropic ids; adding a model
  means updating `OFFERED_MODELS` and its cross-language drift tests, not
  just a config file.
- Citations are only native (span-accurate) on the Anthropic path; the
  OpenRouter BYOK path still relies on a prompt rule and is explicitly
  documented as the weaker of the two.
- Several follow-up fixes landed on the same epic branch rather than
  blocking it (CLI key handling, FAQ generation moving to the platform, a
  citable-text cap, an auth-header bug where an unusable key was accepted
  but silently billed the platform's own key) - see PR #395's table of
  linked issues/PRs for the full list.
- Two problems found during the epic were filed rather than fixed inline,
  deliberately, to keep the epic reviewable; check open issues linked from
  PR #395 before assuming they're resolved.
- This is the first large, multi-phase change to ship under this repo's
  review process before [0005](0005-enforce-pr-approval-requirement.md)
  made review structurally required - each phase was reviewed on its own PR
  by choice, not by rule.
