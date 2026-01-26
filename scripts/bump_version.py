#!/usr/bin/env python3
"""Version bumping script for OSA.

This script helps manage semantic versioning with support for:
- major.minor.patch version bumping
- Pre-release labels (alpha, beta, rc, dev) in PEP 440 format
- Incrementing prerelease number (dev0 -> dev1, a0 -> a1)
- Changing prerelease label without version bump
- Automatic git tagging and GitHub release creation

PEP 440 Version Format:
    - dev:   0.6.3.dev0, 0.6.3.dev1, ...  (development pre-release)
    - alpha: 0.6.3a0, 0.6.3a1, ...        (alpha pre-release)
    - beta:  0.6.3b0, 0.6.3b1, ...        (beta pre-release)
    - rc:    0.6.3rc0, 0.6.3rc1, ...      (release candidate)
    - stable: 0.6.3                        (final release)

Usage:
    python scripts/bump_version.py [major|minor|patch] [--prerelease alpha|beta|rc|dev|stable]
    python scripts/bump_version.py --prerelease dev    # Increment prerelease: dev0 -> dev1
    python scripts/bump_version.py --prerelease alpha  # Change label: dev0 -> a0
    python scripts/bump_version.py --current           # Show current version

Options:
    --push              Automatically push commit and tag (requires bypass permissions for protected branches)
    --ci                CI mode: skip prompts, auto-push, auto-release
    --no-git            Skip git operations entirely

Examples:
    python scripts/bump_version.py patch                      # 0.3.0a0 -> 0.3.1a0
    python scripts/bump_version.py minor --prerelease beta    # 0.3.0a0 -> 0.4.0b0
    python scripts/bump_version.py major --prerelease stable  # 0.3.0a0 -> 1.0.0
    python scripts/bump_version.py --prerelease alpha         # 0.4.5.dev0 -> 0.4.5a0
    python scripts/bump_version.py --prerelease dev           # 0.4.5.dev0 -> 0.4.5.dev1
    python scripts/bump_version.py patch --prerelease dev --push  # Bump and push to remote
    python scripts/bump_version.py patch --ci             # CI mode: bump, push, release (no prompts)
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


class VersionBumper:
    """Handle version bumping and Git operations."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.version_file = project_root / "src" / "version.py"

    # PEP 440 prerelease prefix mapping (without number)
    PRERELEASE_PREFIXES = {
        "dev": ".dev",
        "alpha": "a",
        "beta": "b",
        "rc": "rc",
        "stable": "",
    }

    # Reverse mapping for parsing (pattern -> label)
    PRERELEASE_PATTERNS = {
        r"\.dev(\d+)": "dev",
        r"a(\d+)": "alpha",
        r"b(\d+)": "beta",
        r"rc(\d+)": "rc",
    }

    def get_current_version(self) -> tuple[int, int, int, str, int]:
        """Read the current version from version.py.

        Returns:
            Tuple of (major, minor, patch, prerelease_label, prerelease_num)
            For stable versions, prerelease_num is 0.
        """
        content = self.version_file.read_text()

        # Extract version string
        version_match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
        if not version_match:
            raise ValueError("Could not find __version__ in version.py")

        version_str = version_match.group(1)

        # Parse PEP 440 version string
        # Handles: 0.6.3, 0.6.3a0, 0.6.3b1, 0.6.3rc0, 0.6.3.dev0, 0.6.3.dev2
        match = re.match(r"(\d+)\.(\d+)\.(\d+)(?:[-.]?(.+))?", version_str)
        if not match:
            raise ValueError(f"Invalid version format: {version_str}")

        major, minor, patch, suffix = match.groups()

        # Determine prerelease type and number from suffix
        prerelease = "stable"
        prerelease_num = 0

        if suffix:
            # Check for PEP 440 suffixes with number
            for pattern, label in self.PRERELEASE_PATTERNS.items():
                suffix_match = re.match(pattern, suffix)
                if suffix_match:
                    prerelease = label
                    prerelease_num = int(suffix_match.group(1)) if suffix_match.group(1) else 0
                    break
            else:
                # Legacy format (e.g., -alpha, -dev) or just label without number
                suffix_lower = suffix.lower()
                # Try to extract number from end (e.g., dev2, alpha1)
                num_match = re.match(r"([a-z]+)(\d*)", suffix_lower)
                if num_match:
                    label_part = num_match.group(1)
                    num_part = num_match.group(2)
                    if label_part in self.PRERELEASE_PREFIXES:
                        prerelease = label_part
                        prerelease_num = int(num_part) if num_part else 0
                    elif label_part in ["a", "b"]:
                        # Handle short forms
                        prerelease = "alpha" if label_part == "a" else "beta"
                        prerelease_num = int(num_part) if num_part else 0

        return int(major), int(minor), int(patch), prerelease, prerelease_num

    def format_version(
        self, major: int, minor: int, patch: int, prerelease: str, prerelease_num: int = 0
    ) -> str:
        """Format version tuple into PEP 440 compliant version string."""
        version = f"{major}.{minor}.{patch}"
        prefix = self.PRERELEASE_PREFIXES.get(prerelease, "")
        if prefix:
            return f"{version}{prefix}{prerelease_num}"
        return version

    def bump_version(self, bump_type: str | None, new_prerelease: str = None) -> tuple[str, str]:
        """Bump version and return (old_version, new_version).

        Logic:
        - If bump_type is specified, bump the version number
        - If new_prerelease is specified:
          - If same as current prerelease and no bump_type: increment prerelease_num
          - If different from current prerelease: change label and reset num to 0
          - If bump_type is specified: reset prerelease_num to 0
        """
        major, minor, patch, prerelease, prerelease_num = self.get_current_version()
        old_version = self.format_version(major, minor, patch, prerelease, prerelease_num)

        new_prerelease_num = prerelease_num

        # Apply bump type (if specified)
        version_bumped = False
        if bump_type == "major":
            major += 1
            minor = 0
            patch = 0
            version_bumped = True
        elif bump_type == "minor":
            minor += 1
            patch = 0
            version_bumped = True
        elif bump_type == "patch":
            patch += 1
            version_bumped = True
        elif bump_type is not None:
            raise ValueError(f"Invalid bump type: {bump_type}")

        # Apply prerelease change if specified
        if new_prerelease is not None:
            if new_prerelease == prerelease and not version_bumped:
                # Same prerelease label and no version bump: increment the number
                new_prerelease_num = prerelease_num + 1
            else:
                # Different label or version was bumped: reset to 0
                new_prerelease_num = 0
            prerelease = new_prerelease
        elif version_bumped:
            # Version was bumped but no prerelease specified: reset prerelease num to 0
            new_prerelease_num = 0

        new_version = self.format_version(major, minor, patch, prerelease, new_prerelease_num)

        # Write new version to file
        self._write_version_file(major, minor, patch, prerelease, new_prerelease_num)

        return old_version, new_version

    def _write_version_file(
        self, major: int, minor: int, patch: int, prerelease: str, prerelease_num: int = 0
    ):
        """Write new version to version.py."""
        version_str = self.format_version(major, minor, patch, prerelease, prerelease_num)

        # Build version info tuple
        if prerelease and prerelease != "stable":
            # Include prerelease label with number for clarity (e.g., "dev2")
            prerelease_label = f"{prerelease}{prerelease_num}" if prerelease_num > 0 else prerelease
            version_info = f'({major}, {minor}, {patch}, "{prerelease_label}")'
        else:
            version_info = f"({major}, {minor}, {patch})"

        content = f'''"""Version information for OSA."""

__version__ = "{version_str}"
__version_info__ = {version_info}


def get_version() -> str:
    """Get the current version string."""
    return __version__


def get_version_info() -> tuple:
    """Get the version info tuple (major, minor, patch, prerelease)."""
    return __version_info__
'''

        self.version_file.write_text(content)
        print(f"Updated {self.version_file.relative_to(self.project_root)}")

    def git_commit_and_tag(self, version: str, skip_prompts: bool = False):
        """Commit version bump and create Git tag."""
        # Check if we're in a git repository
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=self.project_root, capture_output=True, text=True
        )

        if result.returncode != 0:
            print("Not in a Git repository. Skipping Git operations.")
            return False

        # Check for uncommitted changes (excluding version files)
        result = subprocess.run(
            ["git", "diff", "--name-only"], cwd=self.project_root, capture_output=True, text=True
        )

        uncommitted_files = [
            f for f in result.stdout.strip().split("\n") if f and not f.startswith("src/version.py")
        ]

        if uncommitted_files and not skip_prompts:
            print(f"Warning: Uncommitted changes detected in: {', '.join(uncommitted_files)}")
            response = input("Continue with version bump? (y/N): ")
            if response.lower() != "y":
                print("Aborted.")
                return False

        # Stage version files
        subprocess.run(["git", "add", "src/version.py"], cwd=self.project_root, check=True)

        # Commit
        commit_message = f"Bump version to {version}"
        subprocess.run(["git", "commit", "-m", commit_message], cwd=self.project_root, check=True)
        print(f"Committed version bump: {commit_message}")

        # Create tag
        tag_name = f"v{version}"
        subprocess.run(
            ["git", "tag", "-a", tag_name, "-m", f"Release {version}"],
            cwd=self.project_root,
            check=True,
        )
        print(f"Created tag: {tag_name}")

        return True

    def push_to_remote(self, version: str, skip_prompts: bool = False):
        """Push commit and tag to remote repository.

        Args:
            version: Version string (e.g., "0.5.1.dev0")
            skip_prompts: If True, skip confirmation prompts (for CI mode)

        Returns:
            True if push succeeded, False otherwise
        """
        # Get current branch
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        current_branch = result.stdout.strip()

        tag_name = f"v{version}"

        if not skip_prompts:
            print("\nReady to push to remote:")
            print(f"  Branch: {current_branch}")
            print(f"  Tag: {tag_name}")
            response = input("\nProceed with push? (y/N): ")
            if response.lower() != "y":
                print("Push cancelled.")
                return False

        # Push branch
        print(f"\nPushing {current_branch} to origin...")
        result = subprocess.run(
            ["git", "push", "origin", current_branch],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print("❌ Failed to push branch to origin:")
            print(result.stderr)
            print("\nPossible reasons:")
            print("  - Branch protection requires PR (you need bypass permissions)")
            print("  - Required status checks haven't passed yet")
            print("  - Remote branch has diverged")
            print("\nYou can manually push later with:")
            print(f"  git push origin {current_branch}")
            print(f"  git push origin {tag_name}")
            return False

        print(f"✓ Pushed {current_branch} to origin")

        # Push tag
        print(f"Pushing tag {tag_name} to origin...")
        result = subprocess.run(
            ["git", "push", "origin", tag_name],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print("❌ Failed to push tag to origin:")
            print(result.stderr)
            print("\nYou can manually push the tag later with:")
            print(f"  git push origin {tag_name}")
            return False

        print(f"✓ Pushed tag {tag_name} to origin")
        return True

    def create_github_release(self, version: str):
        """Create GitHub release using gh CLI."""
        # Check if gh CLI is available
        result = subprocess.run(["gh", "--version"], capture_output=True, text=True)

        if result.returncode != 0:
            print("GitHub CLI (gh) not found. Install it to create releases automatically.")
            print("  See: https://cli.github.com/")
            return False

        tag_name = f"v{version}"

        # Generate release notes
        result = subprocess.run(
            ["git", "log", "--oneline", "--no-decorate", "-10"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )
        recent_commits = result.stdout.strip()

        release_notes = f"""Release {version}

## Recent Changes
{recent_commits}

For full changelog, see commit history.
"""

        # Build release command
        release_cmd = [
            "gh",
            "release",
            "create",
            tag_name,
            "--title",
            f"Release {version}",
            "--notes",
            release_notes,
        ]

        # Add --prerelease flag for alpha/beta/rc/dev versions
        if any(label in version.lower() for label in ["alpha", "beta", "rc", "dev", "a", "b"]):
            release_cmd.append("--prerelease")
            print("  (Marking as pre-release since version contains alpha/beta/rc/dev)")

        # Create release
        print(f"\nCreating GitHub release for {tag_name}...")
        result = subprocess.run(release_cmd, cwd=self.project_root, capture_output=True, text=True)

        if result.returncode == 0:
            print(f"Created GitHub release: {tag_name}")
            return True
        else:
            print(f"Failed to create GitHub release: {result.stderr}")
            return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Bump OSA version and create Git tag/release",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "bump_type", nargs="?", choices=["major", "minor", "patch"], help="Type of version bump"
    )

    parser.add_argument(
        "--prerelease",
        choices=["alpha", "beta", "rc", "dev", "stable"],
        help="Set pre-release label (dev for develop branch, omit for stable release)",
    )

    parser.add_argument("--current", action="store_true", help="Show current version and exit")

    parser.add_argument(
        "--no-git", action="store_true", help="Skip Git operations (commit, tag, release)"
    )

    parser.add_argument(
        "--push",
        action="store_true",
        help="Automatically push commit and tag to remote (requires bypass permissions for protected branches)",
    )

    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: skip interactive prompts, auto-push, suitable for GitHub Actions",
    )

    args = parser.parse_args()

    # Find project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    bumper = VersionBumper(project_root)

    # Show current version
    if args.current:
        major, minor, patch, prerelease, prerelease_num = bumper.get_current_version()
        version = bumper.format_version(major, minor, patch, prerelease, prerelease_num)
        print(f"Current version: {version}")
        if prerelease != "stable":
            print(f"  Components: {major}.{minor}.{patch} ({prerelease} #{prerelease_num})")
        return 0

    # Validate arguments - need either bump_type or prerelease
    if not args.bump_type and not args.prerelease:
        parser.print_help()
        return 1

    # Perform version bump
    try:
        old_version, new_version = bumper.bump_version(args.bump_type, args.prerelease)
        print(f"\nVersion bumped: {old_version} -> {new_version}\n")

        # CI mode: skip prompts, auto-push
        skip_prompts = args.ci
        auto_push = args.push or args.ci

        if not args.no_git and bumper.git_commit_and_tag(new_version, skip_prompts=skip_prompts):
            # Git operations succeeded

            # Push to remote if requested
            if auto_push:
                push_success = bumper.push_to_remote(new_version, skip_prompts=skip_prompts)
                if push_success and not skip_prompts:
                    # Optionally create GitHub release (not in CI mode)
                    response = input("\nCreate GitHub release now? (y/N): ")
                    if response.lower() == "y":
                        bumper.create_github_release(new_version)
                elif push_success and skip_prompts:
                    # In CI mode, auto-create release
                    print("\n[CI Mode] Creating GitHub release...")
                    bumper.create_github_release(new_version)
            else:
                # Manual push workflow
                print("\nNext steps:")
                print(f"  1. Review the changes: git show v{new_version}")
                print("  2. Push to remote: git push origin <your-branch>")
                print(f"  3. Push tag: git push origin v{new_version}")
                print("  4. Create PR and merge to develop/main")
                print("  5. After merge, create GitHub release if needed")

                if not skip_prompts:
                    response = input("\nCreate GitHub release now? (y/N): ")
                    if response.lower() == "y":
                        bumper.create_github_release(new_version)

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
