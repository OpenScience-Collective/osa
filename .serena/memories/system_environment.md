# System Environment Information

## Platform
- **OS**: macOS (Darwin 25.2.0)
- **Architecture**: Apple Silicon (assumed, based on brew path)
- **Current Date**: 2026-01-06

## Python Environment
- **Python Version**: 3.12 (project supports 3.11+)
- **Package Manager**: conda (miniconda3)
- **Conda Path**: `/opt/homebrew/Caskroom/miniconda/base`
- **Conda Init**: `source ~/miniconda3/etc/profile.d/conda.sh`
- **Project Environment**: `osa`

## Key Paths
- **Project Root**: `/Users/yahya/Documents/git/osa`
- **User Config**: `~/.config/osa/` (managed by platformdirs)
- **XCode**: `/Volumes/S1/Applications/Xcode.app`

## macOS-Specific Commands
Many standard Unix commands work, but some have macOS variations:

### File Operations
```bash
# Open file with default app
open file.txt

# Open in specific app
open -a "Visual Studio Code" .

# Reveal in Finder
open -R file.txt
```

### System Info
```bash
# System version
sw_vers

# Architecture
uname -m

# CPU info
sysctl -n machdep.cpu.brand_string
```

### Package Management
```bash
# Homebrew (installed)
brew list
brew install package-name
brew update
brew upgrade
```

## Git Configuration
- **Version Control**: Git (installed)
- **GitHub CLI**: `gh` (available)
- **Current Branch**: main (as of last check)

## Pre-commit Hooks
Installed and configured with:
- ruff (formatting and linting)
- Standard hooks (trailing whitespace, EOF fixer, etc.)

## Environment Variables
Key environment variables (stored in `.env`):
- `OPENROUTER_API_KEY`: For LLM API access (BYOK)
- `OPENROUTER_API_KEY_FOR_TESTING`: For running LLM tests
- Other API keys as needed (see `.env.example`)

## Port Usage
- **Production API Server**: 38528 (default for `osa serve`)
- **Development API Server**: 38529 (for dev/testing)
- Can be changed with `--port` flag or `PORT` environment variable
- Port allocation: HEDit prod=38427, HEDit dev=38428, OSA prod=38528, OSA dev=38529

## Known Paths for Reference Projects
- **HEDit** (deployment patterns): `/Users/yahya/Documents/git/annot-garden/hedit`
- **QP** (doc retrieval patterns): `/Users/yahya/Documents/git/HED/qp`
- **Rule templates**: `/Users/yahya/Documents/git/rule_templates`
