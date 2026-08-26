# Project State

Concise checkpoint. Not a diary.

## Current phase

Phase 14 — Hardening / installer / doctor / docs. **MVP delivered.**

## Working

- Project tree at `D:\projects\meta-harness`.
- Backend Python plugin (`~/.hermes/plugins/meta-harness/`) installs cleanly
  with the host's plugin loader contract.
- Desktop plugin (`~/.hermes/desktop-plugins/meta-harness/plugin.js`) passes
  `node --check` and registers a `/meta-harness` route, sidebar nav row,
  palette commands, and a status chip.
- 21 unit tests pass (redaction, store, capabilities, characters, plugin
  lab, topology, engines, redaction bounds).
- Bootstrap wires up: SQLite + JSONL store, capability registry, two
  engines (hermes + pi), five built-in topologies, four role prompts,
  one default character pack with eight original SVG sprites.
- Plugin Lab: create version → validate → experimental activate → rollback
  flow tested.
- Installer / uninstaller / doctor for Windows + POSIX.
- Full documentation: ARCHITECTURE / DECISIONS / RECONNAISSANCE / PLUGINS
  / TOPOLOGIES / CHARACTER_PACKS / HERMES_INTEGRATION / PI_INTEGRATION /
  SECURITY / DEVELOPMENT.
- Real installation on this machine; doctor returns **PASS** with zero
  warnings.

## In progress

(nothing — MVP delivered)

## Failing

(nothing)

## Next (post-MVP)

1. Hermes gateway RPC wiring for `engine.hermes` so workers actually
   stream from `sessions.message`. The engine adapter is in place; the
   gateway shim (`_HOST_REQUEST`) is documented and gates everything on
   host availability.
2. Pi 0.81 RPC handshake integration test (requires a running Pi install).
3. Visual-review topology wiring for `vision.image` capability.
4. Plugin Lab promotion beyond pointer update (host-side loader contract
   for generated plugins).
5. Persona-style visual preset (jrpg-social-sim) as a separate renderer
   plugin.

## Important decisions

See `docs/DECISIONS.md`. Highlights:

- ADR-0001: no Hermes fork.
- ADR-0002: standalone desktop-plugin door.
- ADR-0003: SQLite + JSONL hybrid event store.
- ADR-0007: character packs are declarative assets only.
- ADR-0010: renderer never sees raw provider keys.

## Verification commands

```bash
# Tests
python tests/run_all.py

# Install (idempotent)
bash scripts/install.sh

# Doctor
bash scripts/doctor.sh

# Manual smoke test
hermes --version
pi --version
ls ~/.hermes/plugins/meta-harness/
ls ~/.hermes/desktop-plugins/meta-harness/
```

## Honest limitations

- The `engine.hermes` adapter requires the host to expose a gateway RPC
  shim that the v0.20.4 build does not surface to in-process plugins.
  When the shim is absent (every current host build), the adapter
  reports `available=false`; topologies still execute against `engine.pi`
  when Pi is available, otherwise the run fails with a clear "engine
  unavailable" message rather than a hang.
- The desktop plugin UI renders correctly only when Hermes Desktop is
  running with the plugin door enabled. The headless path (CLI / dashboard)
  has all backend functionality.
- No cryptographic plugin signing yet. The `integrity` field reserved by
  the runtime loader is unused in MVP.