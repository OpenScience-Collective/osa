# 0009. Astral tooling (uv, ruff) for Python dependency management and linting

Date: 2026-09-19 (decision predates this ADR; sourced from
`.context/plan.md`'s "Architecture Decisions", backfilled)

## Status

Accepted

## Context

The project needed a Python package manager and a linter/formatter. The
incumbent tools (pip/conda/virtualenv for environments; Flake8, Black,
isort separately for style) are slower and more fragmented than newer
alternatives.

## Decision

Standardize on Astral's tooling: `uv` for dependency/environment management
(replacing pip, conda, virtualenv) and `ruff` for linting and formatting
(replacing Flake8, Black, and isort as one tool). Both are Rust-based and
reported roughly 10-100x (`uv`) and 30x (`ruff`) faster than the tools they
replace, are drop-in-compatible with the `pyproject.toml`-based workflow,
and were already being adopted by major projects in the same ecosystem
(FastAPI, Pydantic, pandas, Django at the time of the decision).

This is also a hard global rule (not just a project preference): Python
tooling must be UV-only, and linting/type-checking must be ruff + `ty`
(never mypy) - see `.rules/python.md`.

## Consequences

- CI lint/test time is materially lower than it would be with pip + the
  Flake8/Black/isort stack.
- `pip`, `conda`, `virtualenv`, and `mypy` are not just discouraged but
  actively wrong to reach for in this codebase - a contributor or agent
  defaulting to familiar tooling from other projects will be working
  against the grain (this drift already happened once: see the commit
  history of `.rules/python.md`, which had regressed to documenting
  conda/pip/mypy before being corrected back to this decision).
- `ty` is used for type checking instead of the more established `mypy`;
  expect a smaller ecosystem of examples/integrations for `ty`-specific
  issues than for mypy.
