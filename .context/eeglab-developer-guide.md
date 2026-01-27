# EEGLab Assistant Developer Guide

## Architecture Overview

The EEGLab assistant uses OSA's community framework with plugin extensions:

```
src/assistants/eeglab/
├── config.yaml       # Community configuration (single source of truth)
└── tools.py          # Plugin tools (Phase 2 & 3)

~/.config/osa/data/knowledge/eeglab.db   # SQLite database
├── github_items                # Issues/PRs from repos
├── papers                      # Academic papers
├── docstrings                  # Function docs (Phase 2)
├── mailing_list_messages       # Raw archive (Phase 3)
└── faq_entries                 # FAQ summaries (Phase 3)
```

## Component Breakdown

### config.yaml
- **Purpose:** Single source of truth for community configuration
- **Contains:**
  - System prompt with tool usage guidelines
  - Documentation sources (preloaded vs on-demand)
  - GitHub repos to track
  - Paper DOIs and search queries
  - Mailing list configuration
  - Plugin tool registration
- **Location:** `src/assistants/eeglab/config.yaml`

### tools.py (Plugin Tools)
- **Purpose:** Community-specific tools that can't be auto-generated
- **Tools:**
  - `search_eeglab_docstrings`: Search MATLAB/Python function documentation
  - `search_eeglab_faqs`: Search mailing list FAQ database
- **Pattern:** Use `@tool` decorator from langchain_core.tools
- **Location:** `src/assistants/eeglab/tools.py`

### Database Schema
- **github_items:** Issues/PRs with full text and metadata
- **papers:** Academic papers with abstracts and citations
- **docstrings:** Function signatures and documentation
- **mailing_list_messages:** Raw messages with threading info
- **faq_entries:** LLM-generated Q&A summaries with quality scores

All tables use FTS5 for full-text search.

## Adding New Tools

### Option 1: Auto-Generated Tools (Preferred)
For simple documentation retrieval, use YAML config:

```yaml
documentation:
  - title: "New Tutorial"
    url: https://example.com/tutorial.html
    source_url: https://raw.githubusercontent.com/.../tutorial.md
    category: tutorial
    preload: false
```

### Option 2: Custom Plugin Tools
For complex logic (API calls, special formatting):

1. **Create tool function in tools.py:**

```python
from langchain_core.tools import tool

@tool
def my_eeglab_tool(query: str, limit: int = 5) -> str:
    """Tool description for LLM.

    Args:
        query: Search query
        limit: Max results

    Returns:
        Formatted results
    """
    # Implementation
    from src.knowledge.db import get_db_path
    from src.knowledge.search import search_something

    db_path = get_db_path("eeglab")
    if not db_path.exists():
        return "Database not initialized"

    results = search_something(query, project="eeglab", limit=limit)
    return format_results(results)
```

2. **Export in `__all__`:**

```python
__all__ = ["search_eeglab_docstrings", "search_eeglab_faqs", "my_eeglab_tool"]
```

3. **Register in config.yaml:**

```yaml
extensions:
  python_plugins:
    - module: src.assistants.eeglab.tools
      tools:
        - search_eeglab_docstrings
        - search_eeglab_faqs
        - my_eeglab_tool
```

## Maintaining the Knowledge Base

### GitHub Sync (Weekly Recommended)

```bash
# Sync all configured repos
osa sync github --community eeglab

# Sync specific repo
osa sync github --community eeglab --repo sccn/eeglab
```

**What it syncs:**
- Issues (open and closed)
- Pull requests with comments
- Recent commits
- Releases

**Storage:** `~/.config/osa/data/knowledge/eeglab.db` table `github_items`

### Docstring Sync (After Code Updates)

```bash
# Sync from local clones (faster, recommended)
osa sync docstrings --community eeglab \
  --repo ~/git/eeglab \
  --repo ~/git/ICLabel

# Sync from GitHub (slower, network intensive)
osa sync docstrings --community eeglab
```

