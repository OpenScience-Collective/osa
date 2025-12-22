# Open Science Assistant (OSA) - Architecture

This document describes the system architecture of OSA with diagrams suitable for documentation and publications.

---

## 1. System Overview

```mermaid
flowchart TB
    subgraph Users["User Interfaces"]
        CLI[("CLI<br/>(Typer)")]
        API[("REST API<br/>(FastAPI)")]
        Web[("Web Frontend<br/>(Future)")]
    end

    subgraph Core["Core Platform"]
        Router{{"Router Agent"}}
        State[("State Manager<br/>(LangGraph)")]
        Telemetry[("Telemetry<br/>Service")]
    end

    subgraph Assistants["Specialist Assistants"]
        HED["HED Assistant"]
        BIDS["BIDS Assistant"]
        EEGLAB["EEGLAB Assistant"]
        General["General Assistant"]
    end

    subgraph Tools["Tool System"]
        DocRetrieval["Document<br/>Retrieval"]
        Validation["Validation<br/>API Integrations"]
        Search["Knowledge<br/>Search"]
    end

    subgraph Knowledge["Knowledge Sources"]
        GitHub[("GitHub<br/>Issues/PRs")]
        OpenALEX[("OpenALEX<br/>Papers")]
        Discourse[("Discourse<br/>Forums")]
        MailingLists[("Mailing<br/>Lists")]
        Docs[("Documentation<br/>Sites")]
    end

    subgraph Observability["Observability"]
        LangFuse[("LangFuse<br/>Tracing")]
        Feedback[("Feedback<br/>System")]
        DB[("PostgreSQL<br/>Storage")]
    end

    CLI --> API
    Web --> API
    API --> Router
    Router --> State
    State --> Telemetry

    Router --> HED
    Router --> BIDS
    Router --> EEGLAB
    Router --> General

    HED --> Tools
    BIDS --> Tools
    EEGLAB --> Tools
    General --> Tools

    Tools --> Knowledge

    DocRetrieval --> Docs
    Search --> GitHub
    Search --> OpenALEX
    Search --> Discourse
    Search --> MailingLists

    Telemetry --> LangFuse
    Telemetry --> DB
    API --> Feedback
    Feedback --> DB
```

---

## 2. Request Flow

```mermaid
sequenceDiagram
    participant U as User
    participant API as FastAPI
    participant R as Router Agent
    participant A as Specialist Assistant
    participant T as Tools
    participant K as Knowledge Sources
    participant O as Observability

    U->>API: Query
    API->>O: Start Session
    API->>R: Route Query

    R->>R: Analyze Intent
    R->>A: Delegate to Specialist

    loop Tool Calls
        A->>T: Request Information
        T->>K: Fetch Data
        K-->>T: Return Data
        T-->>A: Tool Result
    end

    A->>A: Generate Response
    A-->>R: Response
    R-->>API: Final Response

    API->>O: Log Session
    API-->>U: Stream Response
```

---

## 3. Assistant Routing (Supervisor Pattern)

```mermaid
flowchart TD
    subgraph Input
        Query["User Query"]
    end

    subgraph Router["Router Agent"]
        Analyze["Analyze Query Intent"]
        Rules{"Rule-Based<br/>Match?"}
        LLM["LLM Classification"]
    end

    subgraph Specialists
        HED["HED Assistant<br/><i>Annotations, Events</i>"]
        BIDS["BIDS Assistant<br/><i>Datasets, Organization</i>"]
        EEGLAB["EEGLAB Assistant<br/><i>EEG Analysis, MATLAB</i>"]
        General["General Assistant<br/><i>Cross-cutting, Meta</i>"]
    end

    subgraph Context
        State["Shared State"]
        History["Conversation History"]
    end

    Query --> Analyze
    Analyze --> Rules

    Rules -->|"'HED', 'annotation'"| HED
    Rules -->|"'BIDS', 'dataset'"| BIDS
    Rules -->|"'EEGLAB', 'EEG'"| EEGLAB
    Rules -->|"No match"| LLM

    LLM --> HED
    LLM --> BIDS
    LLM --> EEGLAB
    LLM --> General

    HED <--> State
    BIDS <--> State
    EEGLAB <--> State
    General <--> State

    State <--> History
```

---

## 4. Tool Execution Pipeline

