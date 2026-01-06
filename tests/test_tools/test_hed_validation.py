"""Tests for HED validation tools.

These tests call the real hedtools.org API to ensure validation works correctly.
NO MOCKS - we test against the actual service.
"""

import pytest

from src.tools.hed_validation import get_hed_schema_versions, validate_hed_string


class TestValidateHedString:
    """Tests for validate_hed_string tool."""

    def test_valid_hed_string(self):
        """Test that a valid HED string passes validation."""
        # Simple valid HED string
        result = validate_hed_string.invoke(
            {"hed_string": "Sensory-event", "schema_version": "8.4.0"}
        )

        assert result["valid"] is True
        assert result["errors"] == ""
        assert "8.4.0" in result["schema_version"]

    def test_valid_complex_hed_string(self):
        """Test validation of a more complex valid HED string."""
        # More complex valid annotation with grouped tags
        hed_string = "Sensory-event, Visual-presentation, (Red, Blue)"
        result = validate_hed_string.invoke({"hed_string": hed_string, "schema_version": "8.4.0"})

        assert result["valid"] is True
        assert result["errors"] == ""

    def test_invalid_hed_string_unknown_tag(self):
        """Test that an invalid HED string with unknown tag fails validation."""
        # This tag doesn't exist in HED schema
        hed_string = "InvalidTagThatDoesNotExist"
        result = validate_hed_string.invoke({"hed_string": hed_string, "schema_version": "8.4.0"})

        assert result["valid"] is False
        assert len(result["errors"]) > 0
        assert "error" in result["errors"].lower() or "invalid" in result["errors"].lower()

    def test_invalid_hed_string_syntax_error(self):
        """Test that HED string with syntax error fails validation."""
        # Unmatched parentheses
        hed_string = "Sensory-event, (Red, Blue"
        result = validate_hed_string.invoke({"hed_string": hed_string, "schema_version": "8.4.0"})

        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_default_schema_version(self):
        """Test that default schema version is used when not specified."""
        result = validate_hed_string.invoke({"hed_string": "Sensory-event"})

        assert result["valid"] is True
        # Should use default version (8.4.0 or similar)
        assert len(result["schema_version"]) > 0

    def test_different_schema_version(self):
        """Test validation with a different schema version."""
        # Use an older schema version
        result = validate_hed_string.invoke(
            {"hed_string": "Sensory-event", "schema_version": "8.3.0"}
        )

        assert result["valid"] is True
        assert "8.3.0" in result["schema_version"]


class TestGetHedSchemaVersions:
    """Tests for get_hed_schema_versions tool."""

    def test_get_schema_versions(self):
        """Test that we can retrieve list of available HED schema versions."""
        result = get_hed_schema_versions.invoke({})

        assert "versions" in result
        assert "error" in result
        assert result["error"] == ""
        assert len(result["versions"]) > 0

        # Check that versions look reasonable
        assert "8.4.0" in result["versions"] or "8.3.0" in result["versions"]

    def test_schema_versions_format(self):
        """Test that schema versions are in expected format."""
        result = get_hed_schema_versions.invoke({})

        versions = result["versions"]
        assert len(versions) > 0

        # Check format of version strings (should be like "8.4.0")
        for version in versions[:5]:  # Check first 5
            parts = version.split(".")
            # Should have at least 2 parts (major.minor) or 3 (major.minor.patch)
            assert len(parts) >= 2
            # First part should be numeric
            assert parts[0].replace("(prerelease)", "").strip().split()[0].isdigit()


@pytest.mark.integration
class TestValidationIntegration:
    """Integration tests for validation workflow.

    These test the complete workflow that the agent would use.
    """

    def test_agent_workflow_valid_example(self):
        """Test the workflow for when agent generates a valid example."""
        # Agent generates an example
        agent_example = "Sensory-event, Visual-presentation, (Red, Blue)"

        # Agent validates before showing to user
        result = validate_hed_string.invoke(
            {"hed_string": agent_example, "schema_version": "8.4.0"}
        )

        # Valid - agent can show to user
        assert result["valid"] is True

    def test_agent_workflow_invalid_example_needs_fix(self):
        """Test the workflow for when agent generates an invalid example and needs to fix it."""
        # Agent generates an example (intentionally wrong - unmatched parenthesis)
        agent_example = "Sensory-event, InvalidTag, (Red, Blue"

        # Agent validates
        result = validate_hed_string.invoke(
            {"hed_string": agent_example, "schema_version": "8.4.0"}
        )

        # Invalid - agent should NOT show to user
        assert result["valid"] is False

        # Agent sees errors and fixes
        errors = result["errors"]
        assert len(errors) > 0

        # Agent tries a corrected version
        fixed_example = "Sensory-event, (Red, Blue)"
        result2 = validate_hed_string.invoke(
            {"hed_string": fixed_example, "schema_version": "8.4.0"}
        )

        # Now valid - agent can show to user
        assert result2["valid"] is True

    def test_multiple_validation_attempts(self):
        """Test that agent can iterate on validation until correct."""
        examples = [
            ("InvalidTag", False),  # First attempt: wrong
            ("Sensory-event, InvalidOther", False),  # Second attempt: still wrong
            ("Sensory-event, Visual-presentation", True),  # Third attempt: correct!
        ]

        for hed_string, should_be_valid in examples:
            result = validate_hed_string.invoke(
                {"hed_string": hed_string, "schema_version": "8.4.0"}
            )
            assert result["valid"] == should_be_valid

            if should_be_valid:
                # Success - agent can show this to user
                break
