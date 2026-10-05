"""Filesystem layout — resolved by code, never by ``__file__`` arithmetic.

The v1 MVP resolved its built-in assets with ``Path(__file__).resolve().parents[N]``,
which happened to be right inside the repository and wrong in every real
installation (`docs/architecture/V1_INVENTORY.md` D1). v2 uses two explicit,
OS-correct mechanisms instead:

  * **user data** comes from ``platformdirs`` (with an env override for tests
    and for portable installs);
  * **repository root** is *discovered* by walking up for a marker file, and
    returns ``None`` when there is none — a source checkout is an optional
    context, not an assumption.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

APP_NAME = "MetaHarness"
APP_AUTHOR = False  # keep the path flat: <data>/MetaHarness, not <data>/<author>/MetaHarness

DATA_DIR_ENV = "METAHARNESS_DATA_DIR"
REPO_MARKER = "pyproject.toml"
REPO_NAME_MARKER = 'name = "metaharness"'
MAX_WALK_UP = 8

# Subdirectories of the data root. Created on demand by ensure_layout().
SUBDIRS = ("config", "data", "cache", "logs", "plugins", "workspaces", "artifacts")


def data_root(override: str | os.PathLike | None = None) -> Path:
    """Absolute data root for this installation.

    Precedence: explicit argument -> ``METAHARNESS_DATA_DIR`` -> ``platformdirs``.
    The env override exists so tests and portable installs never touch the
    user's real directory.
    """
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get(DATA_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=APP_AUTHOR)).expanduser().resolve()


def subdir(name: str, root: str | os.PathLike | None = None) -> Path:
    if name not in SUBDIRS:
        raise ValueError(f"unknown subdirectory {name!r}; expected one of {SUBDIRS}")
    return data_root(root) / name


def ensure_layout(root: str | os.PathLike | None = None) -> Path:
    """Create the data root and its subdirectories. Idempotent."""
    base = data_root(root)
    base.mkdir(parents=True, exist_ok=True)
    for name in SUBDIRS:
        (base / name).mkdir(parents=True, exist_ok=True)
    return base


def find_repo_root(start: str | os.PathLike | None = None) -> Path | None:
    """Walk up from ``start`` (default: this file) looking for the repo marker.

    Returns ``None`` when the code is running from an installed wheel outside
    any checkout. Callers must handle ``None`` rather than assuming a repo.
    """
    here = Path(start) if start else Path(__file__)
    try:
        current = here.resolve()
    except OSError:  # pragma: no cover - defensive
        return None
    if current.is_file():
        current = current.parent
    for _ in range(MAX_WALK_UP):
        marker = current / REPO_MARKER
        if marker.is_file():
            try:
                head = marker.read_text(encoding="utf-8", errors="replace")[:2048]
            except OSError:  # pragma: no cover - defensive
                head = ""
            if REPO_NAME_MARKER in head:
                return current
        if current.parent == current:
            return None
        current = current.parent
    return None
