"""A community assistant on an OpenRouter model, through a full tool loop.

The pieces are tested one by one elsewhere (``test_litellm_chat.py`` for the chat model,
``test_tagged_citations.py`` for the layer, ``test_bedrock_routing.py`` for the routing
flags). This runs them together the way ``create_community_assistant`` builds them: the
real HED assistant with its system prompt and tool set, the real LangGraph agent, the
real OpenRouter chat model against a local fake of OpenRouter's wire format, and the
API layer's answer assembly on the result. What it can catch is the seam: the prompt
that asks for tags, tool results reaching the model as tagged text, the state the graph
carries between the two model calls, and the answer and source list a client receives.
"""

import json

import pytest
from langchain_core.tools import tool

from src.api.routers.community import _build_answer_with_citations
from src.assistants import discover_assistants, registry
from src.core.services.litellm_llm import create_openrouter_llm
from src.core.services.tagged_citations import CITATION_INSTRUCTION
from src.tools.citations import build_search_result
from tests.helpers.openrouter import FakeOpenRouter, stream_of, tool_call_stream

discover_assistants()

SCHEMA_URL = "https://hedtags.org/schema"
SENSORY_URL = "https://hedtags.org/sensory"


@tool
def lookup_hed_reference(query: str) -> list[dict]:  # noqa: ARG001 (the tool schema)
    """Look a topic up in the HED reference (returns citable search results)."""
    return [
        build_search_result(SCHEMA_URL, "The HED schema", "HED tags are assembled from a schema."),
        build_search_result(
            SENSORY_URL, "Sensory-event", "The Sensory-event tag marks a sensory stimulus."
        ),
    ]


@pytest.fixture
def openrouter(monkeypatch: pytest.MonkeyPatch):
    with FakeOpenRouter() as server:
        monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
        yield server


def _assistant(model: str):
    llm = create_openrouter_llm(model=model, api_key="sk-or-test")
    return registry.create_assistant(
        "hed",
        model=llm,
        preload_docs=False,
        citations=True,
        tagged_citations=True,
        additional_tools=[lookup_hed_reference],
    )


@pytest.mark.parametrize("model", ["openai/gpt-6-luna", "anthropic/claude-haiku-4.5"])
async def test_a_question_is_answered_with_numbered_citations(
    openrouter: FakeOpenRouter, model: str
) -> None:
    openrouter.reply(
        tool_call_stream("lookup_hed_reference", '{"query": "sensory events"}'),
        stream_of(
            "Sensory-event marks a stimulus.[src:2] ",
            "Tags come from a schema.[sr",
            "c:1]",
        ),
    )

    state = await _assistant(model).ainvoke("What does Sensory-event mark?")

    final = state["messages"][-1]
    answer, citations = _build_answer_with_citations(final.content)

    assert answer == "Sensory-event marks a stimulus.[1] Tags come from a schema.[2]"
    assert [(c.marker, c.source) for c in citations] == [(1, SENSORY_URL), (2, SCHEMA_URL)]
    assert all(c.cited_text for c in citations)
    assert "[src" not in json.dumps(final.content)


async def test_the_model_is_asked_for_tags_and_shown_tagged_sources(
    openrouter: FakeOpenRouter,
) -> None:
    openrouter.reply(
        tool_call_stream("lookup_hed_reference", '{"query": "x"}'), stream_of("Done.[src:1]")
    )

    await _assistant("openai/gpt-6-luna").ainvoke("What is HED?")

    first, second = openrouter.requests
    system = first["messages"][0]["content"]
    system_text = system if isinstance(system, str) else system[0]["text"]
    assert CITATION_INSTRUCTION in system_text
    assert "Source Links Required" not in system_text, "the markdown-link fallback is gone"

    tool_result = second["messages"][-1]
    assert tool_result["role"] == "tool"
    text = _flat(tool_result["content"])
    assert "[src:1] The HED schema" in text and f"Source: {SCHEMA_URL}" in text
    assert "[src:2] Sensory-event" in text
    assert "search_result" not in json.dumps(second)


async def test_a_follow_up_sees_the_earlier_answer_with_its_tags(
    openrouter: FakeOpenRouter,
) -> None:
    """The history the next turn sends is the model's own format, not the citation dicts."""
    openrouter.reply(
        tool_call_stream("lookup_hed_reference", '{"query": "x"}'),
        stream_of("Tags come from a schema.[src:1]"),
        stream_of("Yes, it is versioned."),
    )
    assistant = _assistant("openai/gpt-6-luna")

    first = await assistant.ainvoke("What is HED?")
    await assistant.ainvoke([*first["messages"], *_human("And is the schema versioned?")])

    last_request = openrouter.requests[-1]["messages"]
    earlier = [m for m in last_request if m["role"] == "assistant" and m.get("content")]
    assert any("Tags come from a schema.[src:1]" in _flat(m["content"]) for m in earlier)
    assert '"citations":' not in json.dumps(last_request), "the citation dicts must not be replayed"


def _human(text: str) -> list:
    from langchain_core.messages import HumanMessage

    return [HumanMessage(content=text)]


def _flat(content: str | list) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))
