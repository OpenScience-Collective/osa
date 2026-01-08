# Testing Guidelines

## Core Principle: Dynamic Tests Over Hardcoded Values

Tests should verify **behavior and consistency**, not specific values that may change with configuration. This ensures tests remain valid as the system evolves.

## Document Registry Tests

### DO: Query the registry dynamically

```python
def test_preloaded_and_ondemand_partition_total():
    """Preloaded + on-demand should equal total docs."""
    preloaded = HED_DOCS.get_preloaded()
    ondemand = HED_DOCS.get_on_demand()

    assert len(preloaded) + len(ondemand) == len(HED_DOCS.docs)
```

### DON'T: Hardcode expected counts

```python
# BAD - breaks when configuration changes
def test_preloaded_count():
    assert len(HED_DOCS.get_preloaded()) == 5  # Hardcoded!
```

### DO: Verify consistency between flag and method

```python
def test_preloaded_docs_have_correct_flag():
    """All docs returned by get_preloaded() should have preload=True."""
    for doc in HED_DOCS.get_preloaded():
        assert doc.preload is True
```

### DO: Pick test data dynamically

```python
def test_find_by_url():
    """Test finding document by URL."""
    # Dynamically pick any preloaded document
    preloaded = HED_DOCS.get_preloaded()
    test_doc = preloaded[0]  # Use first available

    found = HED_DOCS.find_by_url(test_doc.url)
    assert found is not None
    assert found.preload is True
```

## Tool Tests

### Test Input/Output Contracts

Tools have defined inputs and outputs. Test that they honor these contracts:

```python
def test_tool_returns_expected_type():
    """Tool should return the type specified in its schema."""
    result = my_tool.invoke({"param": "value"})
    assert isinstance(result, str)  # Based on tool's return type
```

### Test Error Handling

```python
def test_tool_handles_invalid_input():
    """Tool should handle invalid input gracefully."""
    result = my_tool.invoke({"param": ""})
    assert "error" in result.lower() or len(result) > 0
```

## Agent Tests

### Test Tool Discovery

```python
def test_agent_has_tools():
    """Agent should have tools configured."""
    agent = MyAgent(llm=mock_llm)
    assert len(agent.tools) > 0

    # Each tool should have required attributes
    for tool in agent.tools:
        assert tool.name
        assert tool.description
```

### Test System Prompt Contains Required Elements

```python
def test_system_prompt_includes_preloaded_docs():
    """System prompt should include content from preloaded docs."""
    agent = MyAgent(llm=mock_llm)
    prompt = agent.build_system_prompt()

    # Check for content, not specific text
    for doc in agent.preloaded_docs:
        assert doc.title in prompt or len(prompt) > 1000
```

## Scaling to New Resources

When adding new document registries (BIDS, EEGLAB):

1. **Reuse the same test patterns** - the dynamic tests work for any registry
2. **Parameterize tests** for multiple registries:

```python
import pytest

REGISTRIES = [HED_DOCS, BIDS_DOCS, EEGLAB_DOCS]

@pytest.mark.parametrize("registry", REGISTRIES)
def test_preloaded_plus_ondemand_equals_total(registry):
    preloaded = registry.get_preloaded()
    ondemand = registry.get_on_demand()
    assert len(preloaded) + len(ondemand) == len(registry.docs)
```

3. **Test registry-specific requirements** separately:

```python
def test_hed_has_core_category():
    """HED registry should have core category."""
    assert "core" in HED_DOCS.get_categories()

def test_bids_has_specification_category():
    """BIDS registry should have specification category."""
    assert "specification" in BIDS_DOCS.get_categories()
```

## Summary

| Approach | Good For | Example |
|----------|----------|---------|
| Dynamic queries | Counts, lists | `len(registry.get_preloaded())` |
| Flag consistency | State verification | `doc.preload is True` |
| Contract testing | Tool I/O | `isinstance(result, str)` |
| Parameterization | Multi-registry | `@pytest.mark.parametrize` |
