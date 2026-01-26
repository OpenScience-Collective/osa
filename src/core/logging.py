"""Secure logging configuration with API key redaction.

Provides a custom log formatter that automatically redacts OpenRouter API keys
from log messages to prevent credential exposure in centralized logging systems.

Supports both text and JSON-structured logging formats.
"""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any


class SecureFormatter(logging.Formatter):
    """Custom log formatter that redacts API keys from log messages.

    Automatically detects and redacts OpenRouter API keys in the format
    sk-or-v1-[64 hex chars] to prevent accidental credential exposure.
    """

    # Pattern to match OpenRouter API keys: sk-or-v1-[64 hex chars]
    API_KEY_PATTERN = re.compile(r"sk-or-v1-[0-9a-f]{64}", re.IGNORECASE)

    def format(self, record: logging.LogRecord) -> str:
        """Format log record and redact any API keys.

        Args:
            record: The log record to format.

        Returns:
            Formatted log message with API keys redacted.
        """
        # Format the message first
        try:
            formatted = super().format(record)
        except (ValueError, TypeError, KeyError) as e:
            # Expected formatting errors - include context for debugging
            safe_msg = str(getattr(record, "msg", "<no message>"))[:200]
            safe_name = getattr(record, "name", "<unknown>")
            return (
                f"[LOGGING ERROR: Failed to format log record - {type(e).__name__}: {e}] "
                f"(logger={safe_name}, msg={safe_msg})"
            )
        except Exception as e:
            # Unexpected errors - log to stderr to surface bugs
            print(f"CRITICAL: Unexpected error in SecureFormatter: {e}", file=sys.stderr)
            raise

        # Redact API keys with size limit to prevent ReDoS
        try:
            # Limit message size to prevent potential regex issues with extremely large inputs
            if len(formatted) > 100_000:  # 100KB limit
                formatted = formatted[:100_000] + "... [truncated for safety]"

            formatted = self.API_KEY_PATTERN.sub("sk-or-v1-***[redacted]", formatted)
        except re.error as e:
            # Regex pattern is broken - this is a code bug
            print(f"CRITICAL: Redaction regex failed: {e}", file=sys.stderr)
            return f"[REDACTION REGEX ERROR: {e}] - message suppressed for security"
        except (MemoryError, RecursionError) as e:
            # Resource exhaustion in regex - log it
            print(f"ERROR: Redaction failed due to {type(e).__name__}: {e}", file=sys.stderr)
            return f"[REDACTION ERROR: {type(e).__name__}] - message suppressed for security"
        except Exception as e:
            # Unexpected error - log and re-raise to surface bugs
            print(f"UNEXPECTED: Redaction failure: {type(e).__name__}: {e}", file=sys.stderr)
            raise

        return formatted


class SecureJSONFormatter(SecureFormatter):
    """JSON log formatter with API key redaction and structured context.

    Outputs log records as JSON with standard fields plus custom context fields
    from the 'extra' parameter. API keys are automatically redacted.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with redacted API keys.

        Args:
            record: The log record to format.

        Returns:
            JSON-formatted log message with API keys redacted.
        """
        try:
            # Build base log entry
            log_entry: dict[str, Any] = {
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }

            # Add exception info if present
            if record.exc_info:
                log_entry["exception"] = self.formatException(record.exc_info)

            # Add custom context fields from 'extra'
            # These are fields added via logger.info("msg", extra={...})
            for key, value in record.__dict__.items():
                # Skip internal logging fields and private attributes
                if key.startswith("_"):
                    continue
                if key not in {
                    "name",
                    "msg",
                    "args",
                    "created",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "message",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                    "taskName",
                }:
                    log_entry[key] = value

            # Convert to JSON string
            json_str = json.dumps(log_entry, default=str)

            # Redact API keys from the JSON string
            json_str = self.API_KEY_PATTERN.sub("sk-or-v1-***[redacted]", json_str)

            return json_str

        except Exception as e:
            # Fallback to safe error message
            error_entry = {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": "ERROR",
                "logger": "logging",
                "message": f"[LOGGING ERROR: {type(e).__name__}]",
            }
            return json.dumps(error_entry)


def configure_secure_logging(
    level: int = logging.INFO,
    format_string: str | None = None,
    json_format: bool = False,
) -> None:
    """Configure logging with secure formatter that redacts API keys.

    Args:
        level: Logging level (default: INFO).
        format_string: Custom format string for text logging (default: standard format with timestamp).
                      Ignored if json_format=True.
        json_format: If True, use JSON structured logging format (default: False).
    """
    # Create appropriate formatter
    if json_format:
        formatter = SecureJSONFormatter()
    else:
        if format_string is None:
            format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        formatter = SecureFormatter(format_string)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers and add new one with secure formatter
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add console handler with secure formatter
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
