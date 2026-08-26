# Architecture Decision Log

Only irreversible/non-obvious choices. Each ADR carries a date, status, and a
short reason. Format inspired by ADR-0001 (record yourself).

---

## ADR-0001 — Hermes-first integration, no fork

**Status:** accepted · 2026-08-24

Hermes provides plugin surfaces (`~/.hermes/plugins/<name>/`,
`~/.hermes/desktop-plugins/<name>/`, `dashboard/manifest.json`) and lifecycle
hooks that cover every Meta-Harness need.

We refuse to fork Hermes. Every adaptation is contained in the plugin itself.

## ADR-0002 — Standalone desktop-plugin door, not unified

**Status:** accepted · 2026-08-24

The standalone door (`~/.hermes/desktop-plugins/meta-harness/plugin.js`) ships
`defaultEnabled: true`. The unified door (Python plugin + desktop half) starts
opt-in until `plugins.enabled` allowlists the Python half, which makes hot
reload of the desktop half awkward.

We accept the duplication of one folder name (`meta-harness` under both roots)
because the doors are clearly scoped and the integration is more reliable.

## ADR-0003 — SQLite + JSONL hybrid event store

**Status:** accepted · 2026-08-24

- SQLite (WAL mode): runs, agents, tasks, artifacts, plugin state, topology
  state, character assignments. Indexable, transactional, append-only
  effective through `task_events` with monotonic ids.
- JSONL (append-only): detailed normalized execution events for replay/export.
- Filesystem: large artefacts (logs, screenshots, generated artifacts).

Storing raw LLM tokens in SQLite is forbidden; the SQLite row references the
JSONL offset for replay.

## ADR-0004 — Capability-first, provider-registered

**Status:** accepted · 2026-08-24

Consumers request `validation.test`. Providers register
`validation.test -> local-test-runner`. Resolver picks by trust + availability.
The resolver is hot-swappable.

## ADR-0005 — Native Hermes worker = `delegate_task` (or sessions.create)

**Status:** accepted · 2026-08-24

We do not rebuild a subagent framework. The engine adapter for Hermes spawns
an isolated session via the gateway and tails its stream.

If `delegate_task` is unavailable in a future Hermes build, the engine falls
back to `sessions.create + sessions.message` polling — same event shape.

## ADR-0006 — Pi via structured RPC, not ANSI scraping

**Status:** accepted · 2026-08-24

Pi 0.81 supports `--mode rpc` (JSON-RPC over stdio) and `--mode json`. We use
the JSON mode and translate events into the Meta-Harness normalized schema.
We never scrape Pi terminal output.

## ADR-0007 — Character packs are declarative assets only

**Status:** accepted · 2026-08-24

A character pack is a manifest + sprite assets (PNG/JPG/JSON). No executable
JS, no remote URLs fetched at render time without an explicit user opt-in.
This matches the "no Persona-4-assets" rule and avoids shipping third-party
game art under our license.

## ADR-0008 — Model-authored plugin versions are immutable

**Status:** accepted · 2026-08-24

When the model creates a plugin in the Lab, it lands at
`~/.hermes/plugins/meta-harness/generated/<id>/v<N>/`. Promotion only updates a
`current -> vN` pointer. Rollback is pointer update. No version is destroyed.

## ADR-0009 — Single-writer invariant enforced by worktree isolation

**Status:** accepted · 2026-08-24

Two agents cannot write the same working tree. Parallel topologies default to
`git worktree add` per writer. The build/merge phase reconciles.

## ADR-0010 — Renderer never sees raw provider keys

**Status:** accepted · 2026-08-24

All `/api/plugins/meta-harness/*` endpoints that touch providers run on the
backend and return status + masked identities only. Redaction runs at the
event boundary, not the renderer boundary.

## ADR-0011 — Default rendering is Canvas2D + SVG, no Pixi.js

**Status:** accepted · 2026-08-24

The Hermes desktop disk plugin is intentionally restrictive (only
`@hermes/plugin-sdk` and `react*` imports). Canvas2D and SVG are the default.
A richer renderer can be added later behind a `renderer` capability provider.

## ADR-0012 — Topology-as-data with YAML manifests

**Status:** accepted · 2026-08-24

Topologies are declarative YAML. The executor is small and stable. New
topology kinds are added by writing YAML, not Python.

## ADR-0013 — Event throttling at the reducer, not the renderer

**Status:** accepted · 2026-08-24

Raw events arrive at ~ms cadence (terminal streaming). The reducer coalesces
into per-frame snapshots; the renderer subscribes to snapshots, not events.
This keeps the UI responsive without losing detail in JSONL.

## ADR-0014 — Self-modification is opt-in and reversible

**Status:** accepted · 2026-08-24

The Plugin Lab never overwrites the live runtime or trust policy without an
explicit user action (separate from chat). All generated artifacts are
quarantined until promoted. Rollback is one command.

## ADR-0015 — Hermes Home always respected; never assume `~/.hermes`

**Status:** accepted · 2026-08-24

`HERMES_HOME` env var is the source of truth. On Windows this is
`%LOCALAPPDATA%\hermes`. We resolve via `hermes_constants.get_hermes_home()`
when available, else `os.environ['HERMES_HOME']`, else the default — same
fallback order the rest of Hermes uses.