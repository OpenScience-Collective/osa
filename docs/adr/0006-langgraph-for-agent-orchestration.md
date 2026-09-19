# 0006. LangGraph for agent orchestration

Date: 2026-09-19 (decision predates this ADR; sourced from
`.context/plan.md`'s "Architecture Decisions" section, backfilled)

## Status

Accepted

## Context

OSA needs multi-turn chat with tool-calling assistants per research
community (HED, BIDS, EEGLAB, ...), each with its own tools and system
prompt, and room to add new assistant types over time.

## Decision

Use LangGraph for agent orchestration (`src/agents/`: `BaseAgent`,
`SimpleAgent`, `ToolAgent`), because it gives:

- Clean state management for multi-turn chat out of the box.
- Built-in tool-calling patterns, which this project relies on heavily
  (see [0004](0004-anthropic-claude-platform-migration.md) for how deep the
  tool/citation integration goes).
- A structure that's easy to extend with new assistant types without
  redesigning the state machine each time.

## Consequences

- New assistant behavior is expressed as LangGraph state/nodes rather than
  ad hoc control flow, which keeps community-specific assistants
  structurally similar to each other.
- The project is coupled to LangGraph's and langchain-core's abstractions
  and upgrade cadence; the Claude Platform migration
  ([0004](0004-anthropic-claude-platform-migration.md)) already hit at
  least one case where a langchain-anthropic upgrade could silently break
  payload shape, caught only because a test asserted the exact payload
  rather than trusting the abstraction.
