# HED Tools Analysis for OSA

Analysis of HED validation tools and documentation structure for implementing Phase 2 of OSA.

**Date**: 2026-01-06
**HED Repositories Location**: `~/Documents/git/HED/`

---

## Executive Summary

**Recommendation**: Use the **hedtools.org REST API** (hed-web) for HED validation instead of implementing local validation.

**Reasoning**:
1. **Free to use**: hedtools.org API is public and free
2. **No local dependencies**: Avoids bundling hed-python validator
3. **Always up-to-date**: Uses latest schemas and validation logic
4. **Proven and stable**: Powers www.hedtools.org/hed (production service)
5. **Comprehensive**: Supports strings, events, sidecars, spreadsheets, BIDS datasets

**For documentation**: Fetch **markdown** from GitHub repos, point users to **HTML** URLs on website.

---

## 1. HED Python Tools (hed-python)

### Repository
- **Location**: `~/Documents/git/HED/hed-python`
- **GitHub**: https://github.com/hed-standard/hed-python
- **Branch**: main (updated 2026-01-06)

### Validation Architecture

#### Core Validator
- **File**: `hed/validator/hed_validator.py`
- **Class**: `HedValidator`
- **Purpose**: Top-level validation of HED strings

```python
class HedValidator:
    def __init__(self, hed_schema, def_dicts=None, definitions_allowed=False):
        """
        Parameters:
            hed_schema: HedSchema or HedSchemaGroup
            def_dicts: Definition dictionaries
            definitions_allowed: Flag definitions as errors if False
        """

    def validate(self, hed_string, allow_placeholders, error_handler=None) -> list[dict]:
        """Validate HED string and return issues"""
```

#### Specialized Validators
- `sidecar_validator.py` - JSON sidecar validation
- `spreadsheet_validator.py` - TSV/XLSX validation
- `onset_validator.py` - Onset column validation
- `def_validator.py` - Definition validation

#### No Built-in API Client
hed-python does **not** include HTTP client code for calling hedtools.org. It provides local validation logic only.

---

## 2. HED Web Service (hed-web)

### Repository
- **Location**: `~/Documents/git/HED/hed-web`
- **GitHub**: https://github.com/hed-standard/hed-web
- **Deployment**: https://hedtools.org/hed
- **Tech**: Flask + Docker

### REST API Endpoints

Base URL: `https://hedtools.org/hed`

#### 1. Strings Validation API (`/services_submit`)

**Purpose**: Validate HED strings via JSON API

**Request**:
```json
POST /services_submit
Content-Type: application/json

{
  "service": "strings",
  "command": "validate",
  "command_target": "strings",
  "schema_version": "8.4.0",
  "string_list": [
    "Sensory-event, (Red, Blue)",
    "Onset, Duration/5 ms"
  ],
  "check_for_warnings": false
}
```

**Response** (Success):
```json
{
  "service": "strings",
  "results": {
    "command": "validate",
    "command_target": "strings",
    "data": "",
    "schema_version": "8.4.0",
    "msg_category": "success",
    "msg": "Strings validated successfully...",
    "software_version": "..."
  },
  "error_type": "",
  "error_msg": ""
}
```

**Response** (Validation Errors):
```json
{
  "service": "strings",
  "results": {
    "command": "validate",
    "command_target": "strings",
    "data": "Errors for HED string 0:\nERROR: ...\n",
    "schema_version": "8.4.0",
    "msg_category": "warning",
    "msg": "Strings had validation issues"
  }
}
```

#### 2. Other Commands

**Strings**:
- `validate` - Validate HED strings
- `convert_to_short` - Convert to short form
- `convert_to_long` - Convert to long form
- `search` - Search for patterns

**Sidecars**:
- `validate` - Validate JSON sidecar
- `convert` - Convert sidecar format
- `extract` - Extract spreadsheet from sidecar
- `merge` - Merge multiple sidecars

**Events** (TSV files):
- `validate` - Validate events file
- `assemble` - Assemble events with sidecar
- `generate_sidecar` - Generate sidecar from events

**Spreadsheets**:
- `validate` - Validate spreadsheet
- `convert` - Convert spreadsheet format

**Schemas**:
- `validate` - Validate schema file
- `convert` - Convert schema format

#### 3. Schema Versions API (`/schema_versions_results`)

**Purpose**: Get list of available HED schema versions

**Request**:
```
GET /schema_versions_results?include_prereleases=false
```

**Response**:
```json
{
  "schema_version_list": [
    "8.4.0",
    "8.3.0",
    "8.2.0",
    ...
  ]
}
```

