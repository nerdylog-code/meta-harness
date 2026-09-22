# Reconnaissance — actual installed Hermes Agent

> Findings that affect Meta-Harness implementation. This is a historical reconnaissance
> snapshot; paths are intentionally environment-neutral. Re-run the commands below
> against the target host before relying on a version or plugin detail.
> The audit host used for this portfolio currently reports Hermes Agent `0.21.3`.

## Versions discovered

| Component | Version | Path |
|---|---|---|
| Hermes Agent | `0.20.4` (historical snapshot) | `$HERMES_HOME/hermes-agent` |
| Python (venv) | `3.11.15` (historical snapshot) | `$HERMES_HOME/hermes-agent/venv/` |
| OpenAI SDK | `2.24.0` | inside venv |
| Pi Coding Agent | `0.81.1` (historical snapshot) | `$APPDATA/npm/pi` |
| Hermes Home | environment-specific | env `HERMES_HOME` |
| Hermes Dashboard data dir | environment-specific | env `APPDATA/hermes` |

`hermes` is expected to be on `PATH`; use `hermes --version` to verify the active installation.

## Hermes plugin contract (real)

A user plugin lives at `~/.hermes/plugins/<id>/` and ships:

1. `plugin.yaml` — required; `kind: standalone`, optional `manifest_version: 2`,
   `provides_hooks: [...]`, `config_schema: {...}`.
2. `__init__.py` — must export `register(ctx)`; receives a `PluginContext`
   (`register_tool`, `register_hook`, `get_config`, …).

The currently installed plugin set:

```
orca-status                 — observes Hermes session/lifecycle events
phase11-slice1a-shadow      — pre_api_request / post_api_request shadow recorder
hermes-achievements         — historical
obsidian-preflight          — preflight skill
```

## Hermes dashboard plugin contract

For a plugin to expose HTTP endpoints to a desktop/dashboard UI, ship **also**:

```
<plugin>/dashboard/manifest.json
<plugin>/dashboard/plugin_api.py
```

`manifest.json` (kanban is the canonical example):

```json
{
  "name": "meta-harness",
  "label": "Meta-Harness",
  "description": "...",
  "icon": "Package",
  "version": "1.0.0",
  "tab": { "path": "/meta-harness", "position": "after:kanban" },
  "entry": "dist/index.js",
  "css": "dist/style.css",
  "api": "plugin_api.py"
}
```

`plugin_api.py` exposes a FastAPI `APIRouter()` which is mounted at
`/api/plugins/<id>/`. Auth: routes inherit the dashboard session-token
middleware; the WebSocket uses the same gate (`?token=` or `?ticket=`).

The kanban plugin confirms this exact shape — we follow it.

## Hermes Desktop plugin SDK (real)

Imports allowed for the renderer-side plugin.js (verbatim from the runtime
loader `apps/desktop/src/contrib/runtime-loader.ts`):

```js
import { ... } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
```

Anything else is rejected as "unsupported imports" before evaluation.

Two doors:

| Door | Path | Default posture |
|---|---|---|
| Standalone | `~/.hermes/desktop-plugins/<name>/plugin.js` | `defaultEnabled: true` |
| Unified (Python plugin + desktop half) | `~/.hermes/plugins/<name>/desktop/plugin.js` | `defaultEnabled: false` |

We use the **standalone** door so the install is hot-reloaded without flipping
the Python plugin to enabled. The Python backend is registered independently
under `~/.hermes/plugins/meta-harness/`.

## Lifecycle hooks available (subset used)

From `VALID_HOOKS` in `hermes_cli/plugins.py` and observed usage:

- `pre_tool_call` / `post_tool_call` — per tool invocation
- `pre_llm_call` / `post_llm_call` — per model round
- `pre_api_request` / `post_api_request` / `api_request_error` — provider calls
- `pre_approval_request` / `post_approval_response`
- `on_session_start` / `on_session_end` / `on_session_finalize` / `on_session_reset`
- `transform_terminal_output` / `transform_tool_result`

## Native subagent delegation

Hermes already has a native `delegate_task` tool that spawns an isolated
subagent with its own context. The desktop plugin talks to the gateway via
`host.request(method, params)`; the methods we use are:

- `sessions.create` / `sessions.message` / `sessions.list` / `sessions.get`
- `skills.list`
- `config.get`

(Spelling per the gateway JSON-RPC; the runtime loader normalises them.)

## Capability model — what actually exists

The host-defined capability registry lives in
`hermes_cli/plugin_capabilities.py` — currently only **override** capabilities
(tools.override, llm.model_override, …). We do not need any of those for the
MVP; Meta-Harness never replaces a built-in tool or model provider. Meta-Harness
adds **its own** capability registry internally for agent-facing topology
needs; that is unrelated to the host gate.

## Capability list we will *register* (own registry)

Meta-Harness exposes an internal capability registry; providers register,
consumers request:

```
model.text                  model.reasoning            model.vision
agent.spawn                 agent.delegate             agent.cancel
filesystem.read             filesystem.write
terminal.execute            terminal.stream
git.status                  git.worktree               git.commit
browser.observe             browser.control
vision.image                vision.screenshot
memory.recall               memory.store
validation.command          validation.test
ui.office                   ui.graph                   ui.timeline
artifact.render             artifact.preview
telemetry.events            telemetry.metrics
engine.hermes               engine.pi
```

These are user-facing semantic handles; the resolver binds them to provider
implementations registered at startup.

## Trust / safety boundary — actual

Hermes Desktop disk plugins run as **full app authority** in the renderer. The
isolation is *error* isolation (a broken plugin can be torn down, not crashed
app) — **not** security isolation. `integrity` is an SRI hash, not a sandbox.

Implication for Meta-Harness:

- Generated plugin code stays experimental; not promoted by default.
- The character-pack system is **declarative** (manifest + sprite URLs); no
  executable JS shipped from packs.
- The plugin backend never returns raw provider keys to the renderer.
- Redaction is applied to events before persistence.

## What we did NOT use

- Forked Hermes source.
- Hidden Hermes imports in the desktop plugin.
- A second websocket/gateway server.
- Custom DSL/grammar for topologies — topologies are plain YAML/JSON dicts.
- A separate executor reimplementing `delegate_task` — we go through it.

## Verification commands

```bash
hermes --version                  # re-check the active host version
pi --version                      # 0.81.1
hermes plugins list               # enumerates user + bundled plugins
```