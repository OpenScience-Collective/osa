"""Which OSA deployment this process is, and the config values that differ by it (#480).

Two deployments run the same image: production, and develop, which serves the
staging sites (test.nemar.org embeds the develop widget). A community whose data
has a staging copy names a value per deployment, the way ``notebook.zarr_base``
already does for the notebook site's build (ADR 0011), so the develop chat reads
the staging hosts and never production's: production's hosts do not know the
datasets a staging page shows (docs/adr/0013-the-chat-follows-its-deployment.md).

Import-light on purpose: ``src.core.config.community`` imports this, and that module
must load without the ``server`` extra.
"""

from __future__ import annotations

import os
from typing import Literal, TypeVar

from src.core.config.notebook_lock import NOTEBOOK_ENVIRONMENTS

Deployment = Literal["production", "develop"]

DEPLOYMENTS: tuple[Deployment, ...] = NOTEBOOK_ENVIRONMENTS
"""The same names the notebook build's ``--environment`` takes: one list, so a
community's per-deployment maps and its ``notebook.zarr_base`` share their keys."""

DEVELOP_ROOT_PATH = "/osa-dev"
"""Where ``deploy/auto-update-dev.sh`` mounts the develop container (its ``ROOT_PATH``),
and so what marks a process as develop when ``OSA_DEPLOYMENT`` is not set."""

T = TypeVar("T")


def current_deployment() -> Deployment:
    """``OSA_DEPLOYMENT`` when it is set and not blank; otherwise ``develop`` for the
    container mounted at ``/osa-dev``, and ``production`` for anything else.

    Blank counts as unset, because that is what an env file referencing an unset
    host variable passes; it falls to the mount, as an unset one does.

    The fallback exists because the develop container's launch script lives on the
    host and runs from there: a deploy of this repository does not change what it
    passes, and it already passes ``ROOT_PATH=/osa-dev``. Local runs and tests have
    neither, and are production, which is what they read today.

    Read from the process environment on every call, not cached, so a test can set
    it. A value other than the two names is a startup error, not a silent default.
    """
    named = os.environ.get("OSA_DEPLOYMENT", "").strip().lower()
    if named:
        if named not in DEPLOYMENTS:
            raise ValueError(f"OSA_DEPLOYMENT={named!r}; must be one of {DEPLOYMENTS}")
        return named  # type: ignore[return-value]
    root_path = os.environ.get("ROOT_PATH", "").strip().rstrip("/")
    return "develop" if root_path == DEVELOP_ROOT_PATH else "production"


def check_deployment_map(value: object, field: str) -> None:
    """Raise unless a per-deployment map names every deployment and nothing else.

    Both, not either: a map missing one would leave that deployment with no value,
    discovered only when it runs. Called by the config models' validators."""
    if not isinstance(value, dict):
        return
    keys = set(value)
    missing = [d for d in DEPLOYMENTS if d not in keys]
    unknown = sorted(str(k) for k in keys - set(DEPLOYMENTS))
    # Both in one message: {"production", "staging"} is a typo for "develop", and
    # naming only the unknown key would hide that develop is the one missing.
    problems = []
    if unknown:
        problems.append(f"names an unknown deployment {unknown}")
    if missing:
        problems.append(f"has no value for {missing}")
    if problems:
        raise ValueError(
            f"{field} {' and '.join(problems)}; a per-deployment map names {DEPLOYMENTS}"
        )


def for_deployment(value: T | dict[Deployment, T], deployment: Deployment) -> T:
    """The value for ``deployment``: the map's entry, or the one value every
    deployment shares."""
    if isinstance(value, dict):
        return value[deployment]
    return value
