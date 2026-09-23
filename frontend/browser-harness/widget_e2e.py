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

    uv run python frontend/browser-harness/widget_e2e.py [port] [--nemar] [--tamper-wheel] [--widget-open]

then open http://127.0.0.1:PORT/browser-harness/widget-e2e.html and ask it to
"plot", to "loop" (then press Stop), or to raise an "error".

--nemar serves NEMAR's own config instead: its runtime, its lock overlay and the
wheels its route serves, reading the live, public zarr.nemar.org. Ask it to "read"
(read_window and a plot) or for the "recipe" (the python_browser snippet). Its MCP
servers are dropped, since the scripted model never calls them. --tamper-wheel serves
every wheel one byte longer and still valid, the byte as the zip's comment: the
negative control for the browser's integrity check, which alone can refuse it, so
the runtime must then refuse to start. --widget-open boots the runtime when
the chat opens rather than at the first run, and turns on the widget's test hooks so
`OSAChatWidget.__browser.getBrowserRuntime().state` can be read from the console.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI, Request, Response
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

CDN = "https://cdn.jsdelivr.net"
NEMAR_CONFIG = ROOT / "src" / "assistants" / "nemar" / "config.yaml"

# nemar.org's production policy as the browser-harness README records it, with
# 'self' standing in for the API host because this harness serves both, and the
# one data host NEMAR's runtime reads, which nemar.org grants as *.nemar.org.
CSP = (
    f"script-src 'self' 'wasm-unsafe-eval' {CDN}; "
    f"connect-src 'self' {CDN} https://zarr.nemar.org; "
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

# NEMAR's lane, against the live public archive: what a reader asking to see a
# recording gets, and the recipe nemar_read_window hands the model.
READ = """import eegprep_lean

index = await eegprep_lean.read_index("nm000103")
store = index.stores[0]
window = await eegprep_lean.read_window(index, store, start_sample=2500, n_samples=500, channels=[0, 1, 2, 3])
print(f"{window.data.shape} in {window.unit} at {window.rate} Hz: {', '.join(window.labels)}")
display(eegprep_lean.plot_window(window).figure)
"""

RECIPE = """from eegprep_lean import open_array, read_index

index = await read_index("nm000103")
url = index.level0_url(index.stores[0])
start_sample, end_sample = 0, 500
arr = await open_array(url)
window = await arr.getitem((slice(None), slice(start_sample, end_sample)))
print(url)
print(window.shape, window.dtype, int(window.min()), int(window.max()))
"""

SCRIPTS = {
    "plot": (PLOT, "Plot a 10 Hz sine with a 25 Hz component"),
    "loop": ("while True:\n    pass\n", "Spin forever, to test Stop"),
    "error": ("values = [1, 2, 3]\nvalues[10]\n", "Index past the end of a list"),
    "read": (READ, "Read two seconds of nm000103 and plot four channels"),
    "recipe": (RECIPE, "Run the python_browser recipe on nm000103"),
}


def _tamper(wheel: bytes) -> bytes:
    """The wheel one byte longer and still valid: the byte becomes the zip's comment.

    Appending a bare byte breaks the zip, and a boot then fails whether or not the
    digest is checked. The same trick as tamperWheel in serve.js.
    """
    end = wheel[-22:]
    if end[:4] != b"PK\x05\x06" or end[20:22] != b"\x00\x00":
        raise ValueError("expected a wheel whose zip has no comment")
    return wheel[:-2] + b"\x01\x00 "


def _nemar_config() -> CommunityConfig:
    """NEMAR's shipped config, without the MCP servers the scripted model never calls."""
    config = CommunityConfig.from_yaml(NEMAR_CONFIG)
    assert config.extensions is not None
    extensions = config.extensions.model_copy(update={"mcp_servers": []})
    return config.model_copy(update={"extensions": extensions})


def _config() -> CommunityConfig:
    return CommunityConfig(
        id="browsertest",
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
                "pyodide_version": "0.29.5",
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


def _booting_on_open(config: CommunityConfig) -> CommunityConfig:
    assert config.runtime is not None and config.runtime.python is not None
    python = config.runtime.python.model_copy(update={"preload_on": "widget_open"})
    return config.model_copy(
        update={"runtime": config.runtime.model_copy(update={"python": python})}
    )


def build_app(
    config: CommunityConfig, *, tamper_wheel: bool = False, test_hooks: bool = False
) -> FastAPI:
    registry.register_from_config(config)

    def assistant(
        *_args: Any, declared_client_tools: set[str] | None = None, **_kwargs: Any
    ) -> AssistantWithMetrics:
        built = CommunityAssistant(
            model=BrowserTurnModel(),
            config=config,
            preload_docs=False,
            declared_client_tools=declared_client_tools,
        )
        return AssistantWithMetrics(assistant=built, model="scripted", key_source="platform")

    app = FastAPI()
    app.state.assistant = assistant
    app.include_router(create_community_router(config.id), prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "healthy", "version": "widget-e2e"}

    # Which community the page talks to; a file because the policy forbids inline
    # script, and served here because it depends on how the harness was started.
    @app.get("/browser-harness/widget-e2e-config.js")
    def widget_config() -> Response:
        body = (
            "window.__OSA_CHAT_CONFIG__ = "
            + json.dumps(
                {
                    "communityId": config.id,
                    "storageKey": f"osa-widget-e2e-{config.id}",
                    "pageContextDefaultEnabled": False,
                }
            )
            + ";\nwindow.__OSA_CHAT_CONFIG__.apiEndpoint = `${window.location.origin}/api`;\n"
            + ("window.__OSA_TEST__ = true;\n" if test_hooks else "")
        )
        return Response(body, media_type="application/javascript")

    @app.middleware("http")
    async def policy(request: Request, call_next):
        response = await call_next(request)
        if tamper_wheel and "/runtime/" in request.url.path and request.url.path.endswith(".whl"):
            # A valid wheel that is not the one the entry's sha256 describes, so
            # only the browser's integrity check can refuse it: see _tamper.
            body = _tamper(b"".join([chunk async for chunk in response.body_iterator]))
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            response = Response(body, status_code=response.status_code, headers=headers)
        if not request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = CSP
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/", StaticFiles(directory=FRONTEND), name="frontend")
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The chat widget running code, end to end.")
    parser.add_argument("port", nargs="?", type=int, default=8788)
    parser.add_argument("--nemar", action="store_true", help="serve NEMAR's own config")
    parser.add_argument("--tamper-wheel", action="store_true", help="corrupt every wheel served")
    parser.add_argument("--widget-open", action="store_true", help="boot when the chat opens")
    args = parser.parse_args()
    config = _nemar_config() if args.nemar else _config()
    if args.widget_open:
        config = _booting_on_open(config)
    app = build_app(config, tamper_wheel=args.tamper_wheel, test_hooks=args.widget_open)
    with patch("src.api.routers.community.create_community_assistant", app.state.assistant):
        print(f"open http://127.0.0.1:{args.port}/browser-harness/widget-e2e.html")
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
