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
def suggest_hed_tags(search_terms: list[str], top_n: int = 10) -> dict[str, list[str]]:
    """Suggest valid HED tags for natural language search terms.

    Use this tool to find valid HED tags that match natural language descriptions.
    This uses the hed-lsp semantic search to find relevant tags from the HED schema.

    **Primary Use**: Convert natural language concepts to valid HED tags.

    **Workflow**:
    1. User describes events in natural language (e.g., "button press", "visual flash")
    2. Call this tool to get valid HED tags for each concept
    3. Use the suggested tags to construct valid HED annotation strings
    4. Validate the final string with validate_hed_string before showing to user

    Args:
        search_terms: List of natural language terms to search for HED tags
                     (e.g., ["button press", "visual stimulus", "response"])
        top_n: Maximum number of suggestions per term (default: 10)

    Returns:
        dict mapping each search term to a list of suggested HED tags.
        If hed-lsp is not available, returns empty lists with error message.

    Example:
        >>> result = suggest_hed_tags(["button press", "visual flash"])
        >>> print(result)
        {
            "button press": ["Button", "Response-button", "Mouse-button", "Press"],
            "visual flash": ["Flash", "Flickering", "Visual-presentation"]
        }
    """
    import json
    import os
    import shutil
    import subprocess

    # Try to find hed-suggest CLI
    # 1. Check if it's in PATH (global install)
    # 2. Check configured path via HED_LSP_PATH env var
    # 3. Check common local dev path
    cli_path = shutil.which("hed-suggest")

    if not cli_path:
        # Check env var for local dev path
        hed_lsp_path = os.environ.get("HED_LSP_PATH")
        if hed_lsp_path:
            candidate = os.path.join(hed_lsp_path, "server", "out", "cli.js")
            if os.path.exists(candidate):
                cli_path = candidate

    if not cli_path:
        # Check common local dev path
        dev_path = os.path.expanduser("~/Documents/git/HED/hed-lsp/server/out/cli.js")
        if os.path.exists(dev_path):
            cli_path = dev_path

    if not cli_path:
        # Return empty results with error
        return {term: [] for term in search_terms}

    try:
        # Build command
        cmd = ["node", cli_path] if cli_path.endswith(".js") else [cli_path]
        cmd.extend(["--json", "--top", str(top_n)])
        cmd.extend(search_terms)

        # Run CLI
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            # CLI failed, return empty results
            return {term: [] for term in search_terms}

        # Parse JSON from stdout
        output = json.loads(result.stdout)
        return output

    except subprocess.TimeoutExpired:
        return {term: [] for term in search_terms}
    except json.JSONDecodeError:
        return {term: [] for term in search_terms}
    except Exception:
        return {term: [] for term in search_terms}


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
