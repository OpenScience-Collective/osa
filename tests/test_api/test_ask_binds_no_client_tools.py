"""Pins that `/ask` never binds a client-executed tool to the model.

`AskRequest` (src/api/routers/community.py) has no `client_tools` field, and
neither branch of the `ask()` handler passes `declared_client_tools` to the
router's `create_community_assistant`, so it defaults to `None`. That default
flows straight into `CommunityAssistant.__init__`'s own
`declared_client_tools: set[str] | None = None` (src/assistants/community.py),
which calls `build_client_tools(config, declared_client_tools)`
(src/tools/client_tools.py) and gets back `[]`.

Nothing pinned any of this before. `tests/test_tools/test_client_tools.py`
tests `build_client_tools` directly and
`tests/test_agents/test_client_tools_graph.py` tests `BaseAgent` routing
directly, but neither goes through the actual `/ask` request shape or the
assistant it builds. A copy-paste of `ChatRequest.client_tools` onto
`AskRequest`, or of a `declared_client_tools=set(body.client_tools)` call
site onto one of the `ask()` branches, would silently defeat "ask binds no
client tools" and no existing test would notice.

One correction to the plan this test file was commissioned from: it describes
`AskRequest` as `extra="forbid"`. Checked directly, `AskRequest.model_config`
is `{}` -- pydantic's default, `extra="ignore"` -- not `extra="forbid"`,
unlike `ResumeRequest` a few lines above it in the same module, which does set
it. So a `client_tools` value on an ask request is not rejected; it is
silently dropped. The tests below assert that real behavior rather than the
mechanism the plan assumed.
"""

import inspect

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from src.api.routers.community import AskRequest, ChatRequest, create_community_assistant
from src.assistants.community import CommunityAssistant
from src.core.config.community import CommunityConfig
from src.tools.client_tools import ClientTool
from tests.helpers.chat_models import ScriptedChatModel

COMMUNITY = "asktest"


@tool
def lookup_docs(query: str) -> str:
    """Look something up. A real, server-executed tool, so the assistant's
    tool list is not empty for reasons unrelated to client tools."""
    return f"documentation for {query}"


def _config_with_a_client_tool() -> CommunityConfig:
    return CommunityConfig(
        id=COMMUNITY,
        name="Ask Test",
        description="A community configured with a client tool.",
        extensions={
            "client_tools": [
                {
                    "name": "execute_code",
                    "runtime": "python",
                    "requires_permission": True,
                    "description": "Run Python in the user's browser.",
                }
            ]
        },
        runtime={
            "python": {
                "pyodide_version": "0.28.3",
                "lockfile": "runtime/asktest-pyodide-lock.json",
            }
        },
    )


class TestAskRequestCannotDeclareAClientTool:
    """`AskRequest` carries no field a caller could use to declare one."""

    def test_the_field_is_not_declared(self) -> None:
        assert "client_tools" not in AskRequest.model_fields

    def test_chat_request_declares_it_for_contrast(self) -> None:
        """Not a naming miss: the sibling request model does have the field,
        so AskRequest's omission is deliberate rather than an oversight this
        test cannot tell apart from a typo."""
        assert "client_tools" in ChatRequest.model_fields

    def test_a_client_tools_value_is_dropped_not_stored(self) -> None:
        """Real behavior, not source text: constructing the request with the
        field neither raises (it is not extra="forbid") nor is retained
        anywhere accessible."""
        request = AskRequest(question="hi", client_tools=["execute_code"])

        assert not hasattr(request, "client_tools")
        assert "client_tools" not in request.model_dump()


class TestAskPathBindsNoClientTool:
    """Behavioral pins, built the way `ask()` actually builds an assistant."""

    def test_create_community_assistant_defaults_declared_client_tools_to_none(self) -> None:
        """Both `ask()` branches call the router's `create_community_assistant`
        with no `declared_client_tools` argument at all; pinning the default
        is what keeps that true if a call site is ever touched without
        touching this test."""
        parameter = inspect.signature(create_community_assistant).parameters[
            "declared_client_tools"
        ]
        assert parameter.default is None

    def test_a_community_assistant_built_the_way_ask_builds_one_binds_no_client_tool(
        self,
    ) -> None:
        """Config declares a client tool; the caller (ask) declares nothing.
        The resulting assistant must not expose that tool to the model at
        all -- not merely fail to answer a tool_request for it.

        `ScriptedChatModel` (tests/helpers/chat_models.py) records exactly
        what was bound to it, so this checks the real tool surface the model
        would see, not just `assistant.tools` or `client_tool_names`.
        """
        model = ScriptedChatModel(responses=[AIMessage(content="ok")])

        assistant = CommunityAssistant(
            model=model,
            config=_config_with_a_client_tool(),
            preload_docs=False,
            additional_tools=[lookup_docs],
            # declared_client_tools omitted entirely: this is the default
            # ask() relies on, not a value chosen for this test.
        )

        assert assistant.client_tool_names == set()
        assert not any(isinstance(t, ClientTool) for t in assistant.tools)
        assert model.bound_tool_names == ["lookup_docs"]
