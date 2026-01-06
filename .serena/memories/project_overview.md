# OSA (Open Science Assistant) - Project Overview

## Purpose
A precise, reliable AI assistant platform for researchers working with open science tools. Built for accuracy over scale, serving small research communities from lab servers.

## Design Principles
- **Precision over features**: Researchers need accurate, citation-backed answers
- **Simple infrastructure**: Lab server deployment, no complex scaling
- **Extensible tools**: General tool system that communities can adapt for their needs
- **Domain expertise**: Deep knowledge of specific tools, not broad generalist

## Target Users
- Researchers learning HED annotations, BIDS formatting, or EEGLAB analysis
- Lab members needing quick, accurate answers from documentation
- Developers integrating these tools who need API/usage guidance

## Target Projects
- **HED**: Hierarchical Event Descriptors (annotation standard)
- **BIDS**: Brain Imaging Data Structure (data organization)
- **EEGLAB**: EEG analysis MATLAB toolbox

## Current Status (as of 2026-01-06)
- Phase 1 (Foundation): COMPLETE
- Phase 2 (HED Assistant): MOSTLY COMPLETE
- Phase 3 (Chat Endpoint & CLI): COMPLETE
- Phase 4 (Deployment): NOT STARTED
- Phase 5 (Knowledge Sources): NOT STARTED
- Phase 6 (Other Assistants): NOT STARTED

## Architecture
Simple, single-instance deployment:
- FastAPI backend with streaming chat
- LangGraph agents for conversation management
- In-memory state (no PostgreSQL/Redis needed initially)
- Direct document fetching (no vector DB needed)
- LangFuse for observability (optional)
- BYOK (Bring Your Own Key) support

## Key Documentation References
- `.context/plan.md`: Implementation roadmap
- `.context/research.md`: Technical notes, target project resources
- `.rules/`: Development standards
- `CLAUDE.md`: Quick start guide and design principles
