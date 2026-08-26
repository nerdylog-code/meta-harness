"""Hermes Home + data dir resolution.

Mirrors Hermes's own resolution order (env -> hermes_constants -> default).
"""

from __future__ import annotations

import os
from pathlib import Path


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).expanduser()
    except Exception:
        env = os.environ.get("HERMES_HOME", "").strip()
        if env:
            return Path(env).expanduser()
        return Path.home() / ".hermes"


def data_dir(root: Path | None = None) -> Path:
    """Meta-Harness data directory. Created on first call."""
    base = (root or hermes_home()) / "meta-harness"
    base.mkdir(parents=True, exist_ok=True)
    return base


def events_path(root: Path | None = None) -> Path:
    return data_dir(root) / "events.jsonl"


def store_path(root: Path | None = None) -> Path:
    return data_dir(root) / "harness.db"


def artifacts_dir(root: Path | None = None) -> Path:
    p = data_dir(root) / "artifacts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def character_packs_dir(root: Path | None = None) -> Path:
    """User-extendable character packs live here. The repo ships defaults
    which are copied in on install."""
    p = data_dir(root) / "character-packs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def generated_plugins_dir(root: Path | None = None) -> Path:
    """Immutable, versioned model-authored plugins."""
    p = data_dir(root) / "generated"
    p.mkdir(parents=True, exist_ok=True)
    return p


def topologies_dir(root: Path | None = None) -> Path:
    p = data_dir(root) / "topologies"
    p.mkdir(parents=True, exist_ok=True)
    return p


def roles_dir(root: Path | None = None) -> Path:
    p = data_dir(root) / "roles"
    p.mkdir(parents=True, exist_ok=True)
    return p