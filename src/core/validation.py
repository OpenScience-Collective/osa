"""Shared input validation utilities.

Provides common validation functions used across modules for
preventing path traversal and ensuring safe identifiers.
"""


def is_safe_identifier(value: str) -> bool:
    """Check if a string is a safe identifier (alphanumeric, hyphens, underscores).

    Used for both mirror IDs and community IDs to prevent path traversal.
    """
    return bool(value) and value.replace("-", "").replace("_", "").isalnum()
