"""Config validation command for OSA CLI.

Provides validation of community configuration files before deployment.
Catches YAML syntax errors, schema validation errors, and missing dependencies.
"""

import os
from pathlib import Path

import httpx
import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from src.core.config.community import CommunityConfig

console = Console()


def validate(
    config_path: Path = typer.Argument(..., help="Path to community config.yaml file"),
    test_api_key: bool = typer.Option(
        False,
        "--test-api-key",
        help="Test that API key works by making a request to OpenRouter",
    ),
) -> None:
    """Validate a community configuration file.

    Checks:
    - YAML syntax
    - Pydantic schema validation
    - Environment variable presence
    - Optionally: API key functionality

    Returns exit code 0 on success, 1 on failure.
    """
    if not config_path.exists():
        console.print(f"[red]Error: Config file not found: {config_path}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]Validating configuration:[/bold] {config_path}\n")

    # Track validation results
    checks = []
    warnings = []
    errors = []

    # Step 1: YAML Syntax
    console.print("[dim]Checking YAML syntax...[/dim]")
    try:
        with open(config_path) as f:
            yaml_data = yaml.safe_load(f)
        checks.append(("YAML Syntax", "✓ Valid", "green"))
    except yaml.YAMLError as e:
        error_msg = f"YAML syntax error: {e}"
        if hasattr(e, "problem_mark") and e.problem_mark is not None:
            mark = e.problem_mark
            problem_desc = getattr(e, "problem", str(e))
            error_msg = f"YAML syntax error at line {mark.line + 1}, column {mark.column + 1}: {problem_desc}"
        errors.append(error_msg)
        checks.append(("YAML Syntax", f"✗ {error_msg}", "red"))
        _display_results(checks, warnings, errors)
        raise typer.Exit(1)

    # Step 2: Pydantic Schema Validation
    console.print("[dim]Validating schema...[/dim]")
    try:
        config = CommunityConfig.model_validate(yaml_data)
        checks.append(("Schema Validation", "✓ Valid", "green"))
    except ValidationError as e:
        errors.append("Schema validation failed")
        checks.append(("Schema Validation", "✗ Invalid schema", "red"))

        # Format validation errors clearly
        console.print("\n[red]Schema Validation Errors:[/red]\n")
        for error in e.errors():
            field = " → ".join(str(x) for x in error["loc"])
            message = error["msg"]
            console.print(f"  [yellow]•[/yellow] [bold]{field}[/bold]: {message}")

        _display_results(checks, warnings, errors)
        raise typer.Exit(1)

    # Step 3: Configuration Details
    console.print("[dim]Checking configuration...[/dim]")
    checks.append(("Community ID", config.id, "cyan"))
    checks.append(("Community Name", config.name, "cyan"))
    checks.append(("CORS Origins", f"{len(config.cors_origins)} configured", "cyan"))
    checks.append(("Documentation", f"{len(config.documentation)} docs", "cyan"))

    # GitHub repos
    if config.github:
        checks.append(("GitHub Repos", f"{len(config.github.repos)} repos", "cyan"))

    # Step 4: Environment Variable Check
    console.print("[dim]Checking environment variables...[/dim]")
    if config.openrouter_api_key_env_var:
        env_var_name = config.openrouter_api_key_env_var
        api_key = os.getenv(env_var_name)

        if not api_key:
            warnings.append(
                f"Environment variable '{env_var_name}' is not set. "
                "The assistant will fall back to the platform API key, "
                "and costs will be billed to the platform (not your community)."
            )
            checks.append(("API Key Env Var", f"⚠ {env_var_name} not set", "yellow"))
        else:
            checks.append(("API Key Env Var", f"✓ {env_var_name} is set", "green"))

            # Step 5: Optional API Key Test
            if test_api_key:
                console.print("[dim]Testing API key with OpenRouter...[/dim]")
                test_result = _test_openrouter_api_key(api_key)
                if test_result["success"]:
                    checks.append(("API Key Test", "✓ Key works", "green"))
                else:
                    errors.append(f"API key test failed: {test_result['error']}")
                    checks.append(("API Key Test", f"✗ {test_result['error']}", "red"))
    else:
        checks.append(("API Key Env Var", "Not configured (using platform key)", "cyan"))

    # Step 6: Model Configuration
    if config.default_model:
        checks.append(("Default Model", config.default_model, "cyan"))
        if config.default_model_provider:
            checks.append(("Model Provider", config.default_model_provider, "cyan"))

    # Display results
    _display_results(checks, warnings, errors)

    # Exit with appropriate code
    if errors:
        console.print("\n[red]✗ Validation failed[/red]\n")
        raise typer.Exit(1)
    elif warnings:
        console.print("\n[yellow]✓ Validation passed with warnings[/yellow]\n")
        raise typer.Exit(0)
    else:
        console.print("\n[green]✓ Validation passed[/green]\n")
        raise typer.Exit(0)


def _test_openrouter_api_key(api_key: str) -> dict:
    """Test if an OpenRouter API key works.

    Makes a simple request to the OpenRouter /models endpoint to verify
    the key is valid and has appropriate permissions.

    Args:
        api_key: The OpenRouter API key to test.

    Returns:
        Dict with 'success' bool and optional 'error' message.
    """
    try:
        response = httpx.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )

        if response.status_code == 200:
            return {"success": True}
        elif response.status_code == 401:
            return {"success": False, "error": "Invalid API key (401 Unauthorized)"}
        elif response.status_code == 403:
            return {"success": False, "error": "API key lacks permissions (403 Forbidden)"}
        else:
            return {
                "success": False,
                "error": f"Unexpected status code: {response.status_code}",
            }
    except httpx.TimeoutException:
        return {"success": False, "error": "Request timeout (>10s)"}
    except httpx.RequestError as e:
        return {"success": False, "error": f"Network error: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {e}"}


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
