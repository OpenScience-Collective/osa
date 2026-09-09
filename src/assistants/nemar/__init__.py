"""NEMAR Assistant - NeuroElectroMagnetic Archive.

Self-contained assistant module for discovering and exploring BIDS-formatted
EEG, MEG, and iEEG datasets hosted on NEMAR (nemar.org).

This module carries no Python tools. Its dataset tools come from NEMAR's own MCP
server (`https://mcp.nemar.org/mcp`), configured under `extensions.mcp_servers`
in `config.yaml` and loaded by `src/tools/mcp_client.py`.

That replaced two hand-written tools, `search_nemar_datasets` and
`get_nemar_dataset_details`, which called
`nemar.org/api/dataexplorer/datapipeline/...`. That endpoint returns 404: the
legacy dataexplorer site is gone and its URLs now redirect to
`nemar.org/dataset/<id>`. So the tools had been non-functional, and the MCP
server replaces them rather than supplementing them -- `search_datasets` and
`describe_dataset` map onto the two of them almost exactly, with four more tools
for what is inside a dataset.

All other configuration (system prompt, CORS, budget) is in config.yaml.
"""