```mermaid
flowchart LR
    subgraph Assistant
        A["Assistant Agent"]
        Decision{"Tool<br/>Needed?"}
    end

    subgraph ToolSystem["Tool System"]
        Registry["Tool Registry"]
        Permission{"Requires<br/>Permission?"}
        Execute["Execute Tool"]
    end

    subgraph Sources["Data Sources"]
        direction TB
        S1["GitHub API"]
        S2["OpenALEX API"]
        S3["Discourse API"]
        S4["Doc Fetcher"]
        S5["Validators"]
    end

    subgraph Output
        Result["Tool Result"]
        Cache["Result Cache"]
    end

    A --> Decision
    Decision -->|"Yes"| Registry
    Decision -->|"No"| A

    Registry --> Permission
    Permission -->|"Yes"| User["User Approval"]
    Permission -->|"No"| Execute
    User -->|"Approved"| Execute
    User -->|"Denied"| A

    Execute --> S1
    Execute --> S2
    Execute --> S3
    Execute --> S4
    Execute --> S5

    S1 --> Result
    S2 --> Result
    S3 --> Result
    S4 --> Result
    S5 --> Result

    Result --> Cache
    Result --> A
```

---

## 5. Knowledge Source Integration

```mermaid
flowchart TB
    subgraph Query["Query Processing"]
        Q["Search Query"]
        Expand["Query Expansion"]
    end

    subgraph Sources["Knowledge Sources"]
        subgraph GitHub["GitHub"]
            GH_Issues["Issues"]
            GH_PRs["Pull Requests"]
            GH_Disc["Discussions"]
        end

        subgraph Academic["Academic"]
            OA["OpenALEX<br/>Papers"]
            Citations["Citations"]
        end

        subgraph Community["Community"]
            NS["Neurostars<br/>Forum"]
            MNE["MNE Forum"]
            ML["Mailing Lists<br/>(EEGLAB, etc.)"]
        end

        subgraph Docs["Documentation"]
            HED_Docs["HED Specs"]
            BIDS_Docs["BIDS Specs"]
            EEG_Docs["EEGLAB Docs"]
        end
    end

    subgraph Processing["Result Processing"]
        Merge["Merge Results"]
        Rank["Relevance Ranking"]
        Dedupe["Deduplication"]
    end

    subgraph Output["Output"]
        Context["Retrieved Context"]
    end

    Q --> Expand
    Expand --> GitHub
    Expand --> Academic
    Expand --> Community
    Expand --> Docs

    GH_Issues --> Merge
    GH_PRs --> Merge
    GH_Disc --> Merge
    OA --> Merge
    Citations --> Merge
    NS --> Merge
    MNE --> Merge
    ML --> Merge
    HED_Docs --> Merge
    BIDS_Docs --> Merge
    EEG_Docs --> Merge

    Merge --> Rank
    Rank --> Dedupe
    Dedupe --> Context
```

---

## 6. Feedback Triage System

```mermaid
flowchart TD
    subgraph Input["Feedback Input"]
        User["User Feedback"]
        Session["Session Context"]
    end

    subgraph Triage["Triage Agent"]
        Classify["Classify Feedback"]
        Severity{"Severity?"}
        Similar["Find Similar Issues"]
        Match{"Match<br/>Found?"}
    end

    subgraph Actions["Actions"]
        Archive["Archive for<br/>Manual Review"]
        Comment["Add Comment to<br/>Existing Issue"]
        Create["Create New<br/>GitHub Issue"]
    end

    subgraph Storage["Storage"]
        JSONL["Feedback JSONL"]
        DB["Analytics DB"]
        GH["GitHub Issues"]
    end

    User --> Classify
    Session --> Classify

    Classify --> Severity

    Severity -->|"Low"| Archive
    Severity -->|"Medium/High"| Similar

    Similar --> Match

    Match -->|"Yes (>80%)"| Comment
    Match -->|"No"| Severity2{"High Severity<br/>Bug/Feature?"}

    Severity2 -->|"Yes"| Create
    Severity2 -->|"No"| Archive

    Archive --> JSONL
    Archive --> DB
    Comment --> GH
    Create --> GH

    style Archive fill:#f9f,stroke:#333
    style Comment fill:#ff9,stroke:#333
    style Create fill:#9f9,stroke:#333
```

---

## 7. Telemetry and Session Recording

```mermaid
flowchart LR
    subgraph Session["Chat Session"]
        Start["Session Start"]
        Msg["Messages"]
        Tools["Tool Calls"]
        End["Session End"]
    end

    subgraph Capture["Telemetry Capture"]
        Meta["Metadata<br/><i>user, model, assistant</i>"]
        Content["Content<br/><i>messages, responses</i>"]
        Usage["Usage<br/><i>tokens, cost</i>"]
    end

    subgraph Storage["Storage Layer"]
        PG[("PostgreSQL")]
        LF[("LangFuse")]
    end

    subgraph Analytics["Analytics"]
        Cost["Cost Tracking"]
        Quality["Quality Metrics"]
        Export["Training Data<br/>Export"]
    end

    Start --> Meta
    Msg --> Content
    Tools --> Content
    End --> Usage

    Meta --> PG
    Content --> PG
    Usage --> PG

    Meta --> LF
    Content --> LF
    Usage --> LF

    PG --> Cost
    PG --> Quality
    PG --> Export

    LF --> Cost
    LF --> Quality
```

