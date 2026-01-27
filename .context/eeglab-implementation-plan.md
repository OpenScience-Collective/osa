# EEGLab Community Implementation Plan

**Date:** 2026-01-26
**Status:** Planning

## Overview

Develop a comprehensive EEGLab community for OSA, providing researchers with access to EEGLab documentation, codebase knowledge, and 20+ years of community wisdom from the mailing list.

## Resources Inventory

### 1. Website & Documentation
- **Main Website:** [eeglab.org](https://eeglab.org) at https://github.com/sccn/sccn.github.io
- **Structure:**
  - 11 core tutorial modules (data import → scripting)
  - Plugin documentation (ICLabel, DIPFIT, LIMO, SIFT, NFT)
  - Workshops and training materials
  - FAQs and troubleshooting
  - Integration guides (FieldTrip, HPC, Python, Octave)
  - Citation and revision history

### 2. GitHub Repositories (Top 20 by Stars)
Based on activity, stars, and community engagement:

| Repo | Stars | Forks | Open Issues | Language | Priority |
|------|-------|-------|-------------|----------|----------|
| eeglab | 726 | 261 | 51 | MATLAB | **Critical** |
| labstreaminglayer | 706 | 181 | - | HTML | High |
| liblsl | 159 | 78 | - | C++ | High |
| ICLabel | 70 | 23 | - | MATLAB | High |
| clean_rawdata | 49 | 19 | - | MATLAB | Medium |
| roiconnect | 45 | 18 | - | HTML | Medium |
| PACTools | 42 | 10 | - | MATLAB | Medium |
| mobilab | 31 | 22 | - | MATLAB | Low |
| EEG-BIDS | 30 | 21 | - | MATLAB | Medium |
| eeglab_tutorial_scripts | 11 | 5 | - | MATLAB | Medium |

**Recommended for Phase 1:** eeglab, ICLabel, clean_rawdata, EEG-BIDS, labstreaminglayer, liblsl

### 3. Mailing List Archives
- **URL:** https://sccn.ucsd.edu/pipermail/eeglablist/
- **Coverage:** 2004-2026 (22 years)
- **Organization:** Thread-based, subject-grouped, by-author, chronological
- **Peak Volume:** 2012 (5MB compressed)
- **Format:** HTML + compressed text files

### 4. Key Papers
To track for citations and core knowledge:

1. **Main EEGLab Paper:** [10.1016/j.jneumeth.2003.10.009](https://doi.org/10.1016/j.jneumeth.2003.10.009)
   Delorme & Makeig (2004) - "EEGLAB: an open source toolbox for analysis of single-trial EEG dynamics"

2. **ICLabel:** [10.1016/j.neuroimage.2019.05.026](https://doi.org/10.1016/j.neuroimage.2019.05.026)
   Pion-Tonachini et al. (2019) - "ICLabel: An automated electroencephalographic independent component classifier"

3. **PREP Pipeline:** (Need to find DOI)

4. **Additional:** Search for:
   - "EEGLAB tutorial"
   - "EEGLAB plugin"
   - "ICA EEG analysis"
   - "EEG preprocessing"

## Implementation Phases

### Phase 1: Basic Community Setup (Week 1-2)

**Goal:** Get EEGLab community running with documentation and GitHub repos

1. **Create Community Config** (`src/assistants/eeglab/config.yaml`)
   - Basic metadata (id: eeglab, name, description)
   - System prompt tailored to EEG analysis workflows
   - Documentation sources from sccn.github.io
   - GitHub repos: eeglab, ICLabel, clean_rawdata, EEG-BIDS
   - Paper queries and DOIs

2. **Documentation Strategy**
   - **Preloaded:** 2-3 core concepts (similar to HED)
     - Installation/setup guide
     - Basic tutorial structure
     - Key concepts (ICA, artifact rejection, etc.)
   - **On-demand:** Specific tutorials, plugin docs

3. **Knowledge Tools**
   - Reuse existing knowledge tools (search discussions, list recent, search papers)
   - Same pattern as HED

4. **Testing**
   - Verify documentation retrieval
   - Test GitHub sync
   - Validate paper search

**Deliverables:**
- [ ] `src/assistants/eeglab/config.yaml`
- [ ] Basic system prompt
- [ ] Documentation mapping
- [ ] GitHub sync working
- [ ] Manual testing with common questions

### Phase 2: Docstring Extraction Tools (Week 3-4)

**Goal:** Extract and index MATLAB/Python docstrings from codebases

#### 2.1 MATLAB Docstring Extractor

**Purpose:** Parse MATLAB files and extract function/script documentation

**Implementation:**
- Create `src/tools/matlab_docstring_extractor.py`
- Strategy:
  ```python
  # Parse MATLAB file header comments
  # Format: Lines starting with % before function definition
  # Extract:
  #   - Function name
  #   - Purpose/description
  #   - Input parameters
  #   - Output parameters
  #   - Examples
  #   - See also references
  ```

**Challenges:**
- MATLAB syntax variations
- Mixed comment styles
- Large codebase traversal

**Solution:**
- Use regex patterns for MATLAB comment extraction
- Walk repository tree (recursive)
- Build searchable index
- Store in knowledge database

**Tool Interface:**
```python
from langchain_core.tools import BaseTool

def create_search_matlab_docs_tool(
    community_id: str,
    community_name: str,
    repos: list[str],
) -> BaseTool:
    """Search MATLAB function documentation from docstrings."""
    # Implementation
```

#### 2.2 Python Docstring Extractor

**Purpose:** Extract Python docstrings (for Python-based EEG tools)

**Implementation:**
- Create `src/tools/python_docstring_extractor.py`
- Use `ast` module to parse Python files
- Extract docstrings from:
  - Functions
  - Classes
  - Methods
  - Modules

**Advantages over MATLAB:**
- Python AST parsing is built-in
- Standardized docstring formats (NumPy, Google, etc.)

**Tool Interface:**
```python
def create_search_python_docs_tool(
    community_id: str,
    community_name: str,
    repos: list[str],
) -> BaseTool:
    """Search Python function documentation from docstrings."""
    # Implementation
```

#### 2.3 Integration with Sync System

**Add to CLI:**
```bash
# Sync docstrings from GitHub repos
osa sync docstrings --community eeglab --language matlab
osa sync docstrings --community eeglab --language python

# Or sync all
osa sync all --community eeglab
```

**Database Schema:**
```sql
CREATE TABLE IF NOT EXISTS docstrings (
    id INTEGER PRIMARY KEY,
    community_id TEXT NOT NULL,
    language TEXT NOT NULL,  -- 'matlab' or 'python'
    repo TEXT NOT NULL,
    file_path TEXT NOT NULL,
    symbol_name TEXT NOT NULL,  -- function/class name
    symbol_type TEXT NOT NULL,  -- 'function', 'class', 'method'
    docstring TEXT NOT NULL,
    parameters TEXT,  -- JSON array
    returns TEXT,     -- JSON object
    examples TEXT,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(community_id, repo, file_path, symbol_name)
);

CREATE INDEX idx_docstrings_search ON docstrings(community_id, symbol_name, docstring);
```

**Deliverables:**
- [ ] `src/tools/matlab_docstring_extractor.py`
- [ ] `src/tools/python_docstring_extractor.py`
- [ ] Database schema updates
- [ ] CLI sync commands
- [ ] LangChain tool wrappers
- [ ] Tests for both extractors

### Phase 3: Mailing List FAQ Agent (Week 5-6)

**Goal:** Summarize 22 years of mailing list discussions into searchable FAQ

#### 3.1 Mailing List Scraper

**Implementation:**
- Create `src/tools/mailman_scraper.py`
- Scrape HTML archives from https://sccn.ucsd.edu/pipermail/eeglablist/
- Parse thread structure
- Extract:
  - Thread title
  - Original question
  - Responses
  - Thread metadata (date, participants)

**Challenges:**
- 22 years of data (~5MB peak)
- Rate limiting
- HTML parsing consistency

**Strategy:**
- Incremental scraping (year by year)
- Cache raw HTML locally
- Resume on failure

#### 3.2 FAQ Summarization Agent

**Purpose:** Use LLM to summarize Q&A threads into concise FAQ entries

**Implementation:**
```python
# src/tools/faq_summarizer.py

from src.core.services.llm import create_llm

async def summarize_thread(thread_data: MailingListThread) -> FAQEntry:
    """
    Use LLM to:
    1. Extract the core question
    2. Identify key responses
    3. Synthesize a concise answer
    4. Tag with categories
    """
    llm = create_llm(model="qwen/qwen3-235b-a22b-2507")

    prompt = f"""
    Summarize this EEGLab mailing list discussion into a FAQ entry.

    Thread: {thread_data.title}
    Question: {thread_data.original_post}
    Responses: {thread_data.responses}

    Extract:
    1. Core question (1-2 sentences)
    2. Best answer (2-3 paragraphs max)
    3. Related topics/tags
    4. Link to full thread
    """

    # Get summary
    # Validate quality
    # Store in database
```

**Cost Management:**
- Only summarize threads with >2 responses (indicates valuable discussion)
- Batch processing
- Use cheaper model for initial filtering, better model for final summary
- Cache summaries

**Database Schema:**
```sql
CREATE TABLE IF NOT EXISTS mailing_list_faqs (
    id INTEGER PRIMARY KEY,
    community_id TEXT NOT NULL,
    thread_url TEXT NOT NULL UNIQUE,
    thread_title TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    tags TEXT,  -- JSON array
    participants TEXT,  -- JSON array
    response_count INTEGER,
    thread_date TEXT,
    summarized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_faq_search ON mailing_list_faqs(community_id, question, answer, tags);
```

#### 3.3 FAQ Search Tool

**Tool Interface:**
```python
def create_search_faq_tool(
    community_id: str,
    community_name: str,
) -> BaseTool:
    """Search FAQ entries from mailing list history."""

    def search_faq_impl(query: str, limit: int = 5) -> str:
        # Full-text search on questions and answers
        # Return:
        #   - Question
        #   - Answer summary
        #   - Link to full thread

    return StructuredTool.from_function(...)
```

**Deliverables:**
- [ ] `src/tools/mailman_scraper.py`
- [ ] `src/tools/faq_summarizer.py`
- [ ] Database schema
- [ ] CLI command: `osa sync mailing-list --community eeglab`
- [ ] LangChain FAQ search tool
- [ ] Cost estimation and budget
- [ ] Tests

### Phase 4: Integration & Testing (Week 7)

**Goal:** Integrate all components and test end-to-end

1. **Update EEGLab Config**
   - Add docstring search tool
   - Add FAQ search tool
   - Update system prompt with tool usage instructions

2. **Frontend Widget**
   - Test widget embedding on hypothetical EEGLab site
   - Verify CORS settings
   - Test model selection

3. **Comprehensive Testing**
   - Unit tests for each tool
   - Integration tests for full workflow
   - Test with real EEG researcher questions
   - Performance testing (response times, DB queries)

4. **Documentation**
   - User guide for EEGLab assistant
   - Developer guide for maintaining tools
   - Sync workflow documentation

**Deliverables:**
- [ ] Complete `src/assistants/eeglab/config.yaml`
- [ ] All tools integrated
- [ ] Test suite passing
- [ ] Documentation complete
- [ ] Demo video/screenshots

### Phase 5: Deployment (Week 8)

**Goal:** Deploy to production servers

1. **Backend Deployment**
   ```bash
   # On hedtools server
   cd ~/osa
   git pull origin develop
   deploy/deploy.sh dev  # Test on dev first
   # Verify
   deploy/deploy.sh prod  # Deploy to production
   ```

2. **Knowledge Base Population**
   ```bash
   # Initialize database
   osa sync init --community eeglab

   # Sync GitHub repos
   osa sync github --community eeglab

   # Sync docstrings (may take hours)
   osa sync docstrings --community eeglab --language matlab
   osa sync docstrings --community eeglab --language python

   # Sync papers
   osa sync papers --community eeglab

   # Sync mailing list (may take days)
   osa sync mailing-list --community eeglab --batch-size 100
   ```

3. **Monitoring**
   - Check LangFuse for usage
   - Monitor response quality
   - Gather user feedback

**Deliverables:**
- [ ] Dev deployment tested
- [ ] Prod deployment complete
- [ ] Knowledge base populated
- [ ] Monitoring dashboard
- [ ] User feedback mechanism

## Technical Decisions

### Tool Architecture

**Reuse Existing:**
- Knowledge discovery tools (search discussions, list recent, search papers)
- Documentation fetcher
- Base tool infrastructure

**New Tools Needed:**
1. MATLAB docstring extractor
2. Python docstring extractor
3. FAQ search (mailing list)

### Database Strategy

**SQLite per community** (existing pattern):
- `knowledge/eeglab.db`
- Tables:
  - `github_items` (existing)
  - `papers` (existing)
  - `docstrings` (new)
  - `mailing_list_faqs` (new)

### Cost Considerations

**LLM Costs:**
1. **FAQ Summarization:** Most expensive
   - Estimate: 22 years × 365 days × 5 threads/day = ~40,000 threads
   - Filter to valuable threads (>2 responses): ~10,000 threads
   - Cost per summary: ~$0.01 (with qwen3-235b-a22b)
   - Total: ~$100-200

2. **Docstring Extraction:** Free
   - No LLM needed, pure parsing

3. **Regular Usage:** Standard rates
   - Documentation retrieval: free (HTTP fetch)
   - Tool calls: minimal cost

**Strategy:**
- Run FAQ summarization as one-time batch job
- Incremental updates monthly
- Budget: $200 for initial setup

### Performance Considerations

**Indexing:**
- Full-text search on docstrings
- FTS5 for mailing list FAQ
- Regular PRAGMA optimize

**Caching:**
- Documentation pages cached client-side
- Docstring index in memory for fast lookup

## Open Questions

1. **PREP Pipeline Paper:** Need to find the canonical DOI
2. **Additional Repos:** Should we include dipfit, cleanline, bva-io?
3. **Mailing List Rate Limiting:** What are the limits on pipermail archives?
4. **Custom EEGLab Tools:** Beyond general tools, do we need EEG-specific tools (e.g., channel location lookup, event code reference)?

## Success Metrics

1. **Coverage:**
   - [ ] All major EEGLab tutorials indexed
   - [ ] Top 6 repos synced
   - [ ] 10,000+ FAQ entries
   - [ ] 5,000+ docstrings indexed

2. **Quality:**
   - [ ] 90%+ accurate responses to common questions
   - [ ] Proper citations to original sources
   - [ ] Response time < 3 seconds

3. **Adoption:**
   - [ ] 100+ queries in first month
   - [ ] Positive feedback from EEG researchers
   - [ ] Integration with SCCN website (future)

## Dependencies

- [ ] Access to SCCN GitHub repos (public, no auth needed)
- [ ] Mailing list scraping allowed (check robots.txt)
- [ ] Budget approval for FAQ summarization (~$200)
- [ ] Test users for feedback (SCCN researchers)

## Timeline Summary

- **Week 1-2:** Basic setup (config, docs, GitHub sync)
- **Week 3-4:** Docstring extraction tools
- **Week 5-6:** Mailing list FAQ agent
- **Week 7:** Integration & testing
- **Week 8:** Deployment

**Total:** 8 weeks for complete implementation

## Next Steps

1. Create GitHub epic/issue with this plan
2. Get approval from SCCN team
3. Clone necessary repos to ~/Documents/git/sccn/
4. Start Phase 1 implementation
5. Set up weekly progress reviews
