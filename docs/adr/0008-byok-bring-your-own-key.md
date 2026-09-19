# 0008. Support BYOK (bring your own key) alongside platform-funded requests

Date: 2026-09-19 (decision predates this ADR; sourced from
`.context/plan.md`'s "Architecture Decisions", backfilled)

## Status

Accepted

## Context

OSA serves small research communities from lab servers with modest
infrastructure budgets. Some researchers already have their own Anthropic
or OpenRouter API keys and would rather use those than share a server-wide
budget; requiring every request to be platform-funded would put the full
cost of every user's usage on the project's own key.

## Decision

Support BYOK as a first-class credential path alongside platform- and
community-funded keys: a caller can supply their own key (originally
OpenRouter-shaped, now also Anthropic-shaped per
[0004](0004-anthropic-claude-platform-migration.md)) via a request header,
and the request is billed and rate-limited against that key rather than the
server's. Reasons: researchers may already have their own API keys, it
reduces server cost, and the user pays for their own usage rather than the
project subsidizing it.

## Consequences

- Every provider/credential-resolution code path (`_resolve_provider`,
  `ProviderChoice`, `ByokCredential`, `_check_model_cost`) has to reason
  about three key sources - BYOK, community, platform - not one, which is a
  recurring source of subtle bugs when the three interact (see
  [0004](0004-anthropic-claude-platform-migration.md) and this repo's PR
  #394, where a since-removed `X-OpenAI-API-Key` header was accepted for
  the auth bypass decision and then dropped, silently falling through to
  run - and bill - on the community's key or the platform's, per PR #394's
  own account, rather than being rejected).
- The security model for BYOK endpoints assumes "a junk value fails upstream
  against the caller's own provider" - true for endpoints that actually
  spend the credential against an LLM call, and worth re-checking before
  reusing the same auth dependency on an endpoint that doesn't spend it.
- BYOK is the reason OpenRouter is kept in the codebase at all after
  [0004](0004-anthropic-claude-platform-migration.md) retired it as a
  platform-funded route - it remains fully supported as a BYOK-only path so
  a user with their own OpenRouter key keeps access to whatever models
  OpenRouter offers.
