"""Tests for API page context models and validation."""

import json
import subprocess
from pathlib import Path
from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.routers.community import (
    AskRequest,
    ChatRequest,
    PageContext,
    ResumeRequest,
    create_community_assistant,
    create_community_router,
)
from src.api.security import ByokCredential
from src.assistants import discover_assistants, registry
from tests.helpers.deployment import without_mcp_servers

discover_assistants()

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestPageContextModel:
    """Tests for PageContext Pydantic model."""

    def test_valid_https_url(self):
        """Should accept valid HTTPS URL."""
        ctx = PageContext(url="https://example.com", title="Test")
        assert ctx.url == "https://example.com"
        assert ctx.title == "Test"

    def test_valid_http_url(self):
        """Should accept valid HTTP URL."""
        ctx = PageContext(url="http://example.com", title="Test")
        assert ctx.url == "http://example.com"

    def test_none_url(self):
        """Should accept None URL."""
        ctx = PageContext(url=None, title="Test")
        assert ctx.url is None

    def test_invalid_scheme_raises_error(self):
        """Should raise error for invalid URL schemes."""
        with pytest.raises(ValidationError) as exc_info:
            PageContext(url="ftp://example.com", title="Test")
        assert "URL must start with http://" in str(exc_info.value)

    def test_file_scheme_raises_error(self):
        """Should raise error for file:// URLs."""
        with pytest.raises(ValidationError) as exc_info:
            PageContext(url="file:///etc/passwd", title="Test")
        assert "URL must start with http://" in str(exc_info.value)

    def test_javascript_scheme_raises_error(self):
        """Should raise error for javascript: URLs."""
        with pytest.raises(ValidationError) as exc_info:
            PageContext(url="javascript:alert(1)", title="Test")
        assert "URL must start with http://" in str(exc_info.value)

    def test_title_max_length(self):
        """Should enforce title max length."""
        long_title = "x" * 501
        with pytest.raises(ValidationError):
            PageContext(url="https://example.com", title=long_title)

    def test_url_max_length(self):
        """Should enforce URL max length."""
        long_url = "https://example.com/" + "x" * 2048
        with pytest.raises(ValidationError):
            PageContext(url=long_url, title="Test")

    def test_empty_values(self):
        """Should accept empty PageContext."""
        ctx = PageContext()
        assert ctx.url is None
        assert ctx.title is None

    def test_widget_instructions_valid(self):
        """Should accept valid widget_instructions."""
        ctx = PageContext(widget_instructions="Focus on online tools.")
        assert ctx.widget_instructions == "Focus on online tools."

    def test_widget_instructions_none(self):
        """Should accept None widget_instructions."""
        ctx = PageContext(widget_instructions=None)
        assert ctx.widget_instructions is None

    def test_widget_instructions_max_length(self):
        """Should enforce widget_instructions max length."""
        long_instructions = "x" * 2001
        with pytest.raises(ValidationError):
            PageContext(widget_instructions=long_instructions)

    def test_widget_instructions_at_max_length(self):
        """Should accept widget_instructions at exactly max length."""
        instructions = "x" * 2000
        ctx = PageContext(widget_instructions=instructions)
        assert len(ctx.widget_instructions) == 2000


class TestAskRequestWithPageContext:
    """Tests for AskRequest with page context."""

    def test_request_without_page_context(self):
        """Should work without page context."""
        req = AskRequest(question="What is HED?")
        assert req.question == "What is HED?"
        assert req.page_context is None

    def test_request_with_page_context(self):
        """Should accept page context."""
        req = AskRequest(
            question="What is HED?",
            page_context=PageContext(url="https://hedtags.org", title="HED Tags"),
        )
        assert req.question == "What is HED?"
        assert req.page_context is not None
        assert req.page_context.url == "https://hedtags.org"

    def test_request_with_nested_dict_page_context(self):
        """Should parse nested dict as PageContext."""
        req = AskRequest(
            question="What is HED?",
            page_context={"url": "https://hedtags.org", "title": "HED Tags"},
        )
        assert req.page_context is not None
        assert req.page_context.url == "https://hedtags.org"
        assert req.page_context.title == "HED Tags"

    def test_request_with_invalid_url_in_page_context(self):
        """Should raise error for invalid URL in page context."""
        with pytest.raises(ValidationError) as exc_info:
            AskRequest(
                question="What is HED?",
                page_context={"url": "ftp://invalid", "title": "Test"},
            )
        assert "URL must start with http://" in str(exc_info.value)


