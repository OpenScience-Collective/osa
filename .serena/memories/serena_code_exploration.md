# Using Serena for Efficient Code Exploration

## Overview
Serena provides semantic coding tools via Language Server Protocol (LSP) that allow symbolic-level code exploration without reading entire files. This is much more efficient than text-based searches or reading full files.

## Core Principle: Symbolic Over Full-File Reads
**ALWAYS prefer symbolic tools over reading entire files**
- ❌ Don't read entire source files unless absolutely necessary
- ✅ Use symbolic overview and search tools instead
- ✅ Only read symbol bodies when you need to understand or edit them

## Available Serena Tools

### 1. Directory Listing
```
mcp__serena__list_dir
```
- Fast directory scanning
- Can be recursive or non-recursive
- Use for understanding project structure
- Can skip ignored files

**When to use**: Initial exploration, finding modules

### 2. File Search
```
mcp__serena__find_file
```
- Find files by name pattern
- Faster than shell `find` for specific files
- Supports substring matching

**When to use**: Looking for specific files by name

### 3. Pattern Search
```
mcp__serena__search_for_pattern
```
- Fast, flexible pattern search across codebase
- Use when unsure about symbol names or locations
- Can restrict to specific files/directories with `relative_path`
- Alternative to grep for code patterns

**When to use**:
- Searching for text patterns
- Finding candidates before using symbolic tools
- Searching in non-code files

### 4. Symbol Overview
```
mcp__serena__get_symbols_overview
```
- Get structured overview of symbols in a file
- Shows classes, functions, methods without bodies
- Efficient way to understand file structure
- No need to read full file content

**When to use**:
- Understanding what's in a file
- Finding available classes/functions
- Before deciding which symbols to read in detail

### 5. Symbol Search
```
mcp__serena__find_symbol
```
- Search for symbols by name path pattern
- Can include or exclude bodies
- Control depth for nested symbols
- Most powerful tool for targeted exploration

**Parameters**:
- `name_path_pattern`: Pattern to match (e.g., "Foo", "Foo/__init__", "Foo/*")
- `include_body`: Get full implementation (only when needed)
- `depth`: How deep to traverse nested symbols

**When to use**:
- Finding specific classes/functions
- Reading specific methods
- Understanding symbol hierarchy

### 6. Find References
```
mcp__serena__find_referencing_symbols
```
- Find where a symbol is used
- Get context around references
- Understand symbol dependencies

**When to use**:
- Impact analysis before editing
- Understanding how code is used
- Finding all call sites

## Efficient Exploration Workflow

### Scenario 1: Understanding a New Module
```
1. mcp__serena__list_dir → See what files exist
2. mcp__serena__get_symbols_overview → See classes/functions
3. mcp__serena__find_symbol (include_body=False) → Understand structure
4. mcp__serena__find_symbol (include_body=True) → Read specific symbols only
```

### Scenario 2: Finding and Editing a Function
```
1. mcp__serena__search_for_pattern → Find candidates (if name unclear)
2. mcp__serena__find_symbol → Locate exact symbol
3. mcp__serena__find_symbol (include_body=True) → Read implementation
4. mcp__serena__find_referencing_symbols → Check usage
5. mcp__serena__replace_symbol_body → Edit if needed
```

### Scenario 3: Understanding a Class
```
1. mcp__serena__find_symbol (name_path="ClassName", depth=0) → Get class signature
2. mcp__serena__find_symbol (name_path="ClassName", depth=1) → List all methods
3. mcp__serena__find_symbol (name_path="ClassName/method_name", include_body=True) → Read specific methods
```

### Scenario 4: Impact Analysis
```
1. mcp__serena__find_symbol → Locate symbol to change
2. mcp__serena__find_referencing_symbols → Find all usages
3. Analyze contexts → Understand impact
4. Update references as needed
```

## Symbol Naming and Paths

### Name Path Format
Symbols are identified by `name_path` and `relative_path`:
- **name_path**: Hierarchical identifier (e.g., "MyClass/my_method")
- **relative_path**: File path relative to project root

### Examples
```python
# For Python class:
class Foo:
    def __init__(self): ...
    def bar(self): ...

# Name paths:
- "Foo" → Class definition
- "Foo/__init__" → Constructor
- "Foo/bar" → Method bar
- "Foo/*" → All methods in Foo
```

## Best Practices

### DO ✅
- Start with symbolic overview before reading details
- Use pattern search to find candidates
- Only read symbol bodies when necessary
- Use `depth` parameter to control detail level
- Restrict searches with `relative_path` when possible
- Check references before editing symbols

### DON'T ❌
- Read entire files for exploration
- Use full file reads when you only need specific functions
- Skip symbolic tools in favor of grep/text search
- Read bodies of all symbols when only structure is needed

## When to Use Each Approach

### Use Serena Symbolic Tools:
- Exploring Python/JS/TS source code
- Understanding code structure
- Finding specific classes/functions/methods
- Impact analysis for refactoring
- Targeted reading of implementation details

### Use Pattern Search:
- Searching across code and non-code files
- Finding text patterns
- When exact symbol names are unknown
- Searching configuration files

### Use Standard File Read:
- Reading configuration files (YAML, JSON, TOML)
- Reading documentation
- Reading test data files
- When you already read overview and need full context

## Memory Usage
Serena's symbolic tools are extremely memory-efficient:
- Overview queries: Small output (just signatures)
- Symbol queries: Only requested symbols
- Pattern search: Only matching contexts
- Full file reads: Large output (avoid when possible)

## Example: Efficient vs Inefficient

### ❌ Inefficient
```
1. Read src/agents/base.py (full file)
2. Read src/agents/hed.py (full file)
3. Read src/agents/state.py (full file)
→ Loaded 1000s of lines to find one function
```

### ✅ Efficient
```
1. get_symbols_overview(src/agents/) → See all available classes
2. find_symbol("ToolAgent", depth=1) → See ToolAgent methods
3. find_symbol("ToolAgent/run", include_body=True) → Read just the run method
→ Loaded only what's needed
```

## Integration with Development Workflow

When exploring code for a task:
1. **Plan**: Identify what you need to understand
2. **Navigate**: Use list_dir and get_symbols_overview
3. **Locate**: Use find_symbol with patterns
4. **Read**: Include bodies only for symbols you'll edit/understand
5. **Analyze**: Use find_referencing_symbols for impact
6. **Edit**: Use symbolic editing tools when appropriate

This approach minimizes token usage and maximizes efficiency.