**What it extracts:**
- MATLAB function headers (% comments)
- Python docstrings (""" strings)
- Function signatures
- Parameter descriptions

**Storage:** `~/.config/osa/data/knowledge/eeglab.db` table `docstrings`

### Mailing List Sync (Monthly Recommended)

```bash
# Full sync (first time only, takes hours!)
osa sync mailman --community eeglab --start-year 2004

# Incremental sync (recent years only)
osa sync mailman --community eeglab --start-year 2024
```

**What it scrapes:**
- Message subject, body, author, date
- Thread structure (in-reply-to)
- Links to original archives

**Storage:** `~/.config/osa/data/knowledge/eeglab.db` table `mailing_list_messages`

**Important:** This can take several hours for full history. Use screen/tmux or run overnight.

### FAQ Generation (After Mailing List Sync)

```bash
# Generate FAQ summaries from threads
osa sync faq --community eeglab --quality 0.7

# Process all threads (slow!)
osa sync faq --community eeglab --quality 0.5 --max-threads 10000

# Process specific list
osa sync faq --community eeglab --list-name eeglablist
```

**What it does:**
- Groups messages into threads
- Scores thread quality (how complete is the answer?)
- Uses LLM to summarize Q&A
- Extracts question, answer, category, tags
- Stores with quality score for ranking

**Storage:** `~/.config/osa/data/knowledge/eeglab.db` table `faq_entries`

**Cost:** Uses OpenRouter API (costs per thread). Set quality threshold high (0.7+) to process only best threads.

### Paper Sync (Quarterly Recommended)

```bash
# Sync configured papers and queries
osa sync papers --community eeglab
```

**What it syncs:**
- Papers from configured DOIs
- Papers from search queries
- Citation counts
- Abstracts and metadata

**Storage:** `~/.config/osa/data/knowledge/eeglab.db` table `papers`

## Testing

### Run Integration Tests

```bash
# All EEGLAB tests
uv run pytest tests/test_assistants/test_eeglab_integration.py -v

# Specific test class
uv run pytest tests/test_assistants/test_eeglab_integration.py::TestEEGLabTools -v

# With coverage
uv run pytest tests/test_assistants/test_eeglab_integration.py --cov=src/assistants/eeglab
```

### Test Individual Tools

```python
from src.assistants import discover_assistants, registry
from unittest.mock import MagicMock

# Discover and create assistant
discover_assistants()
assistant = registry.create_assistant('eeglab', model=MagicMock())

# List tools
for tool in assistant.tools:
    print(f"- {tool.name}: {tool.description[:50]}")

# Test plugin tool directly
from src.assistants.eeglab.tools import search_eeglab_docstrings
result = search_eeglab_docstrings.invoke({"query": "pop_loadset"})
print(result)
```

## Performance Monitoring

### Database Query Times

```bash
# Check query performance
sqlite3 ~/.config/osa/data/knowledge/eeglab.db

-- Check indexes exist
.indexes mailing_list_messages
.indexes faq_entries
.indexes docstrings

-- Profile a query
.timer on
SELECT * FROM faq_entries
WHERE list_name = 'eeglablist'
AND quality_score >= 0.7
LIMIT 5;

-- Check query plan (should show index usage)
EXPLAIN QUERY PLAN
SELECT * FROM faq_entries
WHERE list_name = 'eeglablist'
ORDER BY quality_score DESC
LIMIT 5;
```

**Expected performance:**
- FAQ search: < 100ms
- Docstring search: < 100ms
- Full-text search: < 200ms

### API Response Times

```bash
# Test assistant response time
time curl -X POST http://localhost:38528/eeglab/ask \
  -H "Content-Type: application/json" \
  -d '{"message": "How do I filter EEG data?", "model": "haiku"}' \
  | jq -r '.response' | head -20
```

**Target:** < 3 seconds for Haiku, < 10 seconds for Sonnet

## Troubleshooting

### Plugin Tools Not Loading

**Symptom:** Tool count is 4 instead of 6

**Check:**
```python
from src.assistants import discover_assistants, registry
discover_assistants()
info = registry.get('eeglab')
print(info.community_config.extensions)  # Should show python_plugins
```

**Fix:**
- Verify tools.py exports in `__all__`
- Verify config.yaml has `extensions.python_plugins` section
- Check import errors in logs

### Database Not Found

**Symptom:** "Database not initialized" errors

**Check:**
```bash
ls -lh ~/.config/osa/data/knowledge/eeglab.db
```

**Fix:**
```bash
# Initialize by running any sync
osa sync github --community eeglab
```

### Search Returns No Results

**Check:**
```bash
# Verify table has data
sqlite3 ~/.config/osa/data/knowledge/eeglab.db "SELECT COUNT(*) FROM faq_entries"
sqlite3 ~/.config/osa/data/knowledge/eeglab.db "SELECT COUNT(*) FROM docstrings"
```

**Fix:** Run appropriate sync command to populate data

### Slow Queries

**Check:**
```bash
# Verify indexes exist
sqlite3 ~/.config/osa/data/knowledge/eeglab.db ".indexes faq_entries"
```

**Fix:**
```sql
-- Recreate indexes if missing
CREATE INDEX IF NOT EXISTS idx_faq_list ON faq_entries(list_name);
CREATE INDEX IF NOT EXISTS idx_faq_quality ON faq_entries(quality_score);
CREATE INDEX IF NOT EXISTS idx_faq_category ON faq_entries(category);
```

## Contributing

### Code Style
- Follow existing patterns in `src/assistants/hed/tools.py` for reference
- Use `@tool` decorator for plugin tools
- Include comprehensive docstrings with examples
- Type hints required

### Testing
- Add tests to `tests/test_assistants/test_eeglab_integration.py`
- Test both happy path and error cases
- Mock external dependencies
- Use real database operations (no mocks for DB)

### Documentation
- Update this guide when adding features
- Update user guide with new tool capabilities
- Add examples for complex workflows

## Reference Implementation

See HED assistant for similar patterns:
- `src/assistants/hed/config.yaml` - Similar structure
- `src/assistants/hed/tools.py` - Tool implementation patterns
- `tests/test_assistants/test_discovery.py` - Discovery testing patterns

## Support

- **Issues:** https://github.com/hed-standard/osa/issues
- **Discussions:** https://github.com/hed-standard/osa/discussions
- **Mailing List:** hed-announce@googlegroups.com
