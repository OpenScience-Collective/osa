"""Tests for API page context models and validation."""

import pytest
from pydantic import ValidationError

from src.api.routers.community import AskRequest, ChatRequest, PageContext


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
