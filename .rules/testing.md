# Testing Standards - NO MOCKS Policy

## Core Philosophy: Test Reality, Not Fiction
**Why NO MOCKS?** Mocks test your assumptions, not your code.
**Real bugs** hide in integration points, not unit logic.
**Better approach:** No test is better than a false-confidence mock test.

## [STRICT] NO MOCKS, NO FAKE DATA
Never use mocks, stubs, or fake datasets. If real testing isn't possible, don't write tests.
- **No mock objects** - Use real implementations
- **No mock datasets** - Use actual sample data
- **No stub services** - Connect to real test instances
- **Alternative:** Ask user for sample data or test environment setup

## When to Write Tests
- **DO:** Test with real data and actual dependencies
- **DO:** Use test databases with real schemas
- **DO:** Test against actual file systems
- **DON'T:** Write tests if only mocks would work
- **DON'T:** Create artificial test scenarios

## Test Structure
```
tests/
  conftest.py          # Real test fixtures
  sample_data/         # Actual data samples (user-provided)
    valid/
    invalid/
  integration/         # Tests with real dependencies
    test_database.py   # Real DB connection
    test_api.py        # Real API calls
```

## Frameworks
- **Python:** `pytest` with real fixtures and `coverage`
- **Database:** Use test DB with real migrations
- **APIs:** Test against staging/local instances

## Writing Real Tests
```python
# GOOD: Tests actual behavior
def test_user_creation(real_db):
    """Tests that users are actually persisted."""
    user = User.create(email="test@example.com")
    assert real_db.query(User).filter_by(email="test@example.com").first()

# BAD: Tests nothing meaningful - NEVER DO THIS
# def test_user_creation(mock_db):
#     mock_db.return_value = User()
```

## LLM and Prompt Testing

For prompts, responses, and AI-related functionality, use **exemplar scenarios rooted in reality**:

### Approaches (in order of preference)
1. **Recorded real conversations**: Capture actual user interactions as test fixtures
2. **Known real-world examples**: Extract cases from documentation, GitHub issues, mailing lists
3. **Cached API responses**: Record actual LLM responses and replay them
4. **Ground-truth Q&A pairs**: Domain expert-validated question/answer pairs
5. **Exemplar scenarios**: Write scenarios based on documented real cases

### Test Structure for LLM Components
```
tests/
  fixtures/
    conversations/       # Recorded real conversations
    api_responses/       # Cached real API responses
    ground_truth/        # Expert-validated Q&A pairs
  test_prompts.py        # Prompt format/structure tests
  test_responses.py      # Response parsing tests
  test_agents.py         # Agent behavior with real examples
```

### Example: Testing with Real Examples
```python
# GOOD: Uses real example from HED documentation
def test_hed_validation_known_error():
    """Tests validation catches real error from GitHub issue #123."""
    # This HED string caused actual user confusion
    bad_hed = "Sensory-event, (Red, Blue)"  # Real example
    result = validate_hed(bad_hed)
    assert not result.valid
    assert "parentheses" in result.error.lower()

# GOOD: Uses cached real API response
def test_assistant_response(cached_openai_response):
    """Tests response parsing with actual API output."""
    parsed = parse_response(cached_openai_response)
    assert parsed.has_citations
```

## When Real Testing Seems Impossible
**Think creatively before giving up:**
- Can you use Docker for a test database?
- Can you record real API responses for replay?
- Can you get anonymized production data samples?
- Can you create a minimal test environment?
- Can you find real examples in documentation or issues?

**Skipping is the LAST RESORT.** Before skipping:
1. Search GitHub issues for real examples
2. Check mailing list archives for actual user scenarios
3. Look at documentation for sample inputs/outputs
4. Ask domain experts for ground-truth examples

**If truly impossible:** Document what's needed and why, then skip with clear explanation.

---
*NO MOCKS. Real tests build real confidence. Skipping is last resort.*
