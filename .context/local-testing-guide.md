# Local Testing Guide for EEGLAB Assistant

## Quick Test (Verify Configuration)

```bash
cd /Users/yahya/Documents/git/osa-phase1

# Run quick verification
uv run python test_eeglab_interactive.py
```

## Full Backend Testing

### 1. Set Environment Variables

```bash
# Required: OpenRouter API key for LLM
export OPENROUTER_API_KEY="your-key-here"

# Optional: API keys for admin functions (sync)
export API_KEYS="test-key-123"

# Optional: Specific EEGLAB key (if community has BYOK)
# export OPENROUTER_API_KEY_EEGLAB="eeglab-specific-key"
```

### 2. Start Backend Server

```bash
cd /Users/yahya/Documents/git/osa-phase1

# Start development server
uv run uvicorn src.api.main:app --reload --port 38528
```

Server will be available at: `http://localhost:38528`

### 3. Test Endpoints

#### A. List All Communities

```bash
curl http://localhost:38528/communities | jq
```

**Expected response:**
```json
{
  "communities": [
    {
      "id": "eeglab",
      "name": "EEGLAB",
      "description": "EEG signal processing and analysis toolbox",
      "status": "available"
    },
    {
      "id": "hed",
      "name": "HED (Hierarchical Event Descriptors)",
      ...
    }
  ]
}
```

#### B. Get EEGLAB Community Info

```bash
curl http://localhost:38528/communities/eeglab | jq
```

**Expected response:**
```json
{
  "id": "eeglab",
  "name": "EEGLAB",
  "description": "EEG signal processing and analysis toolbox",
  "status": "available",
  "documentation_count": 26,
  "github_repos": 6,
  "has_sync_config": true
}
```

#### C. Ask a Question (Simple)

```bash
curl -X POST http://localhost:38528/eeglab/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is EEGLAB?",
    "api_key": "your-openrouter-key"
  }' | jq
```

**Expected response:**
```json
{
  "answer": "EEGLAB is an interactive MATLAB toolbox...",
  "sources": [
    {
      "title": "EEGLAB quickstart",
      "url": "https://sccn.github.io/..."
    }
  ]
}
```

#### D. Ask About ICA

```bash
curl -X POST http://localhost:38528/eeglab/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How do I run ICA in EEGLAB?",
    "api_key": "your-openrouter-key"
  }' | jq
```

**Should mention:** ICA decomposition, ICLabel, artifact removal

#### E. Test Chat Endpoint

```bash
curl -X POST http://localhost:38528/eeglab/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "What preprocessing steps should I do?"}
    ],
    "api_key": "your-openrouter-key"
  }' | jq
```

**Should mention:** Filtering, re-referencing, ICA, artifact removal

### 4. Test Documentation Retrieval

The assistant should automatically retrieve docs. Test by asking specific questions:

```bash
# Should trigger retrieve_eeglab_docs tool
curl -X POST http://localhost:38528/eeglab/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How do I filter my EEG data in EEGLAB?",
    "api_key": "your-openrouter-key",
    "stream": false
  }' | jq '.tool_calls'
```

**Expected:** Should call `retrieve_eeglab_docs` with filter-related docs

### 5. Test via CLI (Easier!)

```bash
cd /Users/yahya/Documents/git/osa-phase1

# Set API key
export OPENROUTER_API_KEY="your-key-here"

# Start interactive chat
uv run osa chat --community eeglab --standalone

# Or ask single question
uv run osa ask --community eeglab "What is EEGLAB?" --standalone
```

**CLI is easier for testing because:**
- Handles API key automatically
- Shows formatted output
- Interactive mode for multi-turn conversations

## Test Questions for EEGLAB

Good test questions to verify configuration:

1. **Basic Info:**
   - "What is EEGLAB?"
   - "What can EEGLAB do?"

2. **Preprocessing:**
   - "What preprocessing steps should I do?"
   - "How do I filter EEG data?"
   - "How do I re-reference my data?"

3. **ICA:**
   - "How do I run ICA in EEGLAB?"
   - "What is ICLabel?"
   - "How do I remove artifacts with ICA?"

4. **Plugins:**
   - "What is clean_rawdata?"
   - "How do I use ASR?"
   - "What is the PREP pipeline?"

5. **Integration:**
   - "How do I use EEGLAB with BIDS?"
   - "Can I use EEGLAB with Python?"

6. **Knowledge Base (requires sync):**
   - "What are the latest issues in the eeglab repo?"
   - "Show me recent PRs in ICLabel"
   - "Papers about EEGLAB ICA"

## Troubleshooting

### Server won't start

```bash
# Check if port is already in use
lsof -i :38528

# Use different port
uv run uvicorn src.api.main:app --reload --port 38529
```

### "Assistant not found" error

```bash
# Verify EEGLAB is registered
uv run python -c "from src.assistants import discover_assistants, registry; discover_assistants(); print('eeglab' in registry)"
```

### Documentation not retrieved

- Check that `retrieve_eeglab_docs` tool is available
- Check network access to sccn.github.io
- Check tool calls in response

### Knowledge base empty

- Knowledge base requires `API_KEYS` env var for sync
- Run sync locally:
  ```bash
  export API_KEYS="test-key"
  uv run osa sync init --community eeglab
  uv run osa sync github --community eeglab --full
  ```

## Expected Behavior

**What works without knowledge sync:**
- ✓ Assistant creation
- ✓ System prompt
- ✓ Documentation retrieval (fetches from URLs)
- ✓ Answering questions about EEGLAB
- ✓ Providing guidance on workflows

**What needs knowledge sync:**
- ✗ Searching GitHub issues/PRs
- ✗ Listing recent activity
- ✗ Searching papers
- ✗ Citation counts

## Next: Epic Branch Workflow

See `epic-branch-workflow.md` for multi-phase development process.
