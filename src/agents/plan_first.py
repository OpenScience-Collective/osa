"""The prompt section that makes the analysis model plan before it runs code.

A community writes the text (`CommunityConfig.plan_first_instructions`); this module only
decides when it is sent. It is sent exactly when the model can follow it: the turn is on
the analysis model, the request declares `propose_plan` and so the tool is bound
(`src.tools.client_tools.build_client_tools`), and client tools are not switched off. A
prompt that told the model to call a tool it was not given would send it to invent one, the
same reason `QUICK_MODE_INSTRUCTIONS` is added by the server and not written in the YAML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.config.community import PROPOSE_PLAN_TOOL_NAME, Tier
from src.tools.client_tools import client_tools_disabled

if TYPE_CHECKING:
    from src.core.config.community import CommunityConfig


def plan_first_section(
    config: CommunityConfig | None,
    tier: Tier | None,
    declared: set[str] | None,
) -> str | None:
    """The community's plan-first text for this turn, or None when the turn gets none.

    Args:
        config: The community's configuration, or None for a community without one.
        tier: The turn's model tier, or None when the community does not route.
        declared: The client tool names the request declares.

    Returns:
        The text, only when `tier` is analysis, `propose_plan` is declared, the client-tool
        kill switch is off and the community set `plan_first_instructions`.
    """
    if config is None or not config.plan_first_instructions:
        return None
    if tier != "analysis":
        return None
    if PROPOSE_PLAN_TOOL_NAME not in (declared or set()):
        return None
    if client_tools_disabled():
        return None
    return config.plan_first_instructions.strip()
