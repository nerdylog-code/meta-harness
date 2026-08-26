# Hermes integration

The harness installs **beside** Hermes. We never fork it.

## What we touch

| Path | What we do |
|---|---|
| `~/.hermes/plugins/meta-harness/` | Python backend, dashboard API |
| `~/.hermes/desktop-plugins/meta-harness/plugin.js` | Desktop UI |
| `~/.hermes/config.yaml` | Add `plugins.entries.meta-harness` block; nothing else |
| `~/.hermes/meta-harness/` | Created on first run; stores SQLite, JSONL, packs, topologies |

A timestamped backup of `config.yaml` is left in `~/.hermes/backups/` so the
uninstall step is always reversible.

## Plugin contract (Python)

```python
# ~/.hermes/plugins/meta-harness/__init__.py

def register(ctx):
    # ctx is a PluginContext:
    #   ctx.get_config(key, default) -> str
    #   ctx.register_hook(name, fn)  -> None
    #   ctx.register_tool(name=..., description=..., parameters=..., handler=...) -> None
    ...
```

We read `ctx.get_config` and call `ctx.register_hook` /
`ctx.register_tool` only. We never monkey-patch other attributes; if a host
build doesn't expose them, the harness degrades gracefully.

## Plugin manifest

```yaml
name: meta-harness
version: "0.1.0"
kind: standalone
manifest_version: 2
provides_hooks:
  - pre_tool_call
  - post_tool_call
  - pre_api_request
  - post_api_request
  - on_session_start
  - on_session_end
config_schema:
  data_dir: { type: str, default: "" }
  ...
```

The host reads `provides_hooks` and routes the named events to our hook
functions. We do not require any capability grant.

## Hook payloads

We accept either positional-dict or keyword form. The payload we read:

| Hook | Fields read |
|---|---|
| `pre_tool_call` / `post_tool_call` | session_id, task_id, tool_name, args, result, duration_ms |
| `pre_api_request` / `post_api_request` | session_id, task_id, model |
| `on_session_start` / `on_session_end` | session_id, model, platform |

We never depend on a field that the host may not send. Missing fields fall
back to defaults; the runtime never crashes on a missing hook key.

## Dashboard plugin contract

```python
# ~/.hermes/plugins/meta-harness/dashboard/plugin_api.py

from fastapi import APIRouter
router = APIRouter()

@router.get("/status")
def get_status(): ...

# Anything else we expose lives in hermes_plugin.api.router.
```

The dashboard mounts this `router` at `/api/plugins/meta-harness/`. The
session-token middleware is inherited automatically. WebSocket auth
reuses the dashboard's `_ws_auth_ok` gate (kanban uses the same pattern).

## Desktop plugin contract

```js
// ~/.hermes/desktop-plugins/meta-harness/plugin.js

import { ... } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

export default {
  id: 'meta-harness',
  name: 'Meta-Harness',
  defaultEnabled: true,
  register(ctx) {
    ctx.register({ id, area, ... })  // statusBar | PALETTE_AREA | SIDEBAR_NAV_AREA | ROUTES_AREA | ...
  }
}
```

Imports are restricted to `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`
(verified by the runtime loader in `apps/desktop/src/contrib/runtime-loader.ts`).
Everything else is rejected as "unsupported imports" before evaluation.

The standalone door ships `defaultEnabled: true` so the user does not have
to flip it on after install. The unified door (Python plugin + desktop half)
ships `defaultEnabled: false` because the Python plugin starts inert until
the user allowlists it.

## Model-facing tools

The backend registers a small, deliberate tool surface:

| Tool | Purpose |
|---|---|
| `harness_inspect` | Read capabilities, engines, topologies, runs, plugins, characters |
| `harness_run` | Start a topology run |
| `harness_status` | Snapshot of a run + recent events |
| `harness_cancel` | Cancel a run |
| `harness_capabilities` | What the harness can do right now |
| `harness_plugins` | Plugin Lab inventory |
| `harness_topology_list` | List available topologies |
| `harness_artifact_get` | Pull one artifact |
| `harness_plugin_lab` | Inspect/create/validate/activate/rollback model-authored plugins |

Adding a tool is allowed only when two consecutive workflows need it.

## Plugin storage

The backend stores runtime state in `~/.hermes/meta-harness/`:

```
meta-harness/
├── harness.db                # SQLite (WAL)
├── harness.db-wal            # WAL log
├── events.jsonl              # append-only normalized events
├── character-packs/default/  # seeded built-ins
├── topologies/*.yaml         # seeded built-ins
├── roles/*.md                # seeded built-ins
├── artifacts/<aid>.<ext>     # on-disk artifact files
└── generated/<id>/v<N>/      # plugin lab versions
```

## Recap: zero-fork rule

We do not modify `~/.hermes/hermes-agent/`. The harness lives entirely
under `~/.hermes/{plugins,desktop-plugins,meta-harness}/`. If a future
Hermes change breaks one of our hooks, the fallback is to read the field
opportunistically (`info.get("field", default)`) — never to patch Hermes
core.