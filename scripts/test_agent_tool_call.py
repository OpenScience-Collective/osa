#!/usr/bin/env python3
"""Quick test script to verify HED agent tool calling works.

Run with:
    python scripts/test_agent_tool_call.py
"""

import os
import sys

from dotenv import load_dotenv

# Add src to path and load .env file
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
load_dotenv(os.path.join(project_root, ".env"))


def test_agent_tool_call():
    """Test that the HED agent can use tools correctly."""
    # Clear cached settings before importing
    from src.api.config import get_settings

    get_settings.cache_clear()

    import src.core.services.llm as llm_module
    from src.agents.hed import create_hed_assistant
    from src.core.services.llm import get_llm_service

    # Reset the singleton
    llm_module._llm_service = None

    print("=" * 60)
    print("Testing HED Agent Tool Calling")
    print("=" * 60)

    # Check API key
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set")
        return False

    # Get LLM service and show config
    llm_service = get_llm_service()
    print(f"\nModel: {llm_service.default_model}")
    print(f"Provider: {llm_service.settings.default_model_provider}")

    # Create assistant without preloading docs (faster for testing)
    print("\nCreating HED assistant (without doc preload for speed)...")
    assistant = create_hed_assistant(preload_docs=False)

    print(f"Tools available: {[t.name for t in assistant.tools]}")

    # Simple question that should trigger tool use
    question = "What is a valid HED string for annotating a button press event?"

    print(f"\nQuestion: {question}")
    print("-" * 60)

    # Invoke the agent with higher recursion limit
    try:
        result = assistant.invoke(question, config={"recursion_limit": 50})

        # Check results
        messages = result.get("messages", [])
        tool_calls = result.get("tool_calls", [])

        print(f"\nMessages: {len(messages)}")
        print(f"Tool calls made: {len(tool_calls)}")

        if tool_calls:
            print("\nTool calls:")
            for tc in tool_calls:
                print(f"  - {tc['name']}: {tc['args']}")

        # Show final response
        if messages:
            last_msg = messages[-1]
            content = getattr(last_msg, "content", str(last_msg))
            print("\nFinal response (first 500 chars):")
            print(content[:500] if len(content) > 500 else content)

        print("\n" + "=" * 60)
        print("SUCCESS: Agent invoked successfully")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_agent_tool_call()
    sys.exit(0 if success else 1)
