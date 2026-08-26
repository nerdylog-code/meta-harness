# Character packs

A character pack is **declarative assets only**: a manifest plus sprite
files. The runtime never executes pack code.

## Folder layout

```
character-packs/
  default/
    character-pack.json    # manifest
    assets/
      architect.svg
      architect_walk.svg
      builder.svg
      ...
  custom/
    character-pack.json
    assets/
      protagonist.png
      protagonist_walk.png
```

## Manifest schema (v1)

```json
{
  "id": "default",
  "name": "Default Cast",
  "version": "1.0.0",
  "renderer": "procedural | spritesheet",
  "tile": { "width": 32, "height": 48 },
  "characters": [
    {
      "id": "architect",
      "name": "Architect",
      "sprite": "architect.svg",
      "anchor": [0.5, 1.0],
      "scale": 1.0,
      "palette": {
        "primary": "#4a78c8",
        "secondary": "#2c4f8c",
        "accent": "#f5c84a"
      },
      "animations": {
        "idle":             { "frames": [0] },
        "walk_south":       { "frames": [0, 1], "fps": 6 },
        "walk_north":       { "frames": [0, 1], "fps": 6 },
        "walk_east":        { "frames": [0, 1], "fps": 6 },
        "walk_west":        { "frames": [0, 1], "fps": 6 },
        "thinking":         { "frames": [0] },
        "working_file":     { "frames": [0] },
        "working_terminal": { "frames": [0] },
        "working_web":      { "frames": [0] },
        "delegating":       { "frames": [0] },
        "validating":       { "frames": [0] },
        "blocked":          { "frames": [0] },
        "success":          { "frames": [0] },
        "error":            { "frames": [0] }
      }
    }
  ]
}
```

### Required fields

- `id` — unique pack id; matches the folder name.
- `characters[].id` — used by the agent-role binding.
- `characters[].sprite` — relative to `assets/`.

### Optional fields

- `palette` — primary/secondary/accent; the renderer may use them for
  recoloring sprites or drawing fallback glyphs.
- `anchor` — sprite anchor in normalized coordinates (default `[0.5, 1.0]`).
- `scale` — multiplier for the rendered tile.
- `animations` — see below.

## Animation fallback

A runtime state maps to an animation name:

| Agent state | Animation |
|---|---|
| idle | `idle` |
| starting / thinking | `thinking` |
| working | `working_file` |
| blocked | `blocked` |
| validating | `validating` |
| completed | `success` |
| failed | `error` |
| cancelled | `idle` |

If the requested animation is missing in the character's manifest, the
runtime falls back in this order:

1. `working_file`
2. `idle`

The renderer never crashes on a missing animation.

## State machine that drives characters

```text
created -> starting -> idle -> thinking -> working
                                     |         |
                                     v         v
                                 validating   completed
                                     |         |
                                     v         v
                                 failed <-- cancelled
```

## Installing a custom pack

1. Create a folder under `<hermes-home>/meta-harness/character-packs/<your-pack>/`.
2. Drop `character-pack.json` and `assets/*` in it.
3. Trigger reload: `POST /api/plugins/meta-harness/character-packs/reload`.
4. Activate: `POST /api/plugins/meta-harness/character-packs/<your-pack>/activate`.

No rebuild, no Python edit. The default character sprite lives at
`<hermes-home>/meta-harness/character-packs/default/assets/` and is
copied there by the bootstrap.

## Persona-style packs

The pack format is deliberately generic and supports the high-energy visual
patterns of JRPG social sims:

- per-character palette (`palette.primary / secondary / accent`)
- expressive portraits (a future renderer can map `portrait` to the role
  name on a side panel)
- station-specific animations (e.g. `working_terminal`, `working_web`)
- speech portrait (future renderer hook)

The pack format **does not** ship copyrighted game art, sprites, fonts,
portraits, or icons. Users supply their own legally obtained or original
artwork.

## Safety

The runtime:

- never executes pack JS (the loader rejects non-declarative files)
- rejects remote sprite URLs by default (assets must be in the pack dir)
- redacts credentials from any pack metadata that flows through the event
  store

## Character development mode

A future renderer may add a developer panel that:

- shows the sprite sheet
- lets the user simulate any state or animation
- previews missing animations in red
- exposes the JSON for editing

This stays out of the execution kernel. The kernel exposes `chars.animation_for()`,
which is what the developer panel calls.