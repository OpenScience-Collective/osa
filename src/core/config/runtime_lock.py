"""A community's Pyodide lock overlay: the wheels its browser runtime adds to Pyodide's.

Pyodide resolves every package it loads from a lockfile, and ``loadPyodide`` accepts
one as data. A community that needs a pure-Python wheel the Pyodide distribution does
not ship lists it here, in Pyodide's own lock-entry shape; the widget merges these
entries into the stock lock of the community's pinned Pyodide version, and Pyodide
checks each wheel's sha256 as it loads it. That makes the overlay the ``uv.lock`` of
the design note: one reviewed file, and every reader of a community gets the same
environment.

The overlay and its wheels live in the community's own folder, and the server that
sends the hashes also serves the bytes (``GET /{community}/runtime/{file_name}``), so
the two can never come from different releases.

A wheel's file name is its identity. Wheels are served as immutable for a year, so a
changed wheel must carry a new version, and a mismatch between a committed wheel and
the sha256 its entry records fails here, at load, rather than in a reader's browser.
The bytes the route serves are the bytes that were verified, held in memory, so a file
changed on disk after the check cannot go out under the entry's name.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

#: Where an overlay's wheels sit, beside the overlay itself.
WHEELS_DIR_NAME = "wheels"

# Pure Python only: Pyodide has no compiler, and a platform wheel would not even be
# built for its ABI. Compiled packages come from the Pyodide distribution.
_WHEEL_FILE_NAME = re.compile(r"^[A-Za-z0-9_.+-]+-py3-none-any\.whl$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_name(name: str) -> str:
    """A package name as Pyodide keys its lock: PEP 503 normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


class RuntimeLockPackage(BaseModel):
    """One wheel, in the shape of a Pyodide lock entry, so it merges verbatim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    file_name: str
    """The wheel's file name, bare. The widget turns it into a URL on this server."""

    package_type: Literal["package"]
    install_dir: Literal["site"]
    sha256: str
    imports: list[str]
    depends: list[str]
    """Other lock entries this one needs, by canonical name: the overlay's own or the
    Pyodide distribution's. Pyodide resolves them when the package is loaded."""

    @field_validator("file_name")
    @classmethod
    def _pure_python_wheel(cls, value: str) -> str:
        if not _WHEEL_FILE_NAME.fullmatch(value):
            raise ValueError(
                f"file_name {value!r} is not a bare py3-none-any wheel name; the browser "
                "can only install pure-Python wheels, and a path is not a name"
            )
        return value

    @field_validator("sha256")
    @classmethod
    def _hex_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value


class RuntimeLockOverlay(BaseModel):
    """The entries a community adds to Pyodide's lock, keyed as Pyodide keys them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    packages: dict[str, RuntimeLockPackage]

    @model_validator(mode="after")
    def _keys_and_files_are_consistent(self) -> RuntimeLockOverlay:
        if not self.packages:
            raise ValueError(
                "a lock overlay with no packages adds nothing; remove the lockfile key"
            )
        for key, entry in self.packages.items():
            if key != canonical_name(entry.name):
                raise ValueError(
                    f"package key {key!r} is not the canonical form of its name "
                    f"{entry.name!r} ({canonical_name(entry.name)!r}), which is how "
                    "Pyodide looks it up"
                )
        file_names = [entry.file_name for entry in self.packages.values()]
        if len(set(file_names)) != len(file_names):
            raise ValueError("two packages name the same wheel file")
        return self


class RuntimeLockError(ValueError):
    """A community's lock overlay cannot be used as committed."""


def lockfile_path_problem(lockfile: str) -> str | None:
    """Why ``lockfile`` cannot name a file inside a community folder, or None.

    Checked on the string, so a config is refused before anything touches the disk:
    the path is joined to a folder on the server, and a config must not be able to
    point it anywhere else.
    """
    if "\\" in lockfile:
        return "use forward slashes"
    if PurePosixPath(lockfile).is_absolute():
        return "it must be relative to the community's folder"
    # The raw segments, since PurePosixPath would quietly drop a "." or an empty one.
    if any(segment in ("", ".", "..") for segment in lockfile.split("/")):
        return "it must be a plain relative path, with no empty, '.' or '..' segments"
    if PurePosixPath(lockfile).suffix != ".json":
        return "it must be a .json file"
    return None


@dataclass(frozen=True)
class _Verified:
    overlay: RuntimeLockOverlay
    wheels: Mapping[str, bytes]
    """Each listed wheel's bytes, by file name, exactly as they were hashed."""


@dataclass(frozen=True)
class _Unusable:
    reason: str


@functools.cache
def _verify(community_dir: Path, lockfile: str) -> _Verified | _Unusable:
    """Read a community's overlay and check every wheel it lists against its sha256.

    Cached for the life of the process, a failure as much as a success: the files
    are part of the deployment and do not change under it, and a broken overlay is
    asked about on every /config and every wheel request, so re-reading and
    re-hashing every wheel each time would cost the most exactly when nothing can
    come of it. The reason is cached rather than the exception, since raising one
    exception object again and again grows its traceback each time.
    """
    problem = lockfile_path_problem(lockfile)
    if problem is not None:
        return _Unusable(f"lockfile {lockfile!r}: {problem}")
    path = community_dir / lockfile
    try:
        overlay = RuntimeLockOverlay.model_validate(json.loads(path.read_text()))
    except OSError as err:
        return _Unusable(f"lock overlay {path} cannot be read: {err}")
    except (json.JSONDecodeError, ValidationError) as err:
        return _Unusable(f"lock overlay {path} is not a valid overlay: {err}")

    wheels_dir = path.parent / WHEELS_DIR_NAME
    wheels: dict[str, bytes] = {}
    for key, entry in overlay.packages.items():
        wheel = wheels_dir / entry.file_name
        try:
            data = wheel.read_bytes()
        except OSError as err:
            return _Unusable(f"{key}: wheel {wheel} cannot be read: {err}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.sha256:
            return _Unusable(
                f"{key}: wheel {wheel} has sha256 {digest}, not the {entry.sha256} its entry "
                "records. A changed wheel needs a new version, because served wheels are "
                "cached by name for a year."
            )
        wheels[entry.file_name] = data
    return _Verified(overlay, MappingProxyType(wheels))


def _verified(community_dir: Path, lockfile: str) -> _Verified:
    outcome = _verify(community_dir, lockfile)
    if isinstance(outcome, _Unusable):
        raise RuntimeLockError(outcome.reason)
    return outcome


def load_runtime_lock(community_dir: Path, lockfile: str) -> RuntimeLockOverlay:
    """A community's overlay, every wheel it lists verified against its sha256.

    A copy, so nothing a caller does to it reaches the verified original the wheel
    route serves from.

    Raises:
        RuntimeLockError: The overlay is missing, malformed, or lists a wheel that is
            absent or whose bytes do not match its sha256.
    """
    return _verified(community_dir, lockfile).overlay.model_copy(deep=True)


def runtime_wheel(community_dir: Path, lockfile: str, file_name: str) -> bytes | None:
    """The verified bytes to serve for ``file_name``, or None when the overlay does not list it.

    A lookup against the overlay, never a join of the request onto the disk: a name
    that is not an entry is refused before any path is built from it.

    Raises:
        RuntimeLockError: As ``load_runtime_lock``.
    """
    return _verified(community_dir, lockfile).wheels.get(file_name)