class TestPageContextReaderFields:
    """dataset_id and moved_from (#656): validated where the request enters."""

    def test_accepts_dataset_id_and_moved_from(self):
        ctx = PageContext(dataset_id="nm000241", moved_from={"type": "dataset", "id": "on002718"})
        assert ctx.dataset_id == "nm000241"
        assert ctx.moved_from is not None
        assert ctx.moved_from.type == "dataset"
        assert ctx.moved_from.id == "on002718"

    def test_moved_from_search_needs_no_id(self):
        ctx = PageContext(moved_from={"type": "search"})
        assert ctx.moved_from is not None
        assert ctx.moved_from.id is None

    def test_both_are_optional(self):
        ctx = PageContext(url="https://example.com")
        assert ctx.dataset_id is None
        assert ctx.moved_from is None

    @pytest.mark.parametrize("bad", ["", "a b", "a/b", "x" * 65, "id;drop", "id\n", "\u00e9"])
    def test_rejects_dataset_id_outside_the_pattern(self, bad):
        with pytest.raises(ValidationError):
            PageContext(dataset_id=bad)

    @pytest.mark.parametrize("bad", ["", "a b", "a/b", "x" * 65, "id\n"])
    def test_rejects_moved_from_id_outside_the_pattern(self, bad):
        with pytest.raises(ValidationError):
            PageContext(moved_from={"type": "dataset", "id": bad})

    @pytest.mark.parametrize("bad", ["notebook", "", "Dataset", None])
    def test_rejects_unknown_moved_from_type(self, bad):
        with pytest.raises(ValidationError):
            PageContext(moved_from={"type": bad, "id": "nm000241"})

    def test_rejects_moved_from_dataset_without_id(self):
        with pytest.raises(ValidationError):
            PageContext(moved_from={"type": "dataset"})

    @pytest.mark.parametrize("an_id", ["nm000241", ""])
    def test_rejects_an_id_on_a_search_move(self, an_id):
        with pytest.raises(ValidationError):
            PageContext(moved_from={"type": "search", "id": an_id})

    def test_rejects_unknown_moved_from_field(self):
        with pytest.raises(ValidationError):
            PageContext(moved_from={"type": "search", "extra": "x"})

    def test_chat_request_carries_both(self):
        req = ChatRequest(
            message="Compare them",
            page_context={
                "dataset_id": "nm000241",
                "moved_from": {"type": "dataset", "id": "on002718"},
            },
        )
        assert req.page_context is not None
        assert req.page_context.dataset_id == "nm000241"
        assert req.page_context.moved_from.id == "on002718"


