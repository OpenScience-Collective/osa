# Contributing to Open Science Assistant (OSA)

Thanks for your interest in improving OSA! This project provides domain-specific
AI assistants for open science tools (HED, BIDS, EEGLAB, NEMAR, and more).
Contributions of all kinds are welcome: bug reports, features, documentation,
new community assistants, and tests.

By participating in this project, you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to Contribute

- **Report a bug** or **request a feature** by opening a [GitHub issue](https://github.com/OpenScience-Collective/osa/issues).
- **Fix a bug** or **implement a feature** by opening a pull request.
- **Add a new community assistant** via the YAML-driven registry (see below).
- **Improve documentation**, both in this repo and at [docs.osc.earth/osa](https://docs.osc.earth/osa/).

Before starting significant work, please open or comment on an issue so we can
discuss the approach and avoid duplicated effort.

## Development Setup

OSA uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Clone and install in development mode
git clone https://github.com/OpenScience-Collective/osa.git
cd osa
uv sync --extra dev

# Install pre-commit hooks
uv run pre-commit install
```

Run the development server:

```bash
uv run uvicorn src.api.main:app --reload --port 38528
```

## Development Workflow

All development follows: **Issue → Feature Branch (from `develop`) → PR to `develop` → Review → Merge**

**Branch strategy:**
- `main` — production releases only (stable versions, auto-deploys to prod)
- `develop` — integration branch (`.dev` versions, auto-deploys to dev)
- `feature/*` — feature branches, created from and merged back into `develop`

Version numbers are managed automatically; do **not** manually bump the version
in a pull request.

**Typical steps:**

```bash
# Start from an up-to-date develop branch
git checkout develop && git pull

# Create a feature branch (reference the issue number)
git checkout -b feature/issue-123-short-description

# ... implement with small, atomic commits ...

# Open a PR against develop
```

Pull requests should target the `develop` branch and are **squash-merged** to
keep history clean.

## Code Style

- **Formatting & linting** use [ruff](https://docs.astral.sh/ruff/):

  ```bash
  uv run ruff check --fix .
  uv run ruff format .
  ```

- **Type hints** are required.
- **Docstrings** are required for public APIs.
- Write commit messages that are concise and descriptive; keep commits atomic
  and avoid emojis.

Pre-commit hooks enforce most of these automatically once installed.

## Testing

- **No mocks** — write real tests against real data.
- **Dynamic tests** — query registries/configs instead of hardcoding values
  (see `.rules/testing_guidelines.md`).
- Aim for **>70%** coverage.

```bash
# Run the full test suite
uv run pytest tests/ -v

# Run with coverage before submitting a PR
uv run pytest --cov
```

Please make sure the test suite and linters pass before opening a pull request.

## Adding a New Community Assistant

OSA uses a YAML-driven registry, so a new assistant can often be added with just
a config file:

1. Create `src/assistants/<my-tool>/config.yaml` (see
   `src/assistants/hed/config.yaml` and `src/assistants/eeglab/config.yaml` for
   reference).
2. Validate it:

   ```bash
   uv run osa validate src/assistants/<my-tool>/config.yaml
   ```

3. Start the server — the `/{community-id}/ask` endpoint is created
   automatically.

See the [community registry documentation](https://docs.osc.earth/osa/registry/)
for the full guide, schema reference, and local testing instructions.

## Submitting a Pull Request

1. Ensure your branch is based on the latest `develop`.
2. Confirm tests and linters pass locally.
3. Open the PR against `develop` with a clear description and reference the
   related issue (e.g. `Closes #123`).
4. Address all review feedback before merging.

## Reporting Security Issues

If you discover a security vulnerability, please **do not** open a public issue.
Instead, contact the maintainers at `yahya@osc.earth` so it can be handled
responsibly.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE) that covers this project.
