"""Version and build identity.

Identity is read from the installed distribution when available and falls back
to a constant, so the daemon reports something truthful even when run from a
source checkout that was never installed.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from functools import lru_cache
from importlib import metadata

_FALLBACK_VERSION = "0.2.0.dev0"
_GIT_TIMEOUT_S = 5


@lru_cache(maxsize=1)
def __version__() -> str:  # noqa: N802 - module attribute assigned below
    try:
        return metadata.version("metaharness")
    except metadata.PackageNotFoundError:
        return _FALLBACK_VERSION
    except Exception:  # pragma: no cover - defensive
        return _FALLBACK_VERSION


VERSION = __version__()


@lru_cache(maxsize=1)
def git_sha() -> str:
    """Short commit sha, or ``"unknown"``.

    Never raises: identity must not be able to stop the daemon from booting.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            cwd=str(_repo_hint()),
        )
    except Exception:
        return "unknown"
    if out.returncode != 0:
        return "unknown"
    value = (out.stdout or "").strip()
    return value or "unknown"


def _repo_hint() -> str:
    from .paths import find_repo_root

    root = find_repo_root()
    return str(root) if root else "."


def git_branch() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            cwd=str(_repo_hint()),
        )
    except Exception:
        return "unknown"
    if out.returncode != 0:
        return "unknown"
    return (out.stdout or "").strip() or "unknown"


def python_runtime() -> str:
    return f"{platform.python_implementation()} {sys.version.split()[0]}"


def runtime_info() -> dict:
    """Everything ``GET /version`` reports. Pure data, no side effects."""
    return {
        "name": "meta-harness",
        "version": VERSION,
        "schema_version": 1,
        "git_sha": git_sha(),
        "git_branch": git_branch(),
        "python": python_runtime(),
        "platform": platform.platform(),
    }
