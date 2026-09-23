# NEMAR's browser runtime

The wheels NEMAR's Python runtime adds to Pyodide 0.29.5,
and the lock overlay that pins them.
The server verifies each wheel against its entry when it loads the overlay,
sends the entries in `/config`,
and serves the wheels itself at `/nemar/runtime/{file_name}`;
Pyodide checks each sha256 again as it loads the wheel.
How that works is in `src/core/config/runtime_lock.py`.

| wheel | from | sha256 |
|---|---|---|
| `zarr-3.4.0-py3-none-any.whl` | PyPI, byte for byte (digest matches PyPI's published one) | `0a5e6c9b...eb72395` |
| `eegprep_lean-0.1.0.dev2-py3-none-any.whl` | `sccn/eegprep` at `b4aa18bd` (#416), `packages/eegprep-lean`, `SOURCE_DATE_EPOCH=1790128328 uv build --wheel` (reproducible; see below) | `39f33b99...7157582` |

zarr's own dependencies are not here:
Pyodide 0.29.5 ships every one of them, and `depends.toml` names them.
They are why the runtime needs 0.29.5,
since 0.28.3 has no `google-crc32c` and zarr imports it.

`sources.toml`, beside this file, records where each wheel above came from --
for `eegprep-lean`, the upstream repository, branch, path and commit; for `zarr`,
that it is PyPI's own wheel, byte for byte. A test fails if a wheel in `wheels/`
has no entry there, or an entry names no wheel.

## Changing a wheel

1. Build or download the new wheel under a **new version**.
   Wheels are served as immutable for a year,
   so new bytes under an old name would reach some readers and not others,
   and the generator refuses them.
2. Put it in `wheels/`, remove the old one, and update `depends.toml` if its needs changed.
3. Regenerate the overlay, and record where the wheel came from in `sources.toml`
   and the table above:

   ```bash
   uv run python scripts/build_runtime_lock.py src/assistants/nemar/runtime/nemar-pyodide-lock.json
   ```

4. Run `bun frontend/test-data-lane.js`, which loads these exact files in a real Pyodide and reads a store through them.

A test fails when the overlay is not what the wheels and `depends.toml` produce.

## Refreshing eegprep-lean

**Owner:** the OSA maintainer for NEMAR.

**Signal:** the weekly drift watcher's issue (label `eegprep-lean-drift`,
opened or updated by `.github/workflows/eegprep-lean-watch.yml`). It runs
`scripts/eegprep_lean_drift.py`, which compares `sources.toml`'s recorded commit
against the newest commit on `sccn/eegprep`'s `develop` branch touching
`packages/eegprep-lean` -- by commit, never by version string alone, since a
version can sit still across more than one upstream commit -- and reports
`current`, `behind`, or `unknown` (a fetch or parse failure; never reported as
current). Run it by hand at any time: `uv run python scripts/eegprep_lean_drift.py`.

**When it says `behind` and the version did not move,**
upstream changed the package without releasing it.
Read the change.
If it leaves the wheel as it is (tests, documentation, CI),
set `reviewed_through` in `sources.toml` to the commit the watcher named,
and the watcher reads that commit as current until upstream moves again or the version changes.
If it changes the wheel, ask upstream for a new version first:
a wheel's file name is its identity, served as immutable for a year,
so new bytes under the same name would reach some readers and not others,
and the overlay generator refuses them.

**Build:** from the commit `sources.toml` records, with the reproducible command
it names:

```bash
git archive <commit> packages/eegprep-lean | tar -x -C <dir>
cd <dir>/packages/eegprep-lean
SOURCE_DATE_EPOCH=<the commit's own timestamp> uv build --wheel
```

Without `SOURCE_DATE_EPOCH` set to the commit's own timestamp, setuptools stamps
the build time and two builds of the same commit differ; set to it, the wheel is
reproducible byte for byte (verified for the 0.1.0.dev2 re-vendor: a second build
from a fresh checkout produced the identical sha256).

**Proof, in order:**

1. `uv run python scripts/build_runtime_lock.py --check src/assistants/nemar/runtime/nemar-pyodide-lock.json`
   once the new wheel is in `wheels/` and the overlay regenerated (drop `--check`
   to write it in the first place).
2. `bun frontend/test-data-lane.js` -- loads the new wheel in a real Pyodide and
   reads a store through it, both production's current recipe and nemar-cli
   dev's level-0 recipe.
3. `bun frontend/browser-harness/chrome.js` -- the same, in headless Chrome,
   which is the only thing here that proves the wheel loads by URL with its
   sha256 checked, under a real CSP.
4. `uv run python frontend/browser-harness/widget_e2e.py --nemar`, against the
   live archive: open the page it prints and ask it for the "recipe" (the
   `python_browser` recipe production's `nemar_read_window` serves, fetched when
   the page's server starts) and the "prompt" (the snippet NEMAR's system prompt
   teaches) -- both run the new wheel against real, public `zarr.nemar.org` data.

Only once all four pass: update `sources.toml` (repository, branch, path, commit
and build command for the new commit) and the table at the top of this file
(file name and sha256), in the same pull request as the new wheel.
