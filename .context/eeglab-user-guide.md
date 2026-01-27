# EEGLab Assistant User Guide

## Overview

The EEGLab Assistant helps EEG researchers with:
- Finding function documentation (MATLAB/Python)
- Troubleshooting common issues
- Learning from over 20 years of community discussions (since 2004)
- Accessing tutorials and guides
- Tracking development activity

## Available Tools

### Documentation & Codebase

**search_eeglab_docstrings**
- Search MATLAB/Python function documentation from EEGLAB codebase
- Example: "How do I use pop_loadset?"
- Returns: Function signature, parameters, usage documentation
- Useful for: Understanding function APIs, finding usage examples

**retrieve_eeglab_docs**
- Fetch tutorials and guides from eeglab.org
- Example: "Show me the ICA tutorial"
- Returns: Full tutorial content with examples
- Useful for: Learning workflows, understanding concepts

### Community Knowledge

**search_eeglab_faqs**
- Search mailing list Q&A (archives since 2004)
- Example: "Artifact removal best practices"
- Returns: Question, answer summary, category, quality score, thread link
- Useful for: Finding solutions to common problems, learning from past discussions

**search_eeglab_discussions**
- Search GitHub issues and PRs across EEGLAB repos (sccn/eeglab, sccn/ICLabel, etc.)
- Example: "Current issues with ICLabel"
- Returns: Issue/PR title, status, comments, links
- Useful for: Tracking bugs, finding feature requests, seeing current development

**list_eeglab_recent**
- List recent GitHub activity (PRs, issues, commits)
- Example: "What's new in EEGLAB development?"
- Returns: Recent PRs, commits, releases with dates
- Useful for: Staying up to date, seeing what's being worked on

### Research

**search_eeglab_papers**
- Search academic literature about EEGLAB and EEG analysis
- Example: "Papers about ICA for EEG"
- Returns: Title, authors, abstract, DOI, citation count
- Useful for: Literature review, understanding methods, citing correctly

## Example Questions

### Function Usage
- "How do I import BrainVision data?"
- "What are the parameters for pop_runica?"
- "Show me examples of pop_epoch"
- "How to use ICLabel for artifact classification?"
- "What does pop_resample do?"

### Troubleshooting
- "Why am I getting rank deficiency errors?"
- "How to fix channel location issues?"
- "ICA not converging, what to do?"
- "Error loading .set files"
- "Memory issues with large datasets"

### Best Practices
- "Recommended preprocessing pipeline"
- "How to choose ICA algorithm?"
- "Artifact removal strategies"
- "Should I use average reference or mastoid reference?"
- "When to use ASR vs manual artifact rejection?"

### Current Development
- "What's new in the latest release?"
- "Are there open issues with BIDS export?"
- "Recent updates to ICLabel?"
- "Status of MATLAB R2024b compatibility?"

### Research & Literature
- "Papers citing EEGLAB"
- "Research about ICLabel accuracy"
- "ICA methods comparison papers"

## Tips for Best Results

1. **Be specific:** "pop_loadset parameters" is better than "loading data"
2. **Check FAQs first:** Many common questions have been answered before
3. **Use function names:** When asking about specific functions, use exact names
4. **Verify sources:** Assistant provides links - always check original documentation
5. **Report issues:** If assistant gives wrong information, let developers know

## How the Knowledge Base Works

### Documentation (retrieve_eeglab_docs)
- Pulls from eeglab.org and sccn.github.io
- Covers tutorials, setup guides, and plugin documentation
- Updated when docs are updated

### Function Docs (search_eeglab_docstrings)
- Extracted from MATLAB/Python source code comments
- Includes function signatures, parameters, descriptions
- Synced from sccn/eeglab, sccn/ICLabel, and other repos

### FAQ Database (search_eeglab_faqs)
- Generated from EEGLAB mailing list archives (since 2004)
- Uses LLM to summarize threads into Q&A format
- Quality-scored to surface best answers
- Links back to original threads

### GitHub Activity (search_eeglab_discussions, list_eeglab_recent)
- Synced from GitHub repos: sccn/eeglab, sccn/ICLabel, sccn/clean_rawdata, etc.
- Includes issues, PRs, commits, releases
- Updated regularly to stay current

### Academic Papers (search_eeglab_papers)
- Tracked papers from Semantic Scholar
- Includes core EEGLAB papers and cited works
- Citation counts updated periodically

## Limitations

- **FAQ database requires sync:** If not populated, you'll see a message about running sync commands
- **Function docs require sync:** Same for docstring database
- **No real-time updates:** Knowledge base is synced periodically, not live
- **Quality varies:** FAQ quality depends on original mailing list discussions
- **No code execution:** Assistant can't run EEGLAB code, only provide guidance

## Admin: Sync Commands

For system administrators who need to populate/update the knowledge base:

```bash
# Sync GitHub repos (issues, PRs, commits)
osa sync github --community eeglab

# Sync academic papers
osa sync papers --community eeglab

# Sync function docstrings (Phase 2)
osa sync docstrings --community eeglab --repo /path/to/eeglab --repo /path/to/ICLabel

# Sync mailing list archive (Phase 3) - this takes a while!
osa sync mailman --community eeglab --start-year 2004

# Generate FAQ summaries from mailing list (Phase 3)
osa sync faq --community eeglab --quality 0.7
```

**Note:** Full sync takes several hours for 22 years of mailing list data. Run overnight or in background.

## Troubleshooting

### "Database not initialized" error
- Means the knowledge base hasn't been synced yet
- Contact your system administrator to run sync commands
- Or if you have access, run the sync commands above

### Tool not finding results
- Try different search terms (broader or more specific)
- Check if database is populated for that tool
- Some very specific queries may not have matches

### Response seems outdated
- Knowledge base is periodically synced, not real-time
- Check original sources (GitHub, eeglab.org) for latest info
- Ask administrator when last sync was run

## Feedback

If you encounter issues or have suggestions:
- Report bugs: https://github.com/hed-standard/osa/issues
- Feature requests: Same GitHub Issues
- Questions: Ask in the EEGLAB mailing list or GitHub Discussions