---

## 8. Deployment Architecture

```mermaid
flowchart TB
    subgraph Client["Client Layer"]
        CLI["CLI Tool"]
        WebApp["Web Application"]
        ExtAPI["External API Clients"]
    end

    subgraph Edge["Edge/CDN"]
        LB["Load Balancer"]
    end

    subgraph API["API Layer"]
        FastAPI1["FastAPI Instance 1"]
        FastAPI2["FastAPI Instance 2"]
        FastAPIN["FastAPI Instance N"]
    end

    subgraph Workers["Background Workers"]
        Feedback["Feedback Processor"]
        Index["Knowledge Indexer"]
    end

    subgraph Data["Data Layer"]
        PG[("PostgreSQL<br/><i>State, Telemetry</i>")]
        Redis[("Redis<br/><i>Cache, Sessions</i>")]
        VectorDB[("Vector Store<br/><i>Embeddings</i>")]
    end

    subgraph External["External Services"]
        LLM["LLM Providers<br/><i>OpenRouter</i>"]
        LangFuse["LangFuse<br/><i>Observability</i>"]
        GitHub["GitHub API"]
    end

    CLI --> LB
    WebApp --> LB
    ExtAPI --> LB

    LB --> FastAPI1
    LB --> FastAPI2
    LB --> FastAPIN

    FastAPI1 --> PG
    FastAPI1 --> Redis
    FastAPI1 --> VectorDB
    FastAPI1 --> LLM
    FastAPI1 --> LangFuse

    FastAPI2 --> PG
    FastAPI2 --> Redis

    Feedback --> PG
    Feedback --> GitHub
    Index --> VectorDB
```

---

## Component Summary

| Component | Technology | Purpose |
|-----------|------------|---------|
| API Server | FastAPI | REST API, WebSocket streaming |
| CLI | Typer + Rich | Command-line interface |
| Orchestration | LangGraph | Multi-agent workflows, state management |
| LLM Framework | LangChain | Model abstraction, tool calling |
| Observability | LangFuse | Tracing, cost tracking, prompt management |
| Knowledge DB | SQLite + FTS5 | Issues, PRs, papers with full-text search |
| Session State | In-memory / SQLite | Single instance, simple persistence |
| Vector Store | FAISS/Qdrant | Semantic search (future) |

### External API Integrations

OSA integrates with existing validator and tool APIs rather than hosting validation engines locally. This approach:
- Reduces deployment complexity (no need to maintain validator dependencies)
- Ensures validation logic is always up-to-date
- Leverages existing, well-tested infrastructure

| Service | API Endpoint | Integration |
|---------|--------------|-------------|
| HED Validation | https://hedtools.org/hed | String, sidecar, spreadsheet, BIDS validation |
| BIDS Validator | https://bids-validator.github.io | Dataset structure validation |
| OpenALEX | https://api.openalex.org | Academic paper search |
| GitHub API | https://api.github.com | Issues, PRs, discussions |

The assistant tools call these APIs on behalf of users, parsing results and presenting them in context.

### Why SQLite with FTS5 for Knowledge Sources?

For single-instance lab deployment, SQLite with FTS5 is the optimal choice:

| Approach | Search Speed | Dependencies | Use Case |
|----------|-------------|--------------|----------|
| JSON files | O(n) linear | None | Tiny datasets (<1K) |
| MongoDB | O(log n) indexed | External server | Multi-instance, large scale |
| **SQLite + FTS5** | O(log n) indexed | None (stdlib) | **Single instance, 10K-1M records** |
| PostgreSQL | O(log n) indexed | External server | Multi-instance, complex queries |

**SQLite + FTS5 advantages:**
- No external server (single file per project)
- Full-text search with ranking (`bm25()`)
- 100-1000x faster than JSON linear scan
- Python stdlib (no extra dependencies)
- Easy backup (just copy the file)

---

## Notes for Publication-Quality Figures

For creating SVG figures suitable for scientific publications:

1. **Export Mermaid to SVG**: Use [mermaid.live](https://mermaid.live) or mermaid CLI
2. **Post-process in Inkscape/Illustrator**:
   - Adjust fonts to publication standards (Arial, Helvetica)
   - Ensure colorblind-friendly palette
   - Add figure labels (A, B, C, etc.)
   - Set appropriate stroke widths for print
3. **Recommended dimensions**:
   - Single column: 85mm width
   - Double column: 170mm width
   - Resolution: Vector (infinite) or 300 DPI minimum

The diagrams above can be combined into a single multi-panel figure showing:
- (A) Overall architecture
- (B) Request flow
- (C) Tool execution
- (D) Feedback system
