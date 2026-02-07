#!/usr/bin/env python3
"""Sync CORS origins from community configs to Cloudflare Worker.

Reads all community config.yaml files, extracts cors_origins, and updates
the worker's isAllowedOrigin function to match. Platform origins like
demo.osc.earth remain hardcoded.
"""

import re
from pathlib import Path

import yaml


def extract_all_cors_origins():
    """Extract CORS origins from all community config files."""
    origins = set()
    config_dir = Path("src/assistants")

    if not config_dir.exists():
        print(f"Warning: {config_dir} not found")
        return origins

    for config_file in config_dir.glob("*/config.yaml"):
        try:
            with open(config_file) as f:
                config = yaml.safe_load(f)
                if config and "cors_origins" in config:
                    for origin in config["cors_origins"]:
                        origins.add(origin)
                        print(f"  Found: {origin} (from {config_file.parent.name})")
        except Exception as e:
            print(f"Warning: Failed to parse {config_file}: {e}")

    return sorted(origins)


def update_worker_cors(origins):
    """Update worker's isAllowedOrigin function with community origins."""
    worker_file = Path("workers/osa-worker/index.js")

    if not worker_file.exists():
        print(f"Error: {worker_file} not found")
        return False

    content = worker_file.read_text()

    # Find the entire isAllowedOrigin function (from function declaration to closing brace)
    pattern = r"function isAllowedOrigin\(origin\) \{[^}]*\}"

    match = re.search(pattern, content, re.DOTALL)
    if not match:
        print("Error: Could not find isAllowedOrigin function in worker file")
        return False

    # Generate new allowedPatterns array
    # Keep platform origins separate from community origins
    platform_origins = [
        "'https://osc.earth'",
    ]
    community_origin_entries = [f"'{origin}'" for origin in origins]

    new_patterns = ",\n    ".join(platform_origins + community_origin_entries)

    # Extract domains for subdomain checks
    # Extract base domains (e.g., "example.org" from "https://example.org" or "https://www.example.org")
    domains = set()
    for origin in origins:
        # Remove protocol
        domain = origin.replace("https://", "").replace("http://", "")
        # Remove www. prefix if present
        if domain.startswith("www."):
            domain = domain[4:]
        # Take the base domain (last two parts for most TLDs)
        parts = domain.split(".")
        if len(parts) >= 2:
            base = f"{parts[-2]}.{parts[-1]}"
            domains.add(base)

    subdomain_checks = "\n  ".join(
        f"if (origin.endsWith('.{domain}')) return true;" for domain in sorted(domains)
    )

    # Build the new function
    new_function = f"""function isAllowedOrigin(origin) {{
  if (!origin) return false;

  // Allowed origins for OSA
  const allowedPatterns = [
    {new_patterns}
  ];

  // Check exact matches
  if (allowedPatterns.includes(origin)) return true;

  // Check subdomains
  {subdomain_checks}

  // Allow demo.osc.earth and subdomains (develop, PR previews)
  if (origin === 'https://demo.osc.earth') return true;
  if (origin.startsWith('https://') && origin.endsWith('.demo.osc.earth')) return true;

  // Allow osa-demo.pages.dev and subdomains (backward compatibility)
  if (origin === 'https://osa-demo.pages.dev') return true;
  if (origin.startsWith('https://') && origin.endsWith('.osa-demo.pages.dev')) return true;

  // Allow localhost for development
  if (origin.startsWith('http://localhost:')) return true;
  if (origin.startsWith('http://127.0.0.1:')) return true;

  return false;
}}"""

    # Replace the function
    new_content = re.sub(pattern, new_function, content, flags=re.DOTALL)

    if new_content == content:
        print("No changes needed")
        return False

    worker_file.write_text(new_content)
    print(f"Updated {worker_file}")
    return True


def main():
    print("Extracting CORS origins from community configs...")
    origins = extract_all_cors_origins()

    if not origins:
        print("No CORS origins found in community configs")
        return

    print(f"\nFound {len(origins)} unique origins")

    print("\nUpdating worker CORS allowlist...")
    updated = update_worker_cors(origins)

    if updated:
        print("\n✅ Worker CORS updated successfully")
        print("Next steps:")
        print("  1. Review changes: git diff workers/osa-worker/index.js")
        print("  2. Commit: git add workers/osa-worker/index.js")
        print("  3. Deploy worker: cd workers/osa-worker && wrangler deploy")
    else:
        print("\n✅ Worker CORS already up to date")


if __name__ == "__main__":
    main()
