# 0013. The chat follows the deployment it runs in

Date: 2026-09-24

## Status

Accepted.

## Context

OSA runs two deployments from one image: production (`api.osc.earth/osa`), and develop (`api.osc.earth/osa-dev`), which the staging sites embed.
test.nemar.org, the staging nemar.org, embeds the develop widget.

A community's data can have a staging copy that production's hosts do not serve.
NEMAR's does: staging's catalog is the exemplar fleet (`xx0999NN`), on `mcp-test.nemar.org` and `zarr-test.nemar.org`, and it has no other datasets.
Until this record, NEMAR's config named one Model Context Protocol (MCP) server and one Zarr host for every deployment, production's.
So the develop chat on test.nemar.org could not read a single dataset test.nemar.org shows:
production's MCP answered `describe_dataset(xx099906)` with "was not found in the public catalog", and `zarr.nemar.org/xx099906/zarr/index.json` is 404 (issue #480, checked 2026-09-24).

The notebook site already picks the host per deployment, at build time: `notebook.zarr_base` and `notebook.dataset_page_base` are `{production, develop}` maps, and `scripts/build_notebook_site.py --environment` fills the matching one in ([0011](0011-the-notebook-site.md)).
The chat had no equivalent, and the backend had no notion of which deployment it is:
the two containers share one `.env` on the host, and differ only in their port and in the `ROOT_PATH` that `deploy/auto-update-dev.sh` passes (`/osa-dev`).

## Decision

**A value that differs by deployment is written as a `{production, develop}` map, the notebook's shape, and the backend resolves it for the deployment it is.**
Three fields take either one value or such a map: `extensions.mcp_servers[].url`, `runtime.python.fetch_allow` and `runtime.python.prelude`.
A map must name both deployments and nothing else, and every deployment's prelude is compiled at config load, so a mistake in the develop value fails in production's checks too, rather than at the first staging reader's boot.
The MCP client connects to `McpServer.resolved_url`; the public config response carries `RuntimeConfig.for_deployment(...)`, one `fetch_allow` list and one prelude, so the widget and the runtime bundle are unchanged.

**The deployment is `OSA_DEPLOYMENT` when set, and otherwise `develop` for the container mounted at `/osa-dev` and `production` for anything else** (`src/core/config/deployment.py`).
The fallback is deliberate, not a convenience: the develop container's launch script runs from the host, so a deploy of this repository does not change what it passes, and it already passes `ROOT_PATH=/osa-dev`.
Every launch path in `deploy/` now names the deployment: `deploy/auto-update-dev.sh` passes `OSA_DEPLOYMENT=develop`, `deploy/auto-update.sh` and `deploy/deploy.sh` pass whichever their `ENVIRONMENT` selects, and `deploy/docker-compose.yml` sets `production`.
Production is named rather than left to the fallback because the `.env` both containers read is shared: a `ROOT_PATH=/osa-dev` left in it must not be able to send production's readers to staging hosts.
The host runs its own copies of these scripts, so the names take effect when those copies are updated; until then the mount decides, which is what it did before this record.
A value other than the two names is a startup error, never a silent production.
Local runs and tests set neither, and read production, as they did before.

NEMAR names the staging hosts for develop: `https://mcp-test.nemar.org/mcp`, `https://zarr-test.nemar.org/`, and a develop prelude that also assigns `eegprep_lean.index.INDEX_URL_TEMPLATE` to the staging host, because eegprep-lean's `read_index` takes its URL from that module global at each call and has no other setting for it.
`tests/test_assistants/test_nemar_contract_live.py` reads the vendored wheel's source to prove `read_index` still reads that global at call time, so a re-vendored wheel that changed it fails a test instead of quietly sending the develop chat back to production.

## Consequences

- The develop chat reads what the staging site shows, so a change can be tried on test.nemar.org end to end before it reaches production, which is what the staging environment is for.
- The same shape covers the notebook's build-time maps and the chat's runtime ones, with one list of deployment names (`DEPLOYMENTS`, the notebook's `NOTEBOOK_ENVIRONMENTS`).
- A community with no staging copy writes one value and is unaffected; every community but NEMAR does today.
- The develop prelude depends on an eegprep-lean module global. A `set_default_index_url`-style setting in eegprep-lean would replace that line; the test above is what says when one is needed.

## Alternatives considered

- **Point the develop deployment at production's data.** That is what the config did, and it is the problem: staging has no production datasets, and must not, since dev's database shares production's users (nemarOrg/nemar-cli's own staging rules).
- **Environment variables substituted into the config** (`${NEMAR_MCP_URL}`). The two containers share one `.env`, so the develop container would need its own variables passed by a host-side script this repository cannot update by deploying; and a config that reads the environment is harder to test and to read than a map that names both values.
- **Tell the widget which deployment it is, and let it choose.** The MCP server is chosen by the backend, not the widget, so the backend has to know anyway; and a widget that chose its own egress allowlist would be choosing its own sandbox.
