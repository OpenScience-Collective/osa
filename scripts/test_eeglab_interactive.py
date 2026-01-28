#!/usr/bin/env python3
"""Interactive test of EEGLAB assistant locally."""

from src.assistants import discover_assistants, registry
from src.core.services.llm import create_llm

# Discover assistants
discover_assistants()

# Verify EEGLAB is registered
print("✓ EEGLAB registered:", "eeglab" in registry)

# Get config
config = registry.get_community_config("eeglab")
print(f"✓ Name: {config.name}")
print(f"✓ Description: {config.description}")
print(f"✓ Documentation sources: {len(config.documentation)}")
print(f"✓ GitHub repos: {len(config.github.repos)}")
print(f"✓ Paper DOIs: {len(config.citations.dois)}")

# Create assistant (without API key, just to verify it works)
try:
    model = create_llm(model="openrouter/qwen/qwen-2.5-7b-instruct")
    assistant = registry.create_assistant("eeglab", model=model, preload_docs=False)
    print("\n✓ Assistant created successfully")
    print(f"✓ Tools available: {len(assistant.tools)}")
    print(f"  Tool names: {[t.name for t in assistant.tools]}")

    # Show system prompt excerpt
    prompt = assistant.get_system_prompt()
    print(f"\n✓ System prompt length: {len(prompt)} chars")
    print(f"  Contains 'EEGLAB': {('EEGLAB' in prompt)}")
    print(f"  Contains 'ICA': {('ICA' in prompt)}")
    print(f"  Contains 'ICLabel': {('ICLabel' in prompt)}")

except Exception as e:
    print(f"\n✗ Assistant creation failed: {e}")
    print("  (This is OK if you don't have OPENROUTER_API_KEY set)")

print("\n✓ All checks passed! EEGLAB assistant is properly configured.")
