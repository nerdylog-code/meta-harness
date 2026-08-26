# Meta-Harness

A self-extensible, multi-agent **meta-harness** that lives **inside Hermes
Agent** and **inside Hermes Desktop**. Hermes is the host; the harness adds
orchestration, capability routing, validation, character-driven visualization
and a controlled plugin lab. MiniMax M3 alone is a valid baseline. Pi is an
optional specialist engine.

> Hermes is the host.
> MiniMax M3 can be the primary intelligence.
> Pi is an optional specialist engine.
> The topology is the cognitive structure.
> Capabilities are the vocabulary.
> Plugins are the organs.
> Events are the nervous system.
> Character packs are the skin.
> The visual workspace makes the invisible work visible.
> And the harness itself can evolve without becoming a monolith.

## What works in this build

This is a working MVP. Not a writeup. The pieces below are real code that
loads into Hermes and Hermes Desktop today:

- **Backend plugin** (`~/.hermes/plugins/meta-harness/`) exposes a
  FastAPI router under `/api/plugins/meta-harness/` and a `register(ctx)`
  entry that the Hermes agent loader picks up.
- **Desktop plugin** (`~/.hermes/desktop-plugins/meta-harness/plugin.js`)
  mounts a `/meta-harness` page, a sidebar nav row, command-palette entries
  and a status-bar chip. Hot-reload safe.
- **Event store** with SQLite (WAL) + JSONL tail and polling REST.
- **Engine adapters** for Hermes native worker (gateway RPC) and Pi
  (JSON-RPC over stdio).
- **Topology executor** with declarative YAML (solo, sequence, parallel,
  gate, retry).
- **Validation runtime** with retry caps + feedback loop.
- **Capability resolver** registered with the host's own registry semantics.
- **Character pack system** — declarative manifest + sprites + animation
  fallback; default original pack ships in this repo.
- **Animated office view** (Canvas2D) — characters move on real runtime
  events; no fake animation.
- **Plugin Lab** — inspect / create version / validate / experimental
  activate / rollback for model-generated plugins.
- **installer / uninstaller / doctor** scripts with hermes-home autodetect
  and Hermes config backup.

## Install

```bash
# 1. Copy the Python backend plugin into your Hermes plugins directory.
bash scripts/install.sh        # POSIX
scripts\install.bat           # Windows

# 2. Hermes Desktop picks up the desktop plugin automatically
#    from ~/.hermes/desktop-plugins/meta-harness/plugin.js
#    Use ⌘K -> "Reload desktop plugins" if it does not appear.

# 3. Diagnose
bash scripts/doctor.sh
```

The installer never overwrites your existing Hermes config — it patches only
the keys it needs and keeps a timestamped backup.

## Use

1. Open Hermes Desktop.
2. Sidebar → **Meta-Harness**.
3. Pick a topology (default: `solo`), pick a role prompt (default: `builder`),
   type a task, press **Run**.
4. The Agents / Timeline / Office tabs reflect real runtime events.
5. In **Characters**, switch packs or assign a character per agent.
6. In **Plugins**, enable/disable Meta-Harness internal plugins or try the
   **Plugin Lab** for model-authored experiments.

## Uninstall

```bash
bash scripts/uninstall.sh
```

Reverts the config patch, removes both plugin folders. Your Hermes config
backup is left in place.

## Project layout

```
meta-harness/
├── hermes-plugin/        # Python backend (installed at ~/.hermes/plugins/meta-harness/)
├── desktop-plugin/       # JS desktop plugin (installed at ~/.hermes/desktop-plugins/meta-harness/)
├── core/                 # capability registry, event plane, topology executor, validators
├── adapters/             # engine.hermes, engine.pi
├── topologies/           # YAML topologies
├── roles/                # role prompt packs
├── character-packs/      # default + a sample custom pack
├── scripts/              # install / uninstall / doctor
├── tests/                # unit + integration
└── docs/                 # ARCHITECTURE / DECISIONS / RECONNAISSANCE / SECURITY / PLUGINS / etc.
```

## Limitations and honesty

- The official local Hermes Agent v0.20.4 already has a sandbox/permission
  model; Meta-Harness does not promise isolation it does not have.
- The renderer is intentionally minimal (Canvas2D). A richer renderer is a
  capability provider that ships separately.
- Pi integration requires Pi 0.81+ on PATH. If Pi is missing, the engine
  reports `AVAILABLE=false` and the harness keeps working on Hermes native
  alone.
- All credentials stay backend-side; the renderer only sees masked
  identities.

## License

Apache-2.0. See `LICENSE`.