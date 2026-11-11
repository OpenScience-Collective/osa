# NEMAR AI experience: epics and delivery loop

Status: draft for review, 2026-11-10.
Designs: the "NEMAR AI UX concepts" canvas (https://claude.ai/artifact/1jPSUnj2JLhwKYeFha8CiZ), boards A1 to D2.
Inputs: feedback sent to Yahya from the NEMAR team (five items), Yahya's notes of 2026-11-10,
a live session on nemar.org, and code reconnaissance of OSA (`develop` at 545bb1e), `nemarOrg/website` (`staging` at cb9e362), `nemarOrg/docs` and `nemarOrg/nemar-cli`.

## What we found

Live on nemar.org (production pins the OSA 0.8.16 widget):

- A returning visitor sees their old chat, and on another dataset page the old dataset's conversation stays, with the new dataset's suggestion chips added underneath and no notice.
- After a run, the code hides behind a grey "Code · 55 lines" label with no chevron; the run summary ("Ran Python: …") is itself a click target with no visual cue.
- After a reload, the figure and "Edit and run" are gone; stdout and code survive behind the same unmarked disclosures.
- A reply rendered a model-written image reference as "!Welch PSD" (a stray "!" plus a link).
- The power spectrum covered 35.8 of about 50 minutes, because the prompt's Welch recipe caps one `signal.welch` call below the 2 GiB view limit of 32-bit WebAssembly.

In the code:

- History is one record per community and origin, `localStorage['osa-chat-history-nemar']`, holding one conversation (`frontend/osa-chat-widget.js:4515-4605`).
- Figures are dropped on save on purpose (`osa-chat-widget.js:4593-4597`), but the workspace already writes them to IndexedDB as `<session>/results/run-NNN/figure-K.png` (`frontend/osa-workspace.js:143-148`); the chat never reads them back.
- The server keeps a session in memory for 24 hours of inactivity (`src/api/routers/community.py:614-615`), and the widget sends only the new message and the session id, so after a day the reader sees an old chat the model no longer has.
- The dataset id reaches the widget through `setDataset` but is never sent to the server; the model sees only the page URL, and only while "Share page URL" is on.
- The NEMAR prompt has no plan-first instruction; NEMAR runs on Claude Haiku 5.5 at high effort, 20 browser runs per reply, and LangGraph's default 25 steps per graph run.
- `memory_mb` (1536) is carried for reporting only; nothing enforces or exposes a memory budget.
- An `.ipynb` export already exists, but only inside Settings, as part of the workspace `.zip` (`frontend/osa-workspace.js:216-279`); the notebook bridge carries only status and theme (`notebook/osa-bridge.js:19-23`).
- The widget's public API is `setConfig`, `setColorScheme`, `getConfig`, `setDataset`, `getBrowserRuntimeStatus` and `init`; there is no `open`, `close` or `ask`, and no center or modal mode.
- nemar.org navigation is full page loads, so the widget initializes on every page; Discover is server-rendered and edge-cached for 30 minutes, so any per-query AI content must load client-side.
- The search API already returns `corrected_query`, `reranked` and `modality_boost`; the website reads none of them.
- NEMAR's OpenID Connect (OIDC) provider is unreleased and dark in production, accepts confidential server-side clients only, and its tokens are refused by ordinary API routes; NEMAR has no table that could hold conversations.
- docs.nemar.org has no page on the assistant, in-browser Python or the notebook, and the privacy policy does not list OSA or the model provider.

## Scope

Keep it maintainable: polish what exists before adding surfaces.

- **Now:** E1 to E5, and E8. They fix what users hit today, cut running cost, and need no new service.
- **Next:** E6, AI Mode on nemar.org (boards approved 2026-11-10).
- **Later, design only:** E7, account-backed history.
- **Out of scope:** sharing one Python kernel between chat and notebook (a stated non-goal), one notebook tab per snippet (cells replace it), a model rewriting notebooks, and server-side history before E7's decision.

## Epics

Each issue below states its goal and how to verify it.
"Widget tests" means a new or extended `bun frontend/test-widget-*.js` script, wired into the `frontend-tests` job of `.github/workflows/tests.yml`.
No mocks of business logic; recorded fixtures of real runs are fine as data.

### E1. Fresh, per-dataset conversations with history (OSA widget; boards A1, A4)

1. **Conversation store v3.**
   Replace the single conversation with `{version: 3, activeId, conversations: [{id, datasetId, title, sessionId, createdAt, updatedAt, messages}]}`, keeping the last 30 by `updatedAt`.
   Verify: a v2 record migrates into one conversation with nothing lost; the 31st conversation evicts the oldest; widget tests cover both.
2. **Fresh chat on return.**
   Start a new conversation when the page loads in a new browser session (no `sessionStorage` marker) or the active conversation is older than the server's 24-hour session lifetime; show "Pick up where you left off" in the empty state.
   Verify: widget tests for each trigger and for the resume card.
3. **A context change resets on the next message.**
   The case the NEMAR team reported happened within one visit: they closed the widget, opened another dataset, and the old context was still there.
   A conversation has a context: the dataset id from `setDataset` on a dataset page, or "search" on a page matching a search path the community configures (NEMAR: `/discover`); other pages keep the current context.
   When the page's context differs from the active conversation's and that conversation has user messages, the old chat stays visible with the A1 notice above the input: the next message starts a new chat, and the old one stays in History.
   "Continue the <old> chat instead" keeps the conversation, and the next request tells the agent that the reader moved from the old context to the new one.
   Also send the dataset id to the server in `page_context` and render it in the prompt's page context section.
   Verify: widget tests for reset on send, continue, dataset to search, and an empty conversation (silent switch); backend tests that the dataset id and the moved-context note reach the rendered prompt.
4. **History panel.**
   The A4 sheet: list by date with dataset id, step and figure counts and a figure thumbnail; open, continue, delete, clear all; "Saved in this browser only".
   Verify: widget tests for each action; the browser harness at phone width.
5. **Figures in history.**
   Render saved figures from the IndexedDB workspace for restored and opened conversations, and in the pop-out (closes #493).
   Verify: a harness run that executes code, reloads, and finds the figure.
6. **Continue a conversation the server forgot.**
   When a conversation resumes after its server session expired, send a compact text transcript to seed the new session.
   Verify: a backend test against a real graph that the seeded session answers with prior context.

### E2. Plan first, then run one step at a time (OSA backend, NEMAR prompt, widget; boards A2, A3, C3)

1. **`propose_plan` client tool.**
   A tool the agent calls with `{findings, steps: [{title, detail}], est_peak_mb}`; like `execute_code`, it interrupts the graph, the widget shows the plan, and `/chat/resume` returns the approved or edited plan and the run mode.
   Verify: backend tests drive a real graph through propose, edit and approve; the resume payload is validated.
2. **Plan-first prompt for NEMAR.**
   For analysis requests only: inspect cheaply first (dataset summary, events, channels), report what was found, propose 3 to 6 steps, then run exactly one `execute_code` per step, pausing or continuing per the run mode and staying within the memory budget (E5.1).
   Plain questions are answered directly, without a plan.
   Verify: E2.5.
3. **Plan card.**
   The A2 card: editable steps, add and remove, run mode, approve or ask for changes; approving counts as consent to run the plan's code.
   Verify: widget tests for edit, add, remove, approve, and the resume payload.
4. **Step progress.**
   Step headers on run cards, the progress strip, the paused bar ("Run step N", "Change step N"), and Stop plan.
   Verify: widget tests for pause, continue, stop and a failed step.
5. **Consistency check.**
   A script that sends five fixed analysis requests four times each to the NEMAR community and records the plans; marked as an LLM test, run by hand and in the pull request.
   Verify: in at least three of four repeats per request, the plans have the same step sequence (normalized step operations), and the report is attached to the pull request.

### E3. Code, results and errors that read clearly (OSA widget; board A3)

1. **One run card.**
   One component for consent, running, done and failed runs: figure visible, a prominent "Show code · N lines" button with `aria-expanded`, Copy code, Output, Download; the same card after a reload.
   Verify: widget tests for each state and for the restored state; harness screenshots in the pull request.
2. **Errors you can see.**
   Failed runs open by default with the exception type and message, the failing line marked when known, Full traceback and Copy error, and a retry status line; warnings from successful runs show as a collapsed "N warnings"; cut output says how much was cut.
   Verify: widget tests with recorded failing runs.
3. **One copy implementation** with the fallback and a visible "Copied".
   Verify: widget tests for both paths.
4. **Reply markdown.**
   Image references in replies point to the figure above instead of rendering "!"; highlighted code in replies (#598); host CSS no longer restyles replies (#597); links inside bold text render.
   Verify: `bun frontend/test-markdown.js` cases.
5. **Activity status.**
   Finish or close #494 against the A3 design.

### E4. From chat to notebook (OSA widget and notebook; board B1)

1. **Notebook builder.**
   From a conversation's successful runs, in order: a title cell, one setup cell (install, imports deduplicated, `read_index`), then a heading and code cell per step, named from the plan step or run description; failed runs are left out and listed in a closing note.
   Also emit a percent-format script (`# %%`, `# %% [markdown]`) that VS Code, Spyder and Jupytext open as cells.
   Verify: tests over recorded conversations; `jupytext --to notebook` round-trips the script.
2. **Import over the bridge.**
   A new origin-checked `import-notebook` message from widget to notebook frame writes `<community>/<dataset> · <title>.ipynb`, opens it, and offers Run all and Undo; the starter notebook stays as it is.
   Verify: `notebook/e2e-check.js` covers import, open and undo.
3. **Entry points.**
   A quiet "Continue in notebook" card after the first successful run, the history menu, the center-stage header, and "Download code" (`.ipynb` or `.py`).
   Verify: widget tests for visibility rules.
4. **Named notebook tabs.**
   Inside the Notebook surface (beside Chat), a strip of named notebooks: the dataset's playground, each notebook continued from a chat, and "+" for a blank one; rename, close and reopen, each tab backed by an `.ipynb` file in JupyterLite storage.
   Verify: `notebook/e2e-check.js` covers create, rename, close, reopen after reload, and a continued chat arriving as a new tab.

### E5. Runtime transparency: memory budget and documentation (OSA, NEMAR docs; board A5)

1. **Memory budget setting.**
   256 MB, 512 MB (default), 1 GB or 2 GB per step, stored in user settings and sent with each request so the prompt can size reads; after each run the widget reports the WebAssembly heap size against the budget and warns when a step exceeded it.
   Verify: widget tests for the setting and the report; a backend test that the budget reaches the prompt.
2. **Whole-recording spectra.**
   Replace the capped Welch recipe with one that averages Welch estimates over chunks.
   Verify: the on002718 sub-002 FaceRecognition spectrum covers the whole recording in the browser runtime test.
3. **"Analyze data in your browser" on docs.nemar.org** (`nemarOrg/docs`, Web App group).
   How it works (Pyodide in a Web Worker, NEMAR's lossy streaming copy, what runs where, what the model sees), performance (cold start about 8 to 10 s, warm about 1.5 s), limits, the notebook, privacy, and troubleshooting.
   Link it from the widget settings and the ask bar.
   Verify: the docs build passes and the page is reviewed by a NEMAR admin.
4. **Privacy policy.**
   Open an issue for NEMAR admins to list OSA and the model provider; policy text is theirs to change.

### E8. Quick answers on Haiku, analysis on Sonnet (OSA backend, widget; board A6)

NEMAR moved from Sonnet to Haiku for cost (#522), though analysis works much better on Sonnet.
Most turns are questions and search, which Haiku handles well; only analysis needs Sonnet.
Switching models inside one server session risks the preserved-thinking checks noted in #522, so the handover compacts the conversation into a fresh session instead.

1. **Route each turn.**
   Community keys `quick_model` (NEMAR: `haiku`) and `analysis_model` (NEMAR: `sonnet`).
   A turn goes to the analysis model when it asks to run code, plot or analyze (a keyword check first), or when the quick model calls a `start_analysis` tool; the quick model has no `execute_code`.
   Once a conversation is in analysis, it stays there until the context resets (E1.3) or a new chat starts.
   Verify: LLM-marked backend tests with real graphs: "find datasets with face stimuli" stays on the quick model, "plot the ERP for famous faces" goes to analysis.
2. **Compact and hand over.**
   The quick model writes a short summary (at most 150 words, keeping chosen dataset ids and the reader's goal); a new analysis session starts from that summary plus the reader's message; the stream carries handover events.
   Verify: backend tests that the summary keeps the dataset id and that the analysis session answers with that context.
3. **Handover in the widget.**
   The A6 card ("Summarized our conversation", "What the analysis model is told"), the session divider, and the mode label in the header.
   Verify: widget tests for the handover events and for a restored conversation.
4. **Cost per model.**
   Record the model on every turn in the existing usage metrics, and report cost per conversation, quick against analysis, over the first week on dev.
   Verify: the metrics show both models; the report is attached to the epic.

The search overview (E6.3) runs on the quick model.

### E6. AI Mode on nemar.org (OSA widget API, NEMAR website, OSA backend; boards C1 to C3, D1, D2)

Boards approved on 2026-11-10, after one revision: AI Mode is a pill, not a panel, and in search it is a toggle inside the field.
"AI Mode" is one name and one look across the site: a pill of the site's 44 px pill height, brand teal `#5bbad5` with `#04121f` text when it is an action, and a teal tint (`#e3f4f9`, text `#0b4f63`) when it is a toggle that is on.

1. **Host API and center stage.**
   `open()`, `close()`, `ask(text, {mode})`, `setMode('corner' | 'center')`, `on(event, fn)`; a center-stage dialog with focus trap where Escape or "Move to corner" docks the same conversation to the corner.
   The dialog opens on a start screen (C2: question field, suggestion pills, what it can do) and becomes the conversation with the plan rail (C3).
   Verify: widget tests and a harness check for focus, Escape and docking.
2. **AI Mode pill on the dataset page** (C1).
   An ActionBar pill right after Download, same shape and 44 px height (`.action`), in brand teal with a sparkle icon; it calls `open({mode: 'center'})`.
   A one-time hint under it introduces the feature; the pill is hidden when the widget did not load.
   Verify: website unit tests and a staging check.
3. **Search overview in OSA.**
   An endpoint that takes the query, filters and top results, answers on the quick model, cites only dataset ids from the given results, caches per normalized query for 24 hours, and counts against the community budget.
   Verify: backend tests for citation filtering and caching.
4. **Discover AI overview** (D2).
   The AI Mode toggle sits inside the 44 px search field (a 36 px pill, `aria-pressed`); when on, the field gets a teal-to-purple ring and the overview panel loads client-side above the results, with "Ask a follow-up" into the widget.
   The preference is remembered and carried as `ai=1` in the URL.
   Ship "Showing results for <corrected_query>" first; it needs no AI.
   Verify: website unit tests and a staging check.
5. **AI Mode in the home search** (D1).
   The same toggle inside the hero search field, at the full 44 px pill height.
   When on, the field gets the teal-to-purple ring of the hero's "Download" headline, the placeholder invites a plain-language description, and a "Try" row offers example searches; the magnifier and the "Search" button stay as they are, so the toggle's icon is the only AI mark (a sparkle drawn large enough to match its 15 px label). Submitting goes to `/discover?q=…&ai=1`.
   Off, the hero looks exactly as it does today.
   Verify: website unit tests, a staging check in light and dark themes, and phone width.
6. **Release and pin.**
   After an OSA release, bump the pinned widget source and Subresource Integrity (SRI) hash in `nemarOrg/website` `wrangler.toml`.

### E7. Account-backed history (design only)

An Architecture Decision Record (ADR), agreed with NEMAR admins, on where conversations live (an OSA store keyed by the NEMAR subject id, or a NEMAR table behind a new scope), how OSA signs in (as a confidential OIDC client through the OSA worker), retention, export and deletion.
No code until it is accepted.

## Delivery loop

Roles:

- **Project manager (PM):** a Sonnet agent.
  It reads this plan and the epic issues, picks ready issues (dependencies merged, design approved), and writes each brief with the issue's goal and verification.
  It confirms a pull request meets them before merging, then picks the next issue.
- **Implementers:** at most three Sonnet agents at once, one issue each, each in its own git worktree from `develop` (or the target repo's integration branch).
  Each follows that repository's `AGENTS.md`, puts new widget features in new modules where it can to limit conflicts in `osa-chat-widget.js`, rebases before opening the pull request, and reports what it ran.
- **Review:** `pr-review-toolkit` with Sonnet reviewers, at most two rounds per `.rules/code_review.md`; every finding is fixed, rejected with a reason, or filed.
- **Merge:** squash into `develop`; CI must be green.
  A release into `main` and the NEMAR pin bump need a maintainer other than the author, so the loop stops there and hands back.

Waves (at most three issues in flight):

1. E3.1 with E3.2, E1.1, and E5.3.
2. E1.2 with E1.3, E8.1 with E8.2, and E4.1.
3. E1.4 with E1.5, E2.1 with E2.2 (on the analysis model), and E4.2 with E4.4.
4. E2.3 with E2.4, E8.3 with E8.4, and E4.3.
5. E5.1 with E5.2, E2.5, E3.3 to E3.5, and E1.6.
6. E6 (boards approved): E6.4's corrected query, E6.1, E6.3, then E6.2, E6.4 and E6.5.

The loop ends when every Now issue is merged into `develop` and verified on develop-demo.osc.earth, or when an issue is blocked on a human decision, which the PM reports instead of guessing.
