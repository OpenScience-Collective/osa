"""Nothing between ``get_procedure`` and ``propose_plan`` is refused, and the run still parks.

The plan-first gate (``src/agents/plan_first.py``) only decides whether the prompt text is
sent; ``BaseAgent`` refuses no server tool before ``propose_plan``. A procedure is fetched
before the plan, so this proves it through the real app: the real NEMAR config (its MCP
servers emptied, which would reach the network), router, assistant, graph, session store
and SSE assembly. The model is the only stand-in, as in ``test_propose_plan.py``.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk, ToolMessage

import src.api.routers.community as community_module
from src.api.routers.community import _get_session_store, create_community_router
from src.assistants.registry import registry
from src.core.config.community import (
    EXECUTE_CODE_TOOL_NAME,
    GET_PROCEDURE_TOOL_NAME,
    PROPOSE_PLAN_TOOL_NAME,
    CommunityConfig,
)
from src.core.services.anthropic_models import HAIKU, SONNET
from tests.helpers.chat_models import StreamingScriptedChatModel
from tests.helpers.provider_replies import text_chunk
from tests.test_assistants.test_procedures_tool import NEMAR_CONFIG_PATH

COMMUNITY = "proctest"
ORIGIN = "https://procedures.example"
BYOK = {"X-Anthropic-API-Key": "sk-ant-procedures-test-key"}
DECLARED = [EXECUTE_CODE_TOOL_NAME, "get_full_output", PROPOSE_PLAN_TOOL_NAME]
ANALYSIS_REQUEST = "plot the power spectrum of the first recording"
PLAN_ARGS = {
    "findings": "One recording, 30 EEG channels.",
    "steps": [
        {"title": "Compute the thing", "detail": "One run that computes it."},
        {"title": "Plot the thing", "detail": "One run that plots it."},
    ],
    "est_peak_mb": 400,
}


def _config() -> CommunityConfig:
    loaded = CommunityConfig.from_yaml(NEMAR_CONFIG_PATH)
    assert loaded.extensions is not None
    return loaded.model_copy(
        update={
            "id": COMMUNITY,
            "cors_origins": [ORIGIN],
            "extensions": loaded.extensions.model_copy(update={"mcp_servers": []}),
        }
    )


def _call(name: str, args: dict, call_id: str) -> list[AIMessageChunk]:
    return [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": name, "args": json.dumps(args), "id": call_id, "index": 0}],
        )
    ]


@pytest.fixture(autouse=True)
def _community():
    registry.register_from_config(_config())
    _get_session_store(COMMUNITY).clear()
    yield
    registry._assistants.pop(COMMUNITY, None)
    _get_session_store(COMMUNITY).clear()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_community_router(COMMUNITY))
    return TestClient(app)


def _rig(monkeypatch, script) -> tuple[StreamingScriptedChatModel, list]:
    analysis = StreamingScriptedChatModel(chunk_script=script)
    quick = StreamingScriptedChatModel(chunk_script=[[text_chunk("unused")]])
    by_model = {HAIKU: quick, SONNET: analysis}
    monkeypatch.setattr(
        community_module, "create_anthropic_llm", lambda *, model, **_kw: by_model[model]
    )
    assistants: list = []
    real_create = community_module.create_community_assistant

    def spy(*args, **kwargs):
        built = real_create(*args, **kwargs)
        assistants.append(built)
        return built

    monkeypatch.setattr(community_module, "create_community_assistant", spy)
    return analysis, assistants


def _chat(client: TestClient) -> list[dict]:
    response = client.post(
        f"/{COMMUNITY}/chat",
        json={"message": ANALYSIS_REQUEST, "client_tools": DECLARED},
        headers={"Origin": ORIGIN, **BYOK},
    )
    assert response.status_code == 200, response.text
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_an_analysis_turn_fetches_the_procedure_then_proposes_the_plan(
    client, monkeypatch
) -> None:
    config = _config()
    procedure_id = config.procedures[0].id
    document = config.procedure_documents()[procedure_id]
    analysis, assistants = _rig(
        monkeypatch,
        [
            _call(GET_PROCEDURE_TOOL_NAME, {"procedure_id": procedure_id}, "toolu_01proc"),
            _call(PROPOSE_PLAN_TOOL_NAME, PLAN_ARGS, "toolu_01plan"),
        ],
    )

    events = _chat(client)

    names = [(e["event"], e.get("name") or e.get("tool")) for e in events]
    started = names.index(("tool_start", GET_PROCEDURE_TOOL_NAME))
    ended = names.index(("tool_end", GET_PROCEDURE_TOOL_NAME))
    requested = names.index(("tool_request", PROPOSE_PLAN_TOOL_NAME))
    assert started < ended < requested
    assert document.splitlines()[0] in next(
        e for e in events if e["event"] == "tool_end" and e["name"] == GET_PROCEDURE_TOOL_NAME
    )["output"]

    request = events[requested]
    assert request["args"] == PLAN_ARGS
    assert request["requires_permission"] is False
    assert not any(e["event"] in ("done", "error") for e in events)

    # The model's second call saw the document, so the fetch reached it before the plan.
    assert analysis.calls == 2
    fetched = [m for m in analysis.seen_message_lists[1] if isinstance(m, ToolMessage)]
    assert [m.content for m in fetched] == [document]

    # The run parks on the plan, and neither call ran code or spent a browser run.
    session = _get_session_store(COMMUNITY)[request["session_id"]]
    assert session.pending_call is not None
    assert session.pending_call.tool == PROPOSE_PLAN_TOOL_NAME
    assert session.pending_call.runs_code is False

    # Both tools were bound for the turn, and its prompt carried the list, not the text.
    assert GET_PROCEDURE_TOOL_NAME in analysis.bound_tool_names
    assert PROPOSE_PLAN_TOOL_NAME in analysis.bound_tool_names
    prompt = assistants[0].assistant.system_prompt
    assert "## Known procedures" in prompt
    assert document not in prompt


def test_the_fetch_of_an_unknown_id_is_answered_and_the_turn_goes_on(client, monkeypatch) -> None:
    config = _config()
    analysis, _ = _rig(
        monkeypatch,
        [
            _call(GET_PROCEDURE_TOOL_NAME, {"procedure_id": "no-such-id"}, "toolu_01bad"),
            _call(PROPOSE_PLAN_TOOL_NAME, PLAN_ARGS, "toolu_01plan"),
        ],
    )

    events = _chat(client)

    answer = next(m for m in analysis.seen_message_lists[1] if isinstance(m, ToolMessage))
    for procedure in config.procedures:
        assert procedure.id in answer.content
    assert any(e["event"] == "tool_request" and e["tool"] == PROPOSE_PLAN_TOOL_NAME for e in events)
