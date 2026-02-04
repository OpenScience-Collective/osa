# Local Testing Guide for Community Assistants

Quick reference for testing any community assistant locally. For the full guide, see https://docs.osc.earth/osa/registry/local-testing/

## Quick Validation

```bash
# Validate config loads
uv run pytest tests/test_core/ -k "community" -v

# Or programmatically
uv run python -c "
from pathlib import Path
from src.core.config.community import CommunityConfig
config = CommunityConfig.from_yaml(Path('src/assistants/COMMUNITY_ID/config.yaml'))
print(f'Loaded: {config.name} with {len(config.documentation)} docs')
"
```

## Environment Variables

```bash
export OPENROUTER_API_KEY="your-key-here"
# Optional: for sync operations
export API_KEYS="test-key-123"
# Optional: community-specific key
# export OPENROUTER_API_KEY_COMMUNITY="key"
```

## Server Testing

```bash
# Start dev server
uv run uvicorn src.api.main:app --reload --port 38528

# List communities (verify yours appears)
curl http://localhost:38528/communities | jq

# Ask a question
curl -X POST http://localhost:38528/COMMUNITY_ID/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is this tool?", "api_key": "your-key"}' | jq
```

## CLI Testing (No Server Needed)

```bash
# Interactive chat
uv run osa chat --community COMMUNITY_ID --standalone

# Single question
uv run osa ask --community COMMUNITY_ID "What is this tool?" --standalone
```

## Knowledge Sync

```bash
uv run osa sync init --community COMMUNITY_ID
uv run osa sync github --community COMMUNITY_ID --full
uv run osa sync papers --community COMMUNITY_ID --citations
```

## Test Checklist

- [ ] Config validates without errors
- [ ] Community appears in `/communities`
- [ ] `/ask` endpoint returns relevant answers
- [ ] `/chat` endpoint works for multi-turn
- [ ] Preloaded docs are in context
- [ ] On-demand docs retrieved when relevant
- [ ] Documentation URLs in responses are valid
- [ ] CLI standalone mode works
- [ ] Knowledge sync completes (if configured)
- [ ] Assistant does not hallucinate PR/issue numbers

## Troubleshooting

- **Server won't start**: Check port with `lsof -i :38528`
- **Assistant not found**: Check discovery with `uv run python -c "from src.assistants import discover_assistants, registry; discover_assistants(); print([a.id for a in registry.list_available()])"`
- **Docs not retrieved**: Test source_url with `curl -I <url>`
- **Knowledge empty**: Run `uv run osa sync init --community COMMUNITY_ID` first