class TestMemoryBudgetField:
    """memory_budget_mb (E5.1, #668): four sizes, validated where the request enters."""

    @pytest.mark.parametrize("budget", [256, 512, 1024, 2048])
    def test_accepts_each_offered_size(self, budget):
        assert PageContext(memory_budget_mb=budget).memory_budget_mb == budget

    def test_is_optional(self):
        assert PageContext(url="https://example.com").memory_budget_mb is None

    @pytest.mark.parametrize("bad", [0, 128, 300, 511, 4096, -512, 512.5, "512 MB", "big", True])
    def test_rejects_any_other_value(self, bad):
        with pytest.raises(ValidationError):
            PageContext(memory_budget_mb=bad)

    def test_chat_and_resume_requests_carry_it(self):
        chat = ChatRequest(message="Plot it", page_context={"memory_budget_mb": 1024})
        assert chat.page_context is not None
        assert chat.page_context.memory_budget_mb == 1024
        resume = ResumeRequest(
            session_id="s1",
            result={"call_id": "c1", "status": "ok"},
            page_context={"memory_budget_mb": 256},
        )
        assert resume.page_context is not None
        assert resume.page_context.memory_budget_mb == 256

    def test_the_offered_sizes_are_the_widgets(self):
        """frontend/osa-memory.js holds the widget's list; the server's must not drift from it."""
        annotation = PageContext.model_fields["memory_budget_mb"].annotation
        server_sizes = {size for arm in get_args(annotation) for size in get_args(arm)}
        script = (
            "import { MEMORY_BUDGETS_MB } from './frontend/osa-memory.js';"
            "console.log(JSON.stringify(MEMORY_BUDGETS_MB));"
        )
        result = subprocess.run(
            ["bun", "-e", script], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
        assert set(json.loads(result.stdout)) == server_sizes


class TestMemoryBudgetReachesTheNemarPrompt:
    """From the model the request is parsed into, through the real assistant factory."""

    @pytest.fixture(autouse=True)
    def _offline(self, monkeypatch):
        without_mcp_servers(monkeypatch, registry.get("nemar"))

    @pytest.mark.parametrize("budget", [256, 512, 1024, 2048])
    def test_the_prompt_states_the_budget(self, budget):
        request = ChatRequest(message="Plot it", page_context={"memory_budget_mb": budget})
        awm = create_community_assistant(
            "nemar",
            byok=ByokCredential(key="sk-ant-test", provider="anthropic"),
            preload_docs=False,
            page_context=request.page_context,
        )
        prompt = awm.assistant.get_system_prompt()
        assert f"Each step must fit in {budget} MB of the reader's browser memory" in prompt

    def test_no_budget_states_none(self):
        awm = create_community_assistant(
            "nemar",
            byok=ByokCredential(key="sk-ant-test", provider="anthropic"),
            preload_docs=False,
            page_context=PageContext(dataset_id="nm000241"),
        )
        assert "Browser memory budget" not in awm.assistant.get_system_prompt()


class TestMemoryBudgetOverHttp:
    """A size outside the four is a 422 on both endpoints that take a page context."""

    @pytest.fixture
    def client(self):
        app = FastAPI()
        app.include_router(create_community_router("nemar"))
        return TestClient(app)

    @pytest.mark.parametrize("bad", [300, 4096, "512", 0])
    def test_chat_refuses_it(self, client, bad):
        response = client.post(
            "/nemar/chat", json={"message": "Plot it", "page_context": {"memory_budget_mb": bad}}
        )
        assert response.status_code == 422
        assert "memory_budget_mb" in response.text

    @pytest.mark.parametrize("bad", [300, 4096, "512", 0])
    def test_resume_refuses_it(self, client, bad):
        response = client.post(
            "/nemar/chat/resume",
            json={
                "session_id": "s1",
                "result": {"call_id": "c1", "status": "ok"},
                "page_context": {"memory_budget_mb": bad},
            },
        )
        assert response.status_code == 422
        assert "memory_budget_mb" in response.text


class TestChatRequestWithPageContext:
    """Tests for ChatRequest with page context."""

    def test_request_without_page_context(self):
        """Should work without page context."""
        req = ChatRequest(message="What is HED?")
        assert req.message == "What is HED?"
        assert req.page_context is None

    def test_request_with_page_context(self):
        """Should accept page context."""
        req = ChatRequest(
            message="What is HED?",
            page_context=PageContext(url="https://hedtags.org", title="HED Tags"),
        )
        assert req.message == "What is HED?"
        assert req.page_context is not None
        assert req.page_context.url == "https://hedtags.org"

    def test_request_with_nested_dict_page_context(self):
        """Should parse nested dict as PageContext."""
        req = ChatRequest(
            message="What is HED?",
            page_context={"url": "https://hedtags.org", "title": "HED Tags"},
        )
        assert req.page_context is not None
        assert req.page_context.url == "https://hedtags.org"
        assert req.page_context.title == "HED Tags"

    def test_request_with_invalid_url_in_page_context(self):
        """Should raise error for invalid URL in page context."""
        with pytest.raises(ValidationError) as exc_info:
            ChatRequest(
                message="What is HED?",
                page_context={"url": "ftp://invalid", "title": "Test"},
            )
        assert "URL must start with http://" in str(exc_info.value)
