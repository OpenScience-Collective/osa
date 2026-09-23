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
| `eegprep_lean-0.1.0.dev2-py3-none-any.whl` | `sccn/eegprep` at `b4aa18bd` (#416), `packages/eegprep-lean`, `uv build --wheel` | `39f33b99...7157582` |

zarr's own dependencies are not here:
Pyodide 0.29.5 ships every one of them, and `depends.toml` names them.
They are why the runtime needs 0.29.5,
since 0.28.3 has no `google-crc32c` and zarr imports it.

## Changing a wheel

1. Build or download the new wheel under a **new version**.
   Wheels are served as immutable for a year,
   so new bytes under an old name would reach some readers and not others,
   and the generator refuses them.
2. Put it in `wheels/`, remove the old one, and update `depends.toml` if its needs changed.
3. Regenerate the overlay, and record where the wheel came from in the table above:

   ```bash
   uv run python scripts/build_runtime_lock.py src/assistants/nemar/runtime/nemar-pyodide-lock.json
   ```

4. Run `bun frontend/test-data-lane.js`, which loads these exact files in a real Pyodide and reads a store through them.

A test fails when the overlay is not what the wheels and `depends.toml` produce.