### API Implementation Files

- `hedweb/routes.py` - Flask route definitions
- `hedweb/process_service.py` - JSON service handler
- `hedweb/string_operations.py` - String validation logic
- `hedweb/sidecar_operations.py` - Sidecar operations
- `hedweb/event_operations.py` - Event file operations

---

## 3. HED JavaScript Tools (hed-javascript)

### Repository
- **Location**: `~/Documents/git/HED/hed-javascript`
- **GitHub**: https://github.com/hed-standard/hed-javascript
- **Branch**: main (updated 2026-01-06)

### Purpose
- **Client-side** (browser) HED validation
- Used by BIDS validator 2.0
- Online validator: www.hedtags.org/hed-javascript

### Why NOT Use for OSA
1. **Client-side focused**: Designed for browser environments
2. **BIDS-centric**: Optimized for BIDS dataset validation
3. **JavaScript dependency**: Would require Node.js for server-side use
4. **Less comprehensive**: Fewer features than Python tools

**Conclusion**: hed-javascript is not suitable for OSA's server-side needs.

---

## 4. HED Documentation Structure

### Source Repositories

#### hed-resources
- **Location**: `~/Documents/git/HED/hed-resources/docs/source/`
- **Format**: Markdown (.md) + some HTML
- **Content**: User guides, tutorials, how-tos
- **Examples**:
  - `IntroductionToHed.md`
  - `HowCanYouUseHed.md`
  - `HedAnnotationQuickstart.md`
  - `HedPythonTools.html`
  - `HedOnlineTools.html`

#### hed-specification
- **Location**: `~/Documents/git/HED/hed-specification/docs/source/`
- **Format**: Markdown (.md)
- **Content**: Technical specification
- **Examples**:
  - `01_Introduction.md`
  - `02_Terminology.md`
  - `03_HED_formats.md`
  - `04_Basic_annotation.md`
  - `05_Advanced_annotation.md`

### Published Documentation

#### ReadTheDocs
- **hed-resources**: https://hed-resources.readthedocs.io/
- **hed-specification**: https://hed-specification.readthedocs.io/
- **Format**: HTML (built from markdown via Sphinx)

#### GitHub Pages
- **Website**: https://www.hedtags.org
- **Repo**: `~/Documents/git/HED/hed-standard.github.io`
- **Format**: HTML (Jekyll-based)

### Documentation Pipeline

```
Markdown (.md)                   HTML (Sphinx)                Web URLs
in GitHub repos      ────>      on ReadTheDocs    ────>     hedtags.org
                                                            readthedocs.io

Source of Truth                 Built Artifacts            User-Facing
```

### Parsing Strategy for OSA

**For Agent Context (Internal)**:
1. Fetch **raw markdown** from GitHub repos
2. Simpler parsing (plain text-like)
3. Better for LLM context window
4. Examples:
   - `https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/IntroductionToHed.md`
   - `https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/02_Terminology.md`

**For User Citations (External)**:
1. Reference **HTML URLs** on ReadTheDocs/hedtags.org
2. Better reading experience
3. Proper navigation, search, formatting
4. Examples:
   - `https://hed-resources.readthedocs.io/en/latest/IntroductionToHed.html`
   - `https://hed-specification.readthedocs.io/en/latest/02_Terminology.html`

---

## 5. Recommendations for OSA Implementation

### For HED Validation Tool

**Implement**: HTTP client wrapper for hedtools.org API

**Implementation Sketch**:

```python
# src/tools/hed_validation.py

from langchain.tools import tool
import httpx
from typing import List

@tool
def validate_hed_strings(
    hed_strings: List[str],
    schema_version: str = "8.4.0",
    check_warnings: bool = False
) -> dict:
    """Validate HED annotation strings using the hedtools.org API.

    **Primary Use**: Agent self-check before presenting examples to users.

    The agent should:
    1. Generate an example HED string based on documentation
    2. Call this tool to validate the example
    3. If invalid: Fix based on errors OR use known-good example from docs
    4. Only present validated examples to users

    This prevents the agent from confidently giving invalid examples that
    would mislead researchers.

    Args:
        hed_strings: List of HED strings to validate
        schema_version: HED schema version (default: 8.4.0)
        check_warnings: Include warnings in validation (default: False)

    Returns:
        dict with validation results and error messages for fixing
    """
    url = "https://hedtools.org/hed/services_submit"

    payload = {
        "service": "strings",
        "command": "validate",
        "command_target": "strings",
        "schema_version": schema_version,
        "string_list": hed_strings,
        "check_for_warnings": check_warnings
    }

    response = httpx.post(url, json=payload, timeout=30.0)
    response.raise_for_status()

    result = response.json()

    # Format for LLM
    if result["results"]["msg_category"] == "success":
        return {
            "valid": True,
            "message": "All HED strings are valid",
            "schema_version": result["results"]["schema_version"]
        }
    else:
        return {
            "valid": False,
            "errors": result["results"]["data"],
            "schema_version": result["results"]["schema_version"]
        }


@tool
def validate_hed_sidecar(
    sidecar_json: str,
    schema_version: str = "8.4.0"
) -> dict:
    """Validate a BIDS JSON sidecar with HED annotations.

    Args:
        sidecar_json: JSON sidecar content as string
        schema_version: HED schema version

    Returns:
        dict with validation results
    ```

