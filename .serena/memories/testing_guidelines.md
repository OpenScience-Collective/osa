# Testing Guidelines - NO MOCKS Policy

## Core Philosophy
**NO MOCKS, NO FAKE DATA**
- Mocks test your assumptions, not your code
- Real bugs hide in integration points, not unit logic
- Better approach: No test is better than a false-confidence mock test

## Testing Framework
- **pytest** with coverage reporting
- **pytest-asyncio** for async tests
- **Minimum coverage**: 70%

## Test Structure
```
tests/
├── conftest.py          # Real test fixtures
├── test_api/            # API endpoint tests
├── test_agents/         # Agent behavior tests
├── test_cli/            # CLI command tests
├── test_core/           # Core services tests
├── test_tools/          # Tool tests
└── test_integration/    # Integration tests
```

## Running Tests
```bash
# All tests with coverage
pytest --cov

# Skip slow tests
pytest -m "not slow"

# Skip integration tests
pytest -m "not integration"

# Skip LLM tests (require API key)
pytest -m "not llm"

# Run LLM tests (requires OPENROUTER_API_KEY_FOR_TESTING)
pytest -m llm
```

## Test Markers
- `integration`: Integration tests
- `slow`: Slow-running tests
- `llm`: Tests requiring real LLM API calls

## LLM and Prompt Testing
Use exemplar scenarios rooted in reality:
1. **Recorded real conversations**: Capture actual user interactions
2. **Known real-world examples**: From GitHub issues, docs, mailing lists
3. **Cached API responses**: Record actual LLM responses and replay
4. **Ground-truth Q&A pairs**: Domain expert-validated pairs
5. **Exemplar scenarios**: Based on documented real cases

## What NOT to Do
- ❌ Mock objects
- ❌ Mock datasets
- ❌ Stub services
- ❌ Artificial test scenarios
- ❌ Tests that only pass with mocks

## What TO Do
- ✅ Use real data and actual dependencies
- ✅ Test databases with real schemas
- ✅ Test against actual file systems
- ✅ Record and replay real API responses
- ✅ Extract test cases from documentation and issues

## Coverage Configuration
- Source: `src/`
- Omit: `*/tests/*`, `*/__pycache__/*`
- Excluded lines: pragma comments, NotImplementedError, TYPE_CHECKING

## Before Committing
Run the full test suite with coverage:
```bash
pytest --cov
```
Ensure coverage is >70% and all tests pass.
