# Architecture

```
+------------------------------------------------------------+
|                    Hermes Agent (host)                      |
|                                                            |
|  +----------+   +-------------------+   +----------------+ |
|  |  CLI     |   |  gateway (JSON-RPC)|   | desktop (Electron) |
|  +----------+   +-------------------+   +----------------+ |
|         \              |                /                   |
|          \             |               /                     |
|   +-------v-------------v--------------v-------+             |
|   |  ~/.hermes/plugins/<name>/  (Python)       |             |
|   |  + plugin.yaml                             |             |
|   |  + __init__.py  (register(ctx))            |             |
|   |  + dashboard/manifest.json                 |             |
|   |  + dashboard/plugin_api.py                 |             |
|   +------|------------------|------------------+             |
|          |                  |                                |
|          |   meta-harness plugin                             |
|          |   - capability registry                           |
|          |   - event store (SQLite WAL + JSONL)              |
|          |   - engines.hermes / engines.pi                   |
|          |   - topology executor                             |
|          |   - validation runtime                            |
|          |   - plugin lab                                    |
|          |   - character packs                               |
|          v                                                   |
|   /api/plugins/meta-harness/*                               |
|          ^                                                   |
|          |  (ctx.rest + ctx.socket)                          |
|   +------|------------------|------------------+             |
|   |  ~/.hermes/desktop-plugins/<name>/ (JS)    |             |
|   |  + plugin.js (defaultEnabled: true)        |             |
|   +---------------------------------------------+            |
+------------------------------------------------------------+
```

## Layers

| Layer | Lives in | Owns |
|---|---|---|
| Host | Hermes Agent | sessions, tools, model calls, plugin lifecycle |
| Backend | `~/.hermes/plugins/meta-harness/` | orchestration, event store, engine adapters, plugin lab |
| Desktop | `~/.hermes/desktop-plugins/meta-harness/plugin.js` | UI, polling, projection |

The desktop never reaches into the backend process. Communication is via the
plugin-scoped REST API (`/api/plugins/meta-harness/*`) and a WebSocket with
polling fallback.

## Why "mechanisms in the kernel, policy in plugins"

The backend contains:

- mechanism: capability registry, event store, topology executor, engine
  adapters, plugin lab, character pack registry.

And never hard-codes:

- policy: routing heuristics (left to the topology chooser), validator
  gates (left to role prompts), visual rules (left to character packs),
  fusion strategy (left to topology YAML), model selection (left to engine
  config).

The Plugin Lab is the escape hatch: when a missing mechanism becomes a real,
blocking need, the model can author an experimental plugin without
overwriting the trusted one.

## Capability resolution

```
consumer:
    request: validation.test
resolver:
    picks:   local-subprocess  (trust=system, available=True)
```

Providers register at bootstrap. The resolver picks the highest-trust
available provider per request. Trust order:
`system > trusted-installed > user-authored > model-generated > experimental > quarantined`.

## Event plane

Normalized events live in:

- `events.jsonl` — append-only, full payload, replay source of truth.
- `event_index` SQLite table — `(id, run_id, agent_id, kind, ts)` for cheap
  queries; one row per JSONL line, in the same transaction.

The renderer never sees raw events directly — it subscribes to React Query
snapshots keyed on `(run_id, since_id)`. A separate WebSocket pushes an
`id` cursor to keep the renderer near-real-time.

## Engine abstraction

```python
class Engine(Protocol):
    name: str
    def available(self) -> bool: ...
    def default_model(self) -> str | None: ...
    async def start(self, spec: dict) -> WorkerHandle: ...
    async def send(self, worker_id, message) -> None: ...
    def stream(self, worker_id) -> AsyncIterator[dict]: ...
    async def cancel(self, worker_id) -> None: ...
    async def close(self, worker_id) -> None: ...
```

The topology executor talks to this interface only. Adding a new engine
(Claude Code, Codex, Kimi, local llama) is one registration.

## Topology executor

Topologies are declarative YAML:

```yaml
name: gate-build
version: "1"
kind: gate
nodes:
  - { id: builder,    type: agent, role: builder,   engine: hermes }
  - { id: validator,  type: agent, role: validator, engine: hermes }
edges:
  - { from: builder, to: validator }
limits: { retries: 3 }
```

`kind` selects the executor:

- `solo` — one node.
- `sequence` — topologically ordered chain.
- `parallel` — concurrent dispatch, results merged into the run context.
- `gate` — `builder -> validator` with retry caps and feedback loop.

## Validation

The validator role is logically separated from the builder. The gate runtime
keeps the validator's verdict as the sole criterion. Retry caps default to 3
and are configurable per topology.

## Plugin Lab

```
plugins/generated/<id>/v0001/
plugins/generated/<id>/v0002/
plugins/generated/<id>/current.json     # pointer
```

Every version is immutable. `current` is updated by `activate_experimental`;
`rollback` rewinds it. Promoting a generated plugin into the trusted
namespace is intentionally out of MVP scope — promotion requires both a
reviewer signature and the host's plugin loader permission, neither of
which the harness grants to itself.

## Character packs

A pack is a directory containing:

```
character-pack.json   # manifest
assets/*.svg           # original art
```

The runtime never executes pack code. Missing animations always fall back to
`working_file` then `idle`. The default pack ships with four procedural
SVG silhouettes (architect / builder / validator / reviewer) — no third-party
art, no Persona 4 / Atlus assets.

## Cancellation

Cancelling a run is recursive:

```
cancellable_root = asyncio task that owns the run
  -> engine.cancel(worker_id) on every owned engine
  -> topology executor receives CancelledError
  -> database row updated to status=cancelled
  -> JSONL event "run.cancelled" appended
```

Cancellation never touches unrelated Hermes sessions.