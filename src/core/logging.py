"""Secure logging configuration with API key redaction.

Provides a custom log formatter that automatically redacts OpenRouter API keys
from log messages to prevent credential exposure in centralized logging systems.
"""

import logging
import re


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
        formatted = super().format(record)

        # Redact API keys
        formatted = self.API_KEY_PATTERN.sub("sk-or-v1-***[redacted]", formatted)

        return formatted


def configure_secure_logging(
    level: int = logging.INFO,
    format_string: str | None = None,
) -> None:
    """Configure logging with secure formatter that redacts API keys.

    Args:
        level: Logging level (default: INFO).
        format_string: Custom format string (default: standard format with timestamp).
    """
    if format_string is None:
        format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Create secure formatter
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
