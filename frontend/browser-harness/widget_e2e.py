#!/usr/bin/env python3
"""The chat widget running code in the browser, end to end, under a real CSP.

What is real: the widget, the runtime bundle it loads with SRI, Pyodide from the
CDN, the permission gate, the FastAPI community router (config, /chat and
/chat/resume), the LangGraph graph, the session store and the SSE assembly.

What stands in: the language model, which is the one piece whose behavior is not
under test and which needs a key this harness does not have. It is a scripted
model that picks code by keyword and, once the browser has answered, replies with
the result it was sent, so the page shows the round trip actually happened.

Usage, from the repository root:

    uv run python frontend/browser-harness/widget_e2e.py [port]

then open http://127.0.0.1:PORT/browser-harness/widget-e2e.html and ask it to
"plot", to "loop" (then press Stop), or to raise an "error".
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

FRONTEND = Path(__file__).resolve().parent.parent
ROOT = FRONTEND.parent
sys.path.insert(0, str(ROOT))

from src.api.routers.community import AssistantWithMetrics, create_community_router  # noqa: E402
from src.assistants.community import CommunityAssistant  # noqa: E402
from src.assistants.registry import registry  # noqa: E402
from src.core.config.community import CommunityConfig  # noqa: E402

COMMUNITY = "browsertest"
CDN = "https://cdn.jsdelivr.net"

# nemar.org's production policy as the browser-harness README records it, with
# 'self' standing in for the API host because this harness serves both.
CSP = (
    f"script-src 'self' 'wasm-unsafe-eval' {CDN}; "
    f"connect-src 'self' {CDN}; "
    "default-src 'self'; worker-src 'self' blob:; child-src 'self' blob:; "
    "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; form-action 'self'"
)

PLOT = """import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 1, 500)
signal = np.sin(2 * np.pi * 10 * t) + 0.3 * np.sin(2 * np.pi * 25 * t)
print(f"samples: {signal.size}, peak: {signal.max():.3f}")
plt.plot(t, signal)
plt.title("10 Hz and 25 Hz")
"""

SCRIPTS = {
    "plot": (PLOT, "Plot a 10 Hz sine with a 25 Hz component"),
    "loop": ("while True:\n    pass\n", "Spin forever, to test Stop"),
    "error": ("values = [1, 2, 3]\nvalues[10]\n", "Index past the end of a list"),
}


def _config() -> CommunityConfig:
    return CommunityConfig(
        id=COMMUNITY,
        name="Browser Test",
        description="Runs Python in the reader's browser",
        extensions={
            "client_tools": [
                {
                    "name": "execute_code",
                    "runtime": "python",
                    "requires_permission": True,
                    "description": "Run Python in the reader's browser.",
                }
            ]
        },
        runtime={
            "python": {
                "pyodide_version": "0.28.3",
                "lockfile": "runtime/browsertest-pyodide-lock.json",
                "preload": ["numpy", "matplotlib"],
                "preload_on": "first_run",
                "limits": {"exec_seconds": 20},
            }
        },
    )


def _text_of(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


class BrowserTurnModel(BaseChatModel):
    """Stands in for the LLM: asks for code by keyword, then reports what came back."""

    @property
    def _llm_type(self) -> str:
        return "browser-turn-script"

    def bind_tools(self, _tools: Any, **_kwargs: Any) -> BaseChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        # Unused, and kept: BaseChatModel passes both by keyword.
        stop: Any = None,  # noqa: ARG002
        run_manager: Any = None,  # noqa: ARG002
        **_kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._respond(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Any = None,  # noqa: ARG002
        run_manager: Any = None,
        **_kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Stream as a provider does: text a word at a time, a tool call whole.

        The router builds the reply text from streamed chunks, so a model that only
        returns whole messages produces an empty `done.content`, which is not what a
        reader ever sees from a real one.
        """
        message = self._respond(messages)
        if message.tool_calls:
            chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                    for i, c in enumerate(message.tool_calls)
                ],
            )
            yield ChatGenerationChunk(message=chunk)
            return
        for word in re.findall(r"\S+\s*", str(message.content)):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=word))
            if run_manager is not None:
                run_manager.on_llm_new_token(word, chunk=chunk)
            yield chunk

    def _respond(self, messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            images = (
                [b for b in last.content if isinstance(b, dict) and b.get("type") == "image"]
                if isinstance(last.content, list)
                else []
            )
            reply = (
                f"The browser answered call `{last.tool_call_id}` with {len(images)} image(s). "
                f"What came back:\n\n```\n{_text_of(last)[:1200]}\n```"
            )
            return AIMessage(content=reply)
        asked = _text_of(last).lower() if isinstance(last, HumanMessage) else ""
        for keyword, (code, description) in SCRIPTS.items():
            if keyword in asked:
                call = {
                    "name": "execute_code",
                    "args": {"code": code, "description": description},
                    "id": f"toolu_{uuid.uuid4().hex[:24]}",
                    "type": "tool_call",
                }
                return AIMessage(content="", tool_calls=[call])
        return AIMessage(content="Ask me to plot, loop or raise an error.")


def _assistant(
    *_args: Any, declared_client_tools: set[str] | None = None, **_kwargs: Any
) -> AssistantWithMetrics:
    assistant = CommunityAssistant(
        model=BrowserTurnModel(),
        config=_config(),
        preload_docs=False,
        declared_client_tools=declared_client_tools,
    )
    return AssistantWithMetrics(assistant=assistant, model="scripted", key_source="platform")


def build_app() -> FastAPI:
    registry.register_from_config(_config())
    app = FastAPI()
    app.include_router(create_community_router(COMMUNITY), prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "healthy", "version": "widget-e2e"}

    @app.middleware("http")
    async def policy(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = CSP
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/", StaticFiles(directory=FRONTEND), name="frontend")
    return app


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8788
    with patch("src.api.routers.community.create_community_assistant", _assistant):
        print(f"open http://127.0.0.1:{port}/browser-harness/widget-e2e.html")
        uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
