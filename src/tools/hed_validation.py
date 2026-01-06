"""HED validation tools using hedtools.org REST API.

These tools enable the HED assistant to validate its own examples before presenting
them to users, ensuring accuracy and building trust.

The hedtools.org API requires CSRF protection. The workflow is:
1. GET /services to obtain session cookie and CSRF token
2. POST to /services_submit with X-CSRFToken header and Cookie
"""

import re
from typing import Any

import httpx
from langchain_core.tools import tool


def _get_session_info(base_url: str = "https://hedtools.org/hed") -> tuple[str, str]:
    """Get session cookie and CSRF token from hedtools.org.

    Args:
        base_url: Base URL for HED tools

    Returns:
        Tuple of (cookie_value, csrf_token)

    Raises:
        httpx.HTTPError: If session setup fails
    """
    csrf_url = f"{base_url}/services"

    response = httpx.get(csrf_url, timeout=10.0, follow_redirects=True)
    response.raise_for_status()

    # Extract cookie from Set-Cookie header
    cookie = response.cookies.get("session")
    if not cookie:
        # Try getting from Set-Cookie header directly
        set_cookie = response.headers.get("set-cookie", "")
        cookie_match = re.search(r"session=([^;]+)", set_cookie)
        cookie = cookie_match.group(1) if cookie_match else ""

    if not cookie:
        raise ValueError("Failed to obtain session cookie from hedtools.org")

    # Extract CSRF token from HTML response
    # Format: <input type="hidden" name="csrf_token" value="TOKEN_HERE"/>
    html = response.text
    csrf_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if not csrf_match:
        raise ValueError("Failed to extract CSRF token from hedtools.org response")

    csrf_token = csrf_match.group(1)

    return cookie, csrf_token


@tool
def validate_hed_string(hed_string: str, schema_version: str = "8.4.0") -> dict[str, Any]:
    """Validate a HED annotation string using the hedtools.org API.

    **Primary Use**: Self-check tool for the agent to validate examples BEFORE showing to users.

    **Workflow**:
    1. Generate an example HED string based on documentation
    2. Call this tool to validate the example
    3. If valid: Present to user
    4. If invalid: Fix based on error messages OR use known-good example from docs

    This prevents the agent from confidently giving invalid examples to researchers.

    Args:
        hed_string: The HED annotation string to validate (e.g., "Onset, Sensory-event")
        schema_version: HED schema version to validate against (default: "8.4.0")

    Returns:
        dict with:
            - valid (bool): Whether the string is valid
            - errors (str): Error messages if invalid, empty if valid
            - schema_version (str): Schema version used for validation

    Example:
        >>> result = validate_hed_string("Onset, Sensory-event")
        >>> if result["valid"]:
        ...     print("Safe to show this example to user!")
        ... else:
        ...     print(f"Fix needed: {result['errors']}")
    """
    base_url = "https://hedtools.org/hed"
    url = f"{base_url}/services_submit"

    try:
        # Get session cookie and CSRF token
        cookie, csrf_token = _get_session_info(base_url)

        # Prepare payload (service name changed from docs - API uses strings_validate)
        payload = {
            "service": "strings_validate",
            "schema_version": schema_version,
            "string_list": [hed_string],
            "check_for_warnings": False,
        }

        # Make request with CSRF protection headers
        headers = {
            "X-CSRFToken": csrf_token,
            "Cookie": f"session={cookie}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        response = httpx.post(url, json=payload, headers=headers, timeout=30.0)
        response.raise_for_status()
        result = response.json()

        results = result.get("results", {})
        msg_category = results.get("msg_category", "error")

        if msg_category == "success":
            return {
                "valid": True,
                "errors": "",
                "schema_version": results.get("schema_version", schema_version),
            }
        else:
            return {
                "valid": False,
                "errors": results.get("data", "Unknown validation error"),
                "schema_version": results.get("schema_version", schema_version),
            }

    except httpx.HTTPError as e:
        return {
            "valid": False,
            "errors": f"API error: {e}. Could not validate. Use examples from documentation instead.",
            "schema_version": schema_version,
        }
    except Exception as e:
        return {
            "valid": False,
            "errors": f"Validation failed: {e}. Use examples from documentation instead.",
            "schema_version": schema_version,
        }


@tool
def get_hed_schema_versions() -> dict[str, Any]:
    """Get list of available HED schema versions from hedtools.org.

    Use this to check which schema versions are available for validation.
    Most users should use the latest stable version (currently 8.4.0).

    Returns:
        dict with:
            - versions (list[str]): List of available schema versions
            - error (str): Error message if request failed

    Example:
        >>> result = get_hed_schema_versions()
        >>> print(result["versions"][:5])
        ['8.4.0', '8.3.0', '8.2.0', '8.1.0', '8.0.0']
    """
    url = "https://hedtools.org/hed/schema_versions"

    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        result = response.json()

        versions = result.get("schema_version_list", [])
        return {"versions": versions, "error": ""}

    except httpx.HTTPError as e:
        return {"versions": [], "error": f"API error: {e}"}
    except Exception as e:
        return {"versions": [], "error": f"Failed to get versions: {e}"}
