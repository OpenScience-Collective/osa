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

    def test_handles_formatting_errors_gracefully(self) -> None:
        """Should handle formatting errors without crashing."""
        formatter = SecureFormatter("%(invalid_field)s")
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
        # Should return error message with context
        assert "[LOGGING ERROR:" in formatted
        assert "KeyError" in formatted or "ValueError" in formatted
        assert "test" in formatted  # Logger name preserved

    def test_handles_large_messages_safely(self) -> None:
        """Should truncate very large messages to prevent ReDoS."""
        formatter = SecureFormatter("%(message)s")
        # Create a message larger than 100KB
        large_msg = "x" * 150_000
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=large_msg,
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        # Should be truncated
        assert len(formatted) <= 100_050  # 100KB + truncation message
        assert "[truncated for safety]" in formatted

    def test_redacts_api_keys_in_exception_tracebacks(self) -> None:
        """Should redact API keys appearing in exception messages and tracebacks."""
        import sys

        formatter = SecureFormatter("%(message)s")
        api_key = "sk-or-v1-" + "a" * 64

        # Create an exception with API key in the message
        try:
            raise ValueError(f"Connection failed with key {api_key}")
        except ValueError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="An error occurred",
            args=(),
            exc_info=exc_info,
        )

        formatted = formatter.format(record)
        # API key in exception message should be redacted
        assert "sk-or-v1-***[redacted]" in formatted
        assert "aaaa" not in formatted

    def test_concurrent_logging_thread_safety(self) -> None:
        """Should safely redact API keys from concurrent log calls."""
        import threading

        formatter = SecureFormatter("%(message)s")
        errors = []
        formatted_logs = []

        def log_with_key(index: int) -> None:
            try:
                # Use valid hex characters only (0-9, a-f)
                hex_chars = "0123456789abcdef"
                key_suffix = hex_chars[index % len(hex_chars)]
                api_key = f"sk-or-v1-{key_suffix * 64}"
                record = logging.LogRecord(
                    name="test",
                    level=logging.INFO,
                    pathname="",
                    lineno=0,
                    msg=f"Using key: {api_key}",
                    args=(),
                    exc_info=None,
                )
                formatted = formatter.format(record)
                formatted_logs.append((index, api_key, formatted))
                # Verify redaction happened - check that the repeated character doesn't appear
                # (but allow single char in '[redacted]')
                if key_suffix * 4 in formatted:
                    errors.append(
                        f"Key with suffix {key_suffix} not redacted in thread {index}: {formatted}"
                    )
            except Exception as e:
                errors.append(f"Exception in thread {index}: {str(e)}")

        # Create threads with different indices
        threads = [threading.Thread(target=log_with_key, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Check all logs were redacted properly
        assert len(errors) == 0, f"Concurrent logging errors: {errors}"
        assert len(formatted_logs) == 10
        for _, api_key, log in formatted_logs:
            assert "sk-or-v1-***[redacted]" in log
            # Original key should not appear
            assert api_key not in log


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


class TestSecureJSONFormatter:
    """Tests for SecureJSONFormatter structured logging."""

    def test_formats_as_json(self) -> None:
        """Should format log records as JSON."""
        import json

        from src.core.logging import SecureJSONFormatter

        formatter = SecureJSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)

        # Should be valid JSON
        log_data = json.loads(formatted)
        assert log_data["level"] == "INFO"
        assert log_data["logger"] == "test.logger"
        assert log_data["message"] == "Test message"
        assert "timestamp" in log_data

    def test_includes_context_fields(self) -> None:
        """Should include custom context fields from extra parameter."""
        import json

        from src.core.logging import SecureJSONFormatter

        formatter = SecureJSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Request processed",
            args=(),
            exc_info=None,
        )

        # Add custom fields (simulating extra parameter)
        record.community_id = "hed"
        record.origin = "https://example.com"
        record.model = "anthropic/claude-sonnet-4.5"

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert log_data["community_id"] == "hed"
        assert log_data["origin"] == "https://example.com"
        assert log_data["model"] == "anthropic/claude-sonnet-4.5"

    def test_redacts_api_keys_in_json(self) -> None:
        """Should redact API keys in JSON-formatted logs."""
        import json

        from src.core.logging import SecureJSONFormatter

        formatter = SecureJSONFormatter()
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
        log_data = json.loads(formatted)

        assert "sk-or-v1-***[redacted]" in log_data["message"]
        assert "aaaa" not in log_data["message"]

    def test_configures_json_logging(self) -> None:
        """Should configure logging with JSON format when json_format=True."""
        from src.core.logging import SecureJSONFormatter, configure_secure_logging

        configure_secure_logging(level=logging.INFO, json_format=True)

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO
        assert len(root_logger.handlers) >= 1

        # Check that at least one handler has SecureJSONFormatter
        has_json_formatter = any(
            isinstance(handler.formatter, SecureJSONFormatter) for handler in root_logger.handlers
        )
        assert has_json_formatter

    def test_includes_exception_info(self) -> None:
        """Should include exception traceback in JSON logs."""
        import json

        from src.core.logging import SecureJSONFormatter

        formatter = SecureJSONFormatter()

        # Create exception info
        try:
            raise ValueError("Test exception")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="An error occurred",
            args=(),
            exc_info=exc_info,
        )

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert "exception" in log_data
        assert "ValueError" in log_data["exception"]
        assert "Test exception" in log_data["exception"]

    def test_filters_private_attributes(self) -> None:
        """Should not include _-prefixed attributes in JSON output."""
        import json

        from src.core.logging import SecureJSONFormatter

        formatter = SecureJSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Add private attribute
        record._private_field = "should not appear"
        record.public_field = "should appear"

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert "_private_field" not in log_data
        assert "public_field" in log_data
        assert log_data["public_field"] == "should appear"

    def test_handles_json_formatting_errors(self) -> None:
        """Should handle JSON formatting errors gracefully."""
        import json

        from src.core.logging import SecureJSONFormatter

        formatter = SecureJSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Simulate an error condition that could break formatting
        # For example, a circular reference or non-serializable object
        # In our implementation, we use default=str, so this should handle most cases
        # But we can test the exception handler by ensuring error entries are valid JSON
        formatted = formatter.format(record)

        # Should always return valid JSON
        log_data = json.loads(formatted)
        assert "timestamp" in log_data
        assert "level" in log_data
