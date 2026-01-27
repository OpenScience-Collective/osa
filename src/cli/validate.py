"""Config validation command for OSA CLI.

Provides validation of community configuration files before deployment.
Catches YAML syntax errors, schema validation errors, and missing dependencies.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx
import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from src.core.config.community import CommunityConfig

console = Console()
logger = logging.getLogger(__name__)


def validate(
    config_path: Path | None = typer.Argument(None, help="Path to community config.yaml file"),
    community: str | None = typer.Option(
        None,
        "--community",
        "-c",
        help="Community ID to validate (runs full test suite including URL checks)",
    ),
    test_api_key: bool = typer.Option(
        False,
        "--test-api-key",
        help="Test that API key works by making a request to OpenRouter",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show verbose pytest output when using --community",
    ),
) -> None:
    """Validate a community configuration file.

    Two modes:
    1. File mode: osa validate <config_path>
       - YAML syntax, schema validation, env vars
       - Optionally test API key with --test-api-key
    2. Community mode: osa validate --community <id>
       - Full test suite including URL accessibility, GitHub repo validation
       - Use --verbose for detailed pytest output

    Returns exit code 0 on success, 1 on failure.
    """
    # Validate arguments
    if community and config_path:
        console.print("[red]Error: Cannot specify both config_path and --community[/red]")
        console.print("Use either:")
        console.print("  • osa validate <config_path>")
        console.print("  • osa validate --community <id>")
        raise typer.Exit(1)

    if not community and not config_path:
        console.print("[red]Error: Must specify either config_path or --community[/red]")
        console.print("Use either:")
        console.print("  • osa validate <config_path>")
        console.print("  • osa validate --community <id>")
        raise typer.Exit(1)

    # Community mode: Run pytest tests
    if community:
        _validate_community_with_tests(community, verbose)
        return

    # File mode: Direct config validation
    assert config_path is not None  # Guaranteed by argument validation above
    logger.info("Starting validation for config: %s", config_path)

    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        console.print(f"[red]Error: Config file not found: {config_path}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]Validating configuration:[/bold] {config_path}\n")

    # Track validation results
    checks = []
    warnings = []
    errors = []

    # Step 1: Read file with proper error handling
    console.print("[dim]Reading config file...[/dim]")
    try:
        with open(config_path, encoding="utf-8") as f:
            raw_content = f.read()
    except PermissionError:
        logger.error("Permission denied reading config: %s", config_path)
        console.print(f"[red]Error: Permission denied reading config file: {config_path}[/red]")
        console.print("[yellow]Check file permissions and try again.[/yellow]")
        raise typer.Exit(1)
    except IsADirectoryError:
        logger.error("Config path is a directory: %s", config_path)
        console.print(f"[red]Error: Path is a directory, not a file: {config_path}[/red]")
        raise typer.Exit(1)
    except OSError as e:
        logger.error("Cannot read config file %s: %s", config_path, e)
        console.print(f"[red]Error: Cannot read config file: {e}[/red]")
        raise typer.Exit(1)

    # Step 2: YAML Syntax
    console.print("[dim]Checking YAML syntax...[/dim]")
    try:
        yaml_data = yaml.safe_load(raw_content)

        # Check for empty file
        if yaml_data is None:
            logger.error("Config file is empty or contains only comments: %s", config_path)
            errors.append("Config file is empty or contains only comments")
            checks.append(("YAML Content", "✗ File is empty", "red"))
            _display_results(checks, warnings, errors)
            raise typer.Exit(1)

        logger.debug("YAML syntax valid")
        checks.append(("YAML Syntax", "✓ Valid", "green"))
    except yaml.YAMLError as e:
        error_msg = f"YAML syntax error: {e}"
        if hasattr(e, "problem_mark") and e.problem_mark is not None:
            mark = e.problem_mark
            problem_desc = getattr(e, "problem", str(e))
            error_msg = f"YAML syntax error at line {mark.line + 1}, column {mark.column + 1}: {problem_desc}"
        logger.error("YAML syntax error in %s: %s", config_path, error_msg, exc_info=True)
        errors.append(error_msg)
        checks.append(("YAML Syntax", f"✗ {error_msg}", "red"))
        _display_results(checks, warnings, errors)
        raise typer.Exit(1)

    # Step 3: Pydantic Schema Validation
    console.print("[dim]Validating schema...[/dim]")
    try:
        config = CommunityConfig.model_validate(yaml_data)
        logger.debug("Schema validation passed for community: %s", config.id)
        checks.append(("Schema Validation", "✓ Valid", "green"))
    except ValidationError as e:
        logger.error("Schema validation failed for %s", config_path, exc_info=True)
        errors.append("Schema validation failed")
        checks.append(("Schema Validation", "✗ Invalid schema", "red"))

        # Format validation errors clearly with actual values
        console.print("\n[red]Schema Validation Errors:[/red]\n")
        for error in e.errors():
            field = " → ".join(str(x) for x in error["loc"])
            message = error["msg"]

            # Include the actual invalid value
            if "input" in error and error["input"] is not None:
                invalid_value = error["input"]
                value_str = str(invalid_value)
                # Truncate long values
                if len(value_str) > 50:
                    value_str = value_str[:47] + "..."
                console.print(f"  [yellow]•[/yellow] [bold]{field}[/bold]: {message}")
                console.print(f"    Got: [red]{value_str}[/red]")
            else:
                console.print(f"  [yellow]•[/yellow] [bold]{field}[/bold]: {message}")

        _display_results(checks, warnings, errors)
        raise typer.Exit(1)

    # Step 4: Configuration Details
    console.print("[dim]Checking configuration...[/dim]")
    checks.append(("Community ID", config.id, "cyan"))
    checks.append(("Community Name", config.name, "cyan"))
    checks.append(("CORS Origins", f"{len(config.cors_origins)} configured", "cyan"))
    checks.append(("Documentation", f"{len(config.documentation)} docs", "cyan"))

    # GitHub repos
    if config.github:
        checks.append(("GitHub Repos", f"{len(config.github.repos)} repos", "cyan"))

    # Step 5: Environment Variable Check
    console.print("[dim]Checking environment variables...[/dim]")
    if config.openrouter_api_key_env_var:
        env_var_name = config.openrouter_api_key_env_var
        api_key = os.getenv(env_var_name)

        if not api_key:
            logger.warning("API key env var not set: %s", env_var_name)
            warnings.append(
                f"Environment variable '{env_var_name}' is not set. "
                "The assistant will fall back to the platform API key, "
                "and costs will be billed to the platform (not your community)."
            )
            checks.append(("API Key Env Var", f"⚠ {env_var_name} not set", "yellow"))
        else:
            logger.debug("API key env var is set: %s", env_var_name)
            checks.append(("API Key Env Var", f"✓ {env_var_name} is set", "green"))

            # Step 6: Optional API Key Test
            if test_api_key:
                console.print("[dim]Testing API key with OpenRouter...[/dim]")
                logger.info("Testing API key for %s", env_var_name)
                test_result = _test_openrouter_api_key(api_key)
                if test_result["success"]:
                    logger.info("API key test passed for %s", env_var_name)
                    checks.append(("API Key Test", "✓ Key works", "green"))
                else:
                    logger.error(
                        "API key test failed for %s: %s", env_var_name, test_result["error"]
                    )
                    errors.append(f"API key test failed: {test_result['error']}")
                    checks.append(("API Key Test", f"✗ {test_result['error']}", "red"))
    else:
        logger.debug("No community-specific API key configured, will use platform key")
        checks.append(("API Key Env Var", "Not configured (using platform key)", "cyan"))

    # Step 7: Model Configuration
    if config.default_model:
        checks.append(("Default Model", config.default_model, "cyan"))
        if config.default_model_provider:
            checks.append(("Model Provider", config.default_model_provider, "cyan"))

    # Display results
    _display_results(checks, warnings, errors)

    # Exit with appropriate code
    if errors:
        logger.error("Validation failed for %s with %d errors", config_path, len(errors))
        console.print("\n[red]✗ Validation failed[/red]\n")
        raise typer.Exit(1)
    elif warnings:
        logger.warning("Validation passed with %d warnings for %s", len(warnings), config_path)
        console.print("\n[yellow]✓ Validation passed with warnings[/yellow]\n")
        raise typer.Exit(0)
    else:
        logger.info("Validation passed successfully for %s", config_path)
        console.print("\n[green]✓ Validation passed[/green]\n")
        raise typer.Exit(0)


def _validate_community_with_tests(community_id: str, verbose: bool) -> None:
    """Run pytest tests for a specific community via subprocess.

    Executes the generic test suite (test_community_yaml_generic.py) filtered
    to the specified community. Runs pytest in a subprocess to provide clean
    test isolation and user-friendly output formatting.

    Args:
        community_id: The community ID to validate (e.g., 'hed', 'eeglab')
        verbose: Whether to show verbose pytest output (-v flag)

    Raises:
        typer.Exit: With code 0 on success, 1 on failure
    """
    console.print(f"\n[bold]Validating community:[/bold] {community_id}\n")

    # Check if community exists
    from src.assistants import discover_assistants, registry

    registry._assistants.clear()
    discover_assistants()

    if community_id not in registry:
        console.print(f"[red]Error: Community '{community_id}' not found[/red]\n")
        console.print("Available communities:")
        for info in registry.list_all():
            console.print(f"  • {info.id}")
        raise typer.Exit(1)

    # Get community info
    info = registry.get(community_id)
    assert info is not None  # Guaranteed by community_id in registry check above
    console.print(f"[cyan]Name:[/cyan] {info.name}")
    console.print(f"[cyan]Description:[/cyan] {info.description}")
    console.print(f"[cyan]Status:[/cyan] {info.status}\n")

    # Show configuration summary
    config = registry.get_community_config(community_id)
    if config.documentation:
        console.print(f"[dim]Documentation sources: {len(config.documentation)}[/dim]")
    if config.github and config.github.repos:
        console.print(f"[dim]GitHub repositories: {len(config.github.repos)}[/dim]")

    # Run pytest tests for this community
    console.print("[dim]Running test suite (this may take a few seconds)...[/dim]\n")

    # Build pytest command
    pytest_args = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_assistants/test_community_yaml_generic.py",
        "-k",
        community_id,
        "--tb=short",
        "-q" if not verbose else "-v",
        "--color=yes",
    ]

    # Run pytest
    result = subprocess.run(pytest_args, capture_output=True, text=True)

    # Display output
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)

    # Check result
    if result.returncode == 0:
        console.print(f"\n[green]✓ All tests passed for {community_id}[/green]\n")
        raise typer.Exit(0)
    else:
        console.print(f"\n[red]✗ Tests failed for {community_id}[/red]\n")
        console.print("[yellow]Tip:[/yellow] Run with --verbose for more details")
        console.print(
            f"[yellow]Or:[/yellow] pytest tests/test_assistants/test_community_yaml_generic.py -k {community_id} -v"
        )
        raise typer.Exit(1)


def _interpret_api_response(status_code: int, response_text: str = "") -> dict:
    """Interpret OpenRouter API response.

    Pure function to interpret HTTP response - easy to test without mocking.

    Args:
        status_code: HTTP status code from OpenRouter API
        response_text: Response body text (for error details)

    Returns:
        Dict with 'success' bool and optional 'error' message.
    """
    if status_code == 200:
        return {"success": True}
    elif status_code == 401:
        return {"success": False, "error": "Invalid API key (401 Unauthorized)"}
    elif status_code == 403:
        return {"success": False, "error": "API key lacks permissions (403 Forbidden)"}
    else:
        # Include response body for unexpected status codes
        error_msg = f"Unexpected status code: {status_code}"
        if response_text:
            # Truncate long responses
            error_detail = response_text[:200] if len(response_text) > 200 else response_text
            error_msg = f"{error_msg} - {error_detail}"
        return {"success": False, "error": error_msg}


def _test_openrouter_api_key(api_key: str) -> dict:
    """Test if an OpenRouter API key works.

    Makes a simple request to the OpenRouter /models endpoint to verify
    the key is valid and has appropriate permissions.

    Args:
        api_key: The OpenRouter API key to test.

    Returns:
        Dict with 'success' bool and 'error' message (only present when success=False).
    """
    try:
        response = httpx.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        return _interpret_api_response(response.status_code, response.text)
    except httpx.TimeoutException:
        logger.warning("API key test timeout after 10s")
        return {"success": False, "error": "Request timeout (>10s)"}
    except httpx.RequestError as e:
        logger.warning("API key test network error: %s", e)
        return {"success": False, "error": f"Network error: {e}"}
    # No broad exception handler - let unexpected errors propagate


def _display_results(
    checks: list[tuple[str, str, str]],
    warnings: list[str],
    errors: list[str],
) -> None:
    """Display validation results in a formatted table.

    Args:
        checks: List of (check_name, result, color) tuples.
        warnings: List of warning messages.
        errors: List of error messages.
    """
    # Create results table
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Check", style="dim")
    table.add_column("Result")

    for check_name, result, color in checks:
        table.add_row(check_name, f"[{color}]{result}[/{color}]")

    console.print("\n")
    console.print(table)

    # Display warnings
    if warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for warning in warnings:
            console.print(f"  [yellow]⚠[/yellow]  {warning}")

    # Display errors
    if errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for error in errors:
            console.print(f"  [red]✗[/red]  {error}")
