"""Anthropic endpoint constants, shared by the server and the CLI.

Deliberately free of third-party imports. ``src/core/services/anthropic_llm.py``
cannot be imported by the CLI's light paths: it pulls in langchain-anthropic,
which lives in the ``server`` extra, and ``src/cli/main.py`` registers
``osa validate`` only if importing it succeeds (see ``_register_server_commands``).
Importing the provider module from the validator would therefore turn file-mode
validation into a "requires server dependencies" stub for anyone on a CLI-only
install. Keeping the endpoint here lets both sides agree on it at no cost.
"""

# Anthropic's own Messages API, as opposed to the AWS-hosted endpoint that
# ANTHROPIC_BASE_URL points at. A key that is not the platform's own (BYOK, or
# a community's key named by `anthropic_api_key_env_var`) is not authorized on
# the platform's AWS workspace, so requests carrying one go here instead. This
# is why `osa validate --test-api-key` probes this endpoint for a community
# Anthropic key: it is the one that community's requests will actually use.
FIRST_PARTY_BASE_URL = "https://api.anthropic.com"

# Version header the first-party Messages API requires on every request.
API_VERSION = "2023-06-01"
