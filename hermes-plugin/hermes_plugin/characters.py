"""Character pack registry.

A character pack is a declarative manifest + sprite assets. We never ship
third-party game art. The default pack is original work.

Manifest schema:

  {
    "id": "default",
    "name": "Default Cast",
    "version": "1.0.0",
    "renderer": "spritesheet",
    "tile": { "width": 32, "height": 48 },
    "characters": [
      {
        "id": "architect",
        "name": "Architect",
        "sprite": "architect.png",
        "anchor": [0.5, 1.0],
        "scale": 1.0,
        "animations": {
          "idle":          {"frames": [0]},
          "walk_south":    {"frames": [0, 1, 2, 3], "fps": 8},
          "thinking":      {"frames": [4, 5, 4, 5], "fps": 4},
          "working_file":  {"frames": [6, 7], "fps": 6},
          ...
        }
      }
    ]
  }

Missing animations ALWAYS fall back to ``working`` then ``idle``. The renderer
never crashes on an unknown animation.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

logger = logging.getLogger("meta-harness.characters")


_STATE_TO_ANIM = {
    "idle": "idle",
    "starting": "thinking",
    "thinking": "thinking",
    "working": "working_file",
    "blocked": "blocked",
    "validating": "validating",
    "completed": "success",
    "failed": "error",
    "cancelled": "idle",
}


@dataclass
class Animation:
    frames: list[int] = field(default_factory=list)
    fps: int = 6

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Animation":
        if not raw:
            return cls(frames=[0])
        return cls(frames=list(raw.get("frames", [0])),
                   fps=int(raw.get("fps", 6)))


@dataclass
class Character:
    id: str
    name: str
    sprite: str
    anchor: tuple[float, float] = (0.5, 1.0)
    scale: float = 1.0
    animations: dict[str, Animation] = field(default_factory=dict)

    def animation_for(self, state: str) -> tuple[str, Animation]:
        name = _STATE_TO_ANIM.get(state, "idle")
        if name in self.animations:
            return name, self.animations[name]
        for fallback in ("working_file", "idle"):
            if fallback in self.animations:
                return fallback, self.animations[fallback]
        return "idle", Animation(frames=[0])


@dataclass
class Pack:
    id: str
    name: str
    version: str
    renderer: str
    tile_width: int
    tile_height: int
    characters: dict[str, Character] = field(default_factory=dict)
    directory: Path | None = None

    def character(self, character_id: str) -> Character | None:
        return self.characters.get(character_id)


_PACKS: dict[str, Pack] = {}
_ACTIVE: str | None = None
_LOCK = threading.Lock()


def _coerce_animation(raw: Any) -> Animation:
    if raw is None:
        return Animation(frames=[0])
    if isinstance(raw, list):
        return Animation(frames=list(raw))
    if isinstance(raw, dict):
        return Animation.from_dict(raw)
    return Animation(frames=[0])


def load_manifest(path: Path) -> Pack:
    raw = json.loads(path.read_text(encoding="utf-8"))
    tile = raw.get("tile", {}) or {}
    pack = Pack(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        version=str(raw.get("version", "1.0.0")),
        renderer=str(raw.get("renderer", "spritesheet")),
        tile_width=int(tile.get("width", 32)),
        tile_height=int(tile.get("height", 48)),
        directory=path.parent,
    )
    for char_raw in raw.get("characters", []):
        anims = {k: _coerce_animation(v) for k, v in (char_raw.get("animations") or {}).items()}
        anchor = char_raw.get("anchor") or [0.5, 1.0]
        pack.characters[char_raw["id"]] = Character(
            id=char_raw["id"],
            name=char_raw.get("name", char_raw["id"]),
            sprite=char_raw.get("sprite", ""),
            anchor=(float(anchor[0]), float(anchor[1])),
            scale=float(char_raw.get("scale", 1.0)),
            animations=anims,
        )
    return pack


def register(pack: Pack) -> None:
    with _LOCK:
        _PACKS[pack.id] = pack
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = pack.id


def scan(user_dir: Path | None = None,
         builtin_dir: Path | None = None) -> list[str]:
    """Scan both the user-extendable dir and the built-in dir. Returns ids loaded."""
    loaded = []
    for root in (builtin_dir, user_dir):
        if not root or not root.exists():
            continue
        # Packs live in their own subdirectories; look one level deep.
        for path in sorted(root.glob("*/character-pack.json")):
            try:
                pack = load_manifest(path)
                register(pack)
                loaded.append(pack.id)
            except Exception as exc:
                logger.warning("character pack load failed (%s): %s", path, exc)
    return loaded


def seed_builtins() -> None:
    """Copy built-in packs into the user directory on first run."""
    # <repo>/hermes-plugin/hermes_plugin/characters.py -> parents[2] = repo.
    package_root = Path(__file__).resolve().parents[2]
    builtin_root = package_root / "character-packs"
    if not builtin_root.exists():
        return
    user_root = paths.character_packs_dir()
    for src in builtin_root.glob("*/character-pack.json"):
        pack_dir = user_root / src.parent.name
        pack_dir.mkdir(parents=True, exist_ok=True)
        dst = pack_dir / "character-pack.json"
        if not dst.exists():
            shutil.copy2(src, dst)
        # Copy assets too — best effort, don't fail the whole pack if a
        # single image is missing on a strange filesystem.
        assets_src = src.parent / "assets"
        if assets_src.exists():
            assets_dst = pack_dir / "assets"
            assets_dst.mkdir(parents=True, exist_ok=True)
            for asset in assets_src.glob("*"):
                target = assets_dst / asset.name
                if not target.exists():
                    try:
                        shutil.copy2(asset, target)
                    except Exception:
                        pass


def list_packs() -> list[dict]:
    with _LOCK:
        return [
            {"id": p.id, "name": p.name, "version": p.version,
             "renderer": p.renderer,
             "characters": [{"id": c.id, "name": c.name} for c in p.characters.values()]}
            for p in _PACKS.values()
        ]


def get(pack_id: str) -> Pack | None:
    with _LOCK:
        return _PACKS.get(pack_id)


def active_id() -> str | None:
    return _ACTIVE


def set_active(pack_id: str) -> bool:
    global _ACTIVE
    with _LOCK:
        if pack_id not in _PACKS:
            return False
        _ACTIVE = pack_id
    return True


def get_pack_with_assets(pack_id: str) -> dict | None:
    """Return a pack payload including the assets directory (so the renderer
    can resolve sprite URLs)."""
    pack = get(pack_id)
    if not pack:
        return None
    payload = {
        "id": pack.id, "name": pack.name, "version": pack.version,
        "renderer": pack.renderer,
        "tile": {"width": pack.tile_width, "height": pack.tile_height},
        "characters": [],
        "assets_url": f"/api/plugins/meta-harness/character-packs/{pack.id}/assets/",
    }
    for char in pack.characters.values():
        anims = {k: {"frames": a.frames, "fps": a.fps}
                 for k, a in char.animations.items()}
        payload["characters"].append({
            "id": char.id, "name": char.name, "sprite": char.sprite,
            "anchor": list(char.anchor), "scale": char.scale,
            "animations": anims,
        })
    return payload


def animation_for(agent_state: str, character_id: str,
                  pack_id: str | None = None) -> tuple[str, Animation, str]:
    """Resolve the best animation for a (state, character) tuple.

    Returns (pack_id, animation, name). Always returns something — never
    crashes on missing state or character.
    """
    pack = get(pack_id or _ACTIVE or "default")
    if not pack:
        return ("", Animation(frames=[0]), "idle")
    char = pack.character(character_id)
    if not char:
        # Fall back to any character in the pack.
        for c in pack.characters.values():
            name, anim = c.animation_for(agent_state)
            return pack.id, anim, name
        return pack.id, Animation(frames=[0]), "idle"
    name, anim = char.animation_for(agent_state)
    return pack.id, anim, name


def reset() -> None:
    global _ACTIVE
    with _LOCK:
        _PACKS.clear()
        _ACTIVE = None