**Benefits**:
- No local hed-python dependency
- Always uses latest validation logic
- Offloads compute to hedtools.org
- Simpler implementation

**Considerations**:
- Requires internet connectivity
- API availability dependency
- Potential rate limiting (unlikely for research use)

### For Documentation Retrieval

**Current**: `src/tools/hed.py` fetches from GitHub

**Enhancement Needed**: Markdown → HTML URL mapping

```python
# src/tools/hed.py

class DocPage:
    def __init__(self, name, github_url, html_url=None):
        self.name = name
        self.github_url = github_url  # For fetching markdown
        self.html_url = html_url       # For user citations

# Example:
DocPage(
    name="HED Terminology",
    github_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/02_Terminology.md",
    html_url="https://hed-specification.readthedocs.io/en/latest/02_Terminology.html"
)
```

**Implementation**:
1. Fetch markdown from `github_url` for agent context
2. Strip markdown formatting (headers, links, etc.) → clean text
3. When citing to user, provide `html_url` for better reading experience

---

## 6. Next Steps for Phase 2 Completion

### Critical Design Insight: Validation as Self-Check

**Purpose**: The validation tool is primarily for the **agent to validate its own examples** before presenting them to users.

**Problem**: HED is complex. It's very hard for an LLM to generate valid HED strings from scratch without expert knowledge.

**Solution**:
1. When the agent wants to give an example HED string
2. Generate the example using knowledge from docs
3. **Pass through validation API as self-check**
4. If invalid: fix based on error messages OR regenerate OR use a known-good example from docs
5. Only show validated examples to users

**Benefits**:
- Prevents agent from confidently giving invalid examples
- Agent learns from validation errors (feedback loop)
- Users always get correct examples
- Builds trust in the assistant

### 6.1 Implement Validation Tools
- [ ] `validate_hed_string` - Self-check tool for agent's examples
- [ ] `validate_hed_sidecar` - Validate BIDS JSON sidecar
- [ ] `get_hed_schema_versions` - List available schemas
- [ ] Error handling for API failures (with graceful degradation)
- [ ] **Agent workflow**: Generate example → Validate → Fix if needed → Present to user

### 6.2 Enhance Documentation Parsing
- [ ] Add markdown-to-text cleaning function
- [ ] Add HTML URL mapping to existing DocPages
- [ ] Update system prompt to cite HTML URLs when responding to users
- [ ] Test markdown parsing with special characters, code blocks, tables

### 6.3 Dynamic Tool Discovery
- [ ] Create tool registry system
- [ ] Auto-register tools from `src/tools/` directory
- [ ] Generate tool descriptions from docstrings
- [ ] Update agent to discover tools dynamically

---

## Appendix: HED Repository Map

```
~/Documents/git/HED/
├── hed-python/              # Python validator library
│   └── hed/validator/       # Local validation logic
├── hed-web/                 # Flask REST API (hedtools.org)
│   └── hedweb/              # API routes and operations
├── hed-javascript/          # Browser-based validator
├── hed-resources/           # User documentation
│   └── docs/source/         # Markdown source files
├── hed-specification/       # Technical spec
│   └── docs/source/         # Markdown source files
├── hed-schemas/             # Schema definitions (JSON/XML)
├── hed-standard.github.io/  # Website (hedtags.org)
└── hed-examples/            # Example datasets
```

---

## References

- HED Homepage: https://www.hedtags.org
- hedtools.org: https://hedtools.org/hed
- hed-python: https://github.com/hed-standard/hed-python
- hed-web: https://github.com/hed-standard/hed-web
- hed-specification: https://hed-specification.readthedocs.io/
- hed-resources: https://hed-resources.readthedocs.io/
