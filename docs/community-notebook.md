# Adding a starter notebook to the notebook site

A separate site, `notebook.osc.earth/osa` (`develop-notebook.osc.earth/osa` on `develop`), lets a reader open a real JupyterLite notebook, pre-filled for one dataset, in a new browser tab.
The `/osa` path follows OSC's own naming rule: a subdomain is a plane serving several projects, and the project itself is the path (`api.osc.earth/osa`, `widget.osc.earth/osa`, ...), so this site does not own its subdomain's root either.
This is a different surface from `docs/community-browser-runtime.md`'s in-widget Pyodide runtime: that one runs model-written code inside the chat, sealed behind an egress allowlist;
this one is a full notebook, on its own origin, running the reader's OWN code, with no chat and no model in the loop at all (issue #453, `docs/adr/0011-the-notebook-site.md`).

## What the site is

A widget's own button (built separately, not part of this config surface) opens `https://notebook.osc.earth/osa/open.html?community=<id>&dataset=<dataset_id>` in a new tab.
That page validates the link, drops a filled-in starter notebook into JupyterLite's own browser storage, and redirects into it.
Nothing here talks to this API server: once the site is built and deployed, opening a notebook is a static, client-side operation.

## Adding a starter

A community opts in with a top-level `notebook:` block in its own `config.yaml`:

```yaml
notebook:
  starter: notebook/starter.ipynb
  dataset_pattern: "^(nm|ds|on)[0-9]{6}$"
```

`NotebookConfig` (`src/core/config/community.py`) defines the shape:

- `starter`: path to an `.ipynb` template, relative to the community's own folder.
  Must be a plain relative path (no `..`, no absolute path) ending in `.ipynb`, checked at config load.
  Its CONTENT -- valid JSON, nbformat 4, and the token below appearing somewhere -- is checked separately, wherever the config is loaded from a real checkout (`src.core.config.notebook_lock.validate_notebook_starter`; every community with a `notebook:` block is checked this way in `tests/test_assistants/test_shipped_runtimes.py`, and again by the site build itself).
- `dataset_pattern`: a regular expression a dataset id must match for this starter to open.
  Must be anchored (`^...$`) and must compile, checked at config load.
  `notebook/open.js` uses this pattern, verbatim, as the entire gate between an arbitrary query string and writing into a reader's browser storage, so an unanchored pattern would let a substring match through.

### The token

A starter's cells carry the literal placeholder `{{dataset_id}}` (braces, not a bare `{dataset}`: a bare name is a common substring inside an f-string or a dict literal a starter cell might already contain, and would be a false match).
It can appear in a markdown cell, a code cell, or both, and in as many cells as needed;
`open.js` fills in every occurrence, in every cell, client-side, at the moment a reader opens the link -- never at build time and never on this server.
A starter that never uses the token fails validation: nothing would ever be filled in for a reader, which is exactly the mistake this check exists to catch before it ships.

### The Pyodide-version rule

**A community with a `notebook:` block must pin `runtime.python.pyodide_version` to the notebook site's own Pyodide** (`src.core.config.notebook_lock.NOTEBOOK_SITE_PYODIDE_VERSION`, `0.29.5` today), checked at config load (`CommunityConfig.validate_notebook_needs_matching_pyodide`) and again, defensively, by the site build itself.
One site loads one Pyodide: a starter built against a different pin would load into an interpreter nothing tested it against, and the failure would surface only in a reader's browser, not anywhere a maintainer would see it first.
This is the SAME `runtime.python.pyodide_version` the in-widget browser runtime already uses (`docs/community-browser-runtime.md`, "The Pyodide pin"), so a community that already offers `execute_code` in its widget needs no separate pin to also ship a notebook starter.

### Wheels a notebook needs

If a starter's code needs a package the Pyodide distribution does not ship, it is served the same way the widget's own runtime serves one: through `runtime.python.lockfile`, the community's own lock overlay (`docs/community-browser-runtime.md`, "The lock overlay").
The site build merges every notebook-enabled community's overlay into ONE Pyodide lock for the whole site (`src.core.config.notebook_lock.merge_site_lock`), following the exact rule the widget's own `mergeLock` already enforces: an overlay entry may only ADD a package, never replace one the Pyodide distribution itself ships.
Extended here across communities: if two communities both name the same package, their entries must be byte-identical, or the site build fails rather than silently keeping one and dropping the other.
A community's wheel is served from `wheels/<community_id>/<file_name>` on the notebook site, a different path from the API's own `/{community}/runtime/{file_name}` route, but sourced from the exact same committed wheel and verified against the exact same recorded sha256.

## What the reader's browser keeps

Once a reader opens a link, the filled-in notebook lives in JupyterLite's own storage (IndexedDB, via `localforage`), on the notebook site's own origin -- NOT the widget's own workspace storage, and not this server.
Opening the same dataset again never overwrites it: `open.js` checks for an existing notebook at that path first, and if one is already there, it is left alone and the reader is taken straight to it, edits intact.
Deleting it, or starting over, is a plain file operation inside JupyterLite's own file browser; nothing here ever does it for the reader.

## What is not supported yet

**The chat widget's own workspace does not hand off to the notebook.** A reader who ran code in the widget and wants to keep exploring it here downloads their workspace `.zip` from the widget's Settings panel and uploads the files into JupyterLite by hand;
nothing bridges the two origins' storage automatically (`docs/adr/0011-the-notebook-site.md`, "The workspace hand-off"; this was the fourth prerequisite `docs/adr/0010-the-notebook-surface.md` named, and it stays open).

**No pull-request previews.** Unlike the demo widget's own `deploy-pages.yml`, the notebook site deploys only from `develop` and `main` (`.github/workflows/deploy-notebook.yml`).
Review a starter change by building the site locally (`notebook/README.md`) before merging.

**PyPI fallback is on, deliberately**, unlike the sealed in-widget runtime: a notebook runs the reader's own edited code, not a model's, so an unplanned `%pip install` failing silently would defeat the point of a notebook (`docs/adr/0011-the-notebook-site.md` states this difference plainly).
