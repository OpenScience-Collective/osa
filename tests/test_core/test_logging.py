"""Tests for secure logging configuration (Issue #65).

Tests cover:
- API key redaction in log messages
- SecureFormatter functionality
- configure_secure_logging setup
"""

import logging

from src.core.logging import SecureFormatter, configure_secure_logging


class TestSecureFormatter:
    """Tests for SecureFormatter API key redaction."""

    def test_redacts_api_key_in_message(self) -> None:
        """Should redact OpenRouter API keys from log messages."""
        formatter = SecureFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Using API key: sk-or-v1-" + "a" * 64,
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        assert "sk-or-v1-***[redacted]" in formatted
        assert "aaaa" not in formatted  # Original key should not appear

    def test_redacts_multiple_api_keys(self) -> None:
        """Should redact multiple API keys in same message."""
        formatter = SecureFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=f"Key1: sk-or-v1-{'a' * 64}, Key2: sk-or-v1-{'b' * 64}",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        assert formatted.count("sk-or-v1-***[redacted]") == 2
        assert "aaaa" not in formatted
        assert "bbbb" not in formatted

    def test_preserves_non_key_content(self) -> None:
        """Should preserve message content that is not an API key."""
        formatter = SecureFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=f"Starting request with key sk-or-v1-{'c' * 64} for user john",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        assert "Starting request with key" in formatted
        assert "for user john" in formatted
        assert "sk-or-v1-***[redacted]" in formatted

    def test_handles_message_without_keys(self) -> None:
        """Should not modify messages without API keys."""
        formatter = SecureFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Normal log message without any keys",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        assert formatted == "Normal log message without any keys"

    def test_redacts_case_insensitive(self) -> None:
        """Should redact API keys regardless of case."""
        formatter = SecureFormatter("%(message)s")
        # Mix of upper and lower case hex digits
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Key: sk-or-v1-" + "AbCdEf123456" * 5 + "abcd",  # 64 hex chars
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        assert "sk-or-v1-***[redacted]" in formatted

    def test_uses_standard_format_fields(self) -> None:
        """Should support standard logging format fields."""
        formatter = SecureFormatter("%(levelname)s - %(name)s - %(message)s")
        record = logging.LogRecord(
            name="my.logger",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg=f"Key: sk-or-v1-{'d' * 64}",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        assert "WARNING" in formatted
        assert "my.logger" in formatted
        assert "sk-or-v1-***[redacted]" in formatted

    def test_preserves_partial_matches(self) -> None:
        """Should not redact strings that partially match key pattern."""
        formatter = SecureFormatter("%(message)s")
        # These should NOT be redacted (wrong length, wrong prefix, etc.)
        partial_matches = [
            "sk-or-v1-short",  # Too short
            "sk-or-v2-" + "a" * 64,  # Wrong version
            "sk-openrouter-" + "a" * 64,  # Wrong prefix format
        ]

        for msg in partial_matches:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=msg,
                args=(),
                exc_info=None,
            )
            formatted = formatter.format(record)
            assert formatted == msg  # Should not be modified


class TestConfigureSecureLogging:
    """Tests for configure_secure_logging function."""

    def test_configures_root_logger_with_secure_formatter(self) -> None:
        """Should configure root logger with SecureFormatter."""
        configure_secure_logging(level=logging.INFO)

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO
        assert len(root_logger.handlers) >= 1

        # Check that at least one handler has SecureFormatter
        has_secure_formatter = any(
            isinstance(handler.formatter, SecureFormatter) for handler in root_logger.handlers
        )
        assert has_secure_formatter, "Root logger should have at least one SecureFormatter"

    def test_removes_old_handlers(self) -> None:
        """Should remove existing handlers before adding new one."""
        root_logger = logging.getLogger()

        # Store initial handler count
        initial_count = len(root_logger.handlers)

        # Add a handler
        handler = logging.StreamHandler()
        root_logger.addHandler(handler)
        assert len(root_logger.handlers) == initial_count + 1

        # Configure - should remove old handlers and add new one
        configure_secure_logging()

        # Should have at least one handler (the new secure one)
        assert len(root_logger.handlers) >= 1
        # At least one should be a SecureFormatter
        assert any(isinstance(h.formatter, SecureFormatter) for h in root_logger.handlers)

    def test_sets_custom_format_string(self) -> None:
        """Should use custom format string when provided."""

        configure_secure_logging(
            level=logging.INFO,
            format_string="CUSTOM: %(levelname)s - %(message)s",
        )

        # Capture the format by formatting a record
        root_logger = logging.getLogger()
        formatter = root_logger.handlers[0].formatter
        assert isinstance(formatter, SecureFormatter)

        # Create a test record
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        assert "CUSTOM:" in formatted
        assert "INFO" in formatted
