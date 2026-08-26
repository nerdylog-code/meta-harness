"""Plugin Lab — versioned model-authored plugin experiments.

Stages a model-created plugin into ``<data_dir>/generated/<id>/v<N>/``,
validates it without executing it, then optionally activates experimentally.

Trust:

  * Generated code is never trusted.
  * Experimental activation is gated by an explicit ``activate_experimental``
    call from the operator (or a high-trust role).
  * Promotion only updates a pointer; rollback is a pointer update.
  * The runtime never destroys older versions.

This module only manages the *lab*; the actual mounting of an experimental
plugin as a host tool is intentionally NOT in MVP scope. The lab is an
inspectable artifact store + validator surface; promotion would route
through the host's plugin loader in a follow-up.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths, store

logger = logging.getLogger("meta-harness.plugin_lab")

_VERSION_RE = re.compile(r"^v(\d+)$")
_LOCK = threading.Lock()


@dataclass
class PluginVersion:
    plugin_id: str
    version: str
    directory: Path
    manifest: dict
    validation: dict


def _versions_root(plugin_id: str) -> Path:
    safe = re.sub(r"[^a-z0-9._-]", "-", plugin_id.lower())
    return paths.generated_plugins_dir() / safe


def _next_version(plugin_id: str) -> str:
    root = _versions_root(plugin_id)
    if not root.exists():
        return "v0001"
    existing = sorted([p.name for p in root.iterdir()
                        if p.is_dir() and _VERSION_RE.match(p.name)])
    if not existing:
        return "v0001"
    last = existing[-1]
    n = int(_VERSION_RE.match(last).group(1))
    return f"v{n + 1:04d}"


def list_plugins() -> list[dict]:
    root = paths.generated_plugins_dir()
    out = []
    if not root.exists():
        return out
    for plugin_dir in sorted(root.iterdir()):
        if not plugin_dir.is_dir():
            continue
        versions = []
        for v_dir in sorted(plugin_dir.iterdir()):
            if not v_dir.is_dir() or not _VERSION_RE.match(v_dir.name):
                continue
            manifest_path = v_dir / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {"_error": "manifest unreadable"}
            else:
                manifest = {"_error": "no manifest"}
            versions.append({"version": v_dir.name,
                             "validation": (v_dir / "validation.json").exists(),
                             "active": _is_active(plugin_dir.name, v_dir.name)})
        if versions:
            out.append({"id": plugin_dir.name, "versions": versions})
    return out


def _is_active(plugin_id: str, version: str) -> bool:
    pointer = _versions_root(plugin_id) / "current.json"
    if not pointer.exists():
        return False
    try:
        return json.loads(pointer.read_text(encoding="utf-8")).get("version") == version
    except Exception:
        return False


def get_plugin(plugin_id: str) -> dict | None:
    root = _versions_root(plugin_id)
    if not root.exists():
        return None
    out = {"id": plugin_id, "versions": []}
    pointer = root / "current.json"
    if pointer.exists():
        try:
            out["current"] = json.loads(pointer.read_text(encoding="utf-8")).get("version")
        except Exception:
            pass
    for v_dir in sorted(root.iterdir()):
        if not v_dir.is_dir() or not _VERSION_RE.match(v_dir.name):
            continue
        manifest_path = v_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
        validation = {}
        vp = v_dir / "validation.json"
        if vp.exists():
            try:
                validation = json.loads(vp.read_text(encoding="utf-8"))
            except Exception:
                validation = {}
        out["versions"].append({"version": v_dir.name,
                                "manifest": manifest,
                                "validation": validation})
    return out


def create_version(plugin_id: str, manifest: dict, code: str | None = None) -> dict:
    version = _next_version(plugin_id)
    v_dir = _versions_root(plugin_id) / version
    v_dir.mkdir(parents=True, exist_ok=True)
    (v_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    if code:
        (v_dir / "plugin.py").write_text(code, encoding="utf-8")
    with _LOCK:
        store.upsert_plugin_state(
            plugin_id=plugin_id, version=version, kind="model-generated",
            trust="quarantined", state="created",
            capabilities=list(manifest.get("provides") or []),
            metadata={"manifest": manifest},
        )
    return {"plugin_id": plugin_id, "version": version, "directory": str(v_dir)}


def validate_version(plugin_id: str, version: str) -> dict:
    v_dir = _versions_root(plugin_id) / version
    if not v_dir.exists():
        return {"ok": False, "errors": [f"version {version} not found"]}
    issues = []
    manifest_path = v_dir / "manifest.json"
    if not manifest_path.exists():
        issues.append("manifest.json missing")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append(f"manifest.json invalid: {exc}")
            manifest = {}
        if "id" not in manifest:
            issues.append("manifest.id missing")
        if "version" not in manifest:
            issues.append("manifest.version missing")
    code_path = v_dir / "plugin.py"
    if code_path.exists():
        try:
            compile(code_path.read_text(encoding="utf-8"), str(code_path), "exec")
        except SyntaxError as exc:
            issues.append(f"plugin.py syntax: {exc}")
    result = {"ok": not issues, "errors": issues, "version": version,
              "plugin_id": plugin_id}
    (v_dir / "validation.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    with _LOCK:
        state = "validated" if result["ok"] else "invalid"
        store.upsert_plugin_state(
            plugin_id=plugin_id, version=version, kind="model-generated",
            trust="quarantined", state=state,
            capabilities=(json.loads(manifest_path.read_text(encoding="utf-8")).get("provides")
                          if manifest_path.exists() else None) or [],
            metadata={"validation": result},
        )
    return result


def activate_experimental(plugin_id: str, version: str) -> dict:
    """Update the current pointer to a version. Promotion, not execution."""
    v_dir = _versions_root(plugin_id) / version
    if not v_dir.exists():
        return {"ok": False, "error": f"version {version} not found"}
    validation_path = v_dir / "validation.json"
    if not validation_path.exists():
        return {"ok": False, "error": "validate first"}
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if not validation.get("ok"):
        return {"ok": False, "error": "version invalid; refusing activation"}
    pointer = _versions_root(plugin_id) / "current.json"
    pointer.write_text(
        json.dumps({"version": version, "activated_at": _utc_iso()}, indent=2),
        encoding="utf-8",
    )
    with _LOCK:
        store.upsert_plugin_state(
            plugin_id=plugin_id, version=version, kind="model-generated",
            trust="experimental", state="experimental",
            capabilities=[],
            metadata={"validation": validation},
        )
    return {"ok": True, "plugin_id": plugin_id, "version": version,
            "pointer": str(pointer)}


def rollback(plugin_id: str, target_version: str | None = None) -> dict:
    """Rollback: update current pointer back. If target omitted, pick the
    previous version older than current."""
    root = _versions_root(plugin_id)
    pointer = root / "current.json"
    current = None
    if pointer.exists():
        try:
            current = json.loads(pointer.read_text(encoding="utf-8")).get("version")
        except Exception:
            pass
    versions = sorted([p.name for p in root.iterdir()
                       if p.is_dir() and _VERSION_RE.match(p.name)])
    if not versions:
        return {"ok": False, "error": "no versions"}
    if target_version:
        if target_version not in versions:
            return {"ok": False, "error": f"version {target_version} not found"}
        chosen = target_version
    else:
        if current:
            older = [v for v in versions if v < current]
            if not older:
                return {"ok": False, "error": "no earlier version to rollback to"}
            chosen = older[-1]
        else:
            chosen = versions[-1]
    pointer.write_text(
        json.dumps({"version": chosen, "rolled_back_from": current}, indent=2),
        encoding="utf-8",
    )
    with _LOCK:
        store.upsert_plugin_state(
            plugin_id=plugin_id, version=chosen, kind="model-generated",
            trust="experimental", state="active",
            capabilities=[],
            metadata={"rolled_back_from": current},
        )
    return {"ok": True, "plugin_id": plugin_id, "version": chosen,
            "rolled_back_from": current}


def _utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()