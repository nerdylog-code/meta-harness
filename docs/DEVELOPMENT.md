# Development

## Layout

```
meta-harness/
├── hermes-plugin/
│   ├── plugin.yaml
│   ├── __init__.py                 # register(ctx)
│   ├── dashboard/
│   │   ├── manifest.json
│   │   └── plugin_api.py
│   └── hermes_plugin/             # the actual runtime package
│       ├── paths.py
│       ├── redaction.py
│       ├── capabilities.py
│       ├── store.py
│       ├── characters.py
│       ├── plugin_lab.py
│       ├── topology.py
│       ├── api.py
│       ├── runtime.py
│       └── engines/
│           ├── base.py
│           ├── hermes.py
│           └── pi.py
├── desktop-plugin/
│   └── plugin.js
├── core/                          # reserved for future cross-cutting helpers
├── adapters/                      # reserved for non-engine adapters
├── topologies/                    # built-in topologies (YAML)
├── roles/                         # role prompts (markdown)
├── character-packs/default/       # default pack manifest + assets
├── scripts/
│   ├── install.sh / install.bat
│   ├── uninstall.sh / uninstall.bat
│   └── doctor.sh / doctor.bat
├── tests/
│   ├── test_core.py
│   └── run_all.py
└── docs/
```

## Running tests

```bash
python tests/run_all.py
```

or, if pytest is available:

```bash
python -m pytest tests/
```

The tests use a temp directory for `HERMES_HOME` and do not require any
external services.

## Iterating on the desktop plugin

The desktop plugin is hot-reloaded by Hermes Desktop when its
`plugin.js` changes. Save the file, ⌘K → **Reload desktop plugins**, done.

For a backend change, restart Hermes (or use `hermes dashboard` /
`hermes serve` and reconnect).

## Adding a topology

Drop a YAML file in `topologies/`. Restart the harness or trigger a
topology rescan. The runtime seeds any user-added topology into the data
dir on first boot.

## Adding a character

1. Drop a folder under `character-packs/<your-pack>/`.
2. `character-pack.json` with the v1 manifest schema.
3. `assets/*.{svg,png}`.
4. Trigger `POST /api/plugins/meta-harness/character-packs/reload`.

The harness never modifies your pack files. It only reads them.

## Adding a role

Roles are pure markdown. Drop a file in `roles/<id>.md`. The runtime
seeds it into the data dir. Roles are referenced by `role:` in topology
nodes; the engine adapter (Hermes or Pi) injects the markdown into the
system prompt.

## Adding an engine

Create `hermes_plugin/engines/<name>.py`:

```python
from .base import WorkerHandle

class FooEngine:
    name = "foo"
    description = "..."

    def available(self) -> bool: ...
    def default_model(self) -> str | None: ...

    async def start(self, spec): ...
    async def send(self, worker_id, message): ...
    def stream(self, worker_id): ...
    async def cancel(self, worker_id): ...
    async def close(self, worker_id): ...
```

Then register it in `hermes_plugin/engines/__init__.py` and in
`runtime.bootstrap()`. Add a capability `engine.foo` with provider
`foo`. Done.

## Plugin Lab dev loop

```bash
# Create an experimental plugin version
curl -X POST http://localhost:PORT/api/plugins/meta-harness/plugin-lab/create \
  -H "Content-Type: application/json" \
  -d '{"plugin_id":"my-plugin","manifest":{"id":"my-plugin","version":"0.0.1","provides":["capability.test"]},"code":"def hello():\n    return 1"}'

# Validate
curl -X POST http://localhost:PORT/api/plugins/meta-harness/plugin-lab/validate \
  -H "Content-Type: application/json" \
  -d '{"plugin_id":"my-plugin","version":"v0001"}'

# Activate
curl -X POST http://localhost:PORT/api/plugins/meta-harness/plugin-lab/activate \
  -H "Content-Type: application/json" \
  -d '{"plugin_id":"my-plugin","version":"v0001"}'

# Rollback (to v0001, since v0001 is already the oldest version)
curl -X POST http://localhost:PORT/api/plugins/meta-harness/plugin-lab/rollback \
  -H "Content-Type: application/json" \
  -d '{"plugin_id":"my-plugin"}'
```

The lab never auto-loads anything into the host. Promotion is a separate
out-of-MVP step.

## Where to look first

- Want to follow a run end-to-end? Start at `runtime.tool_harness_run`.
- Want to add a new event kind? Start at `runtime.hook_*` and the
  `EVENT_SCHEMA` in `engines/base.py`.
- Want to change the office view? Start at `desktop-plugin/plugin.js`,
  `OfficePanel` and `draw`.
- Want a new topology kind? Add it to `topology.execute()` and a YAML
  example in `topologies/`.

## Style

- Type hints where Python ≥ 3.10 syntax helps; not for show.
- Functions < 50 lines unless they have an obvious reason.
- No `print()` outside tests; use `logging.getLogger(__name__)`.
- No `import *`.
- No `try / except: pass`; narrow or re-raise.
- Comments explain WHY, not WHAT.

## Tests that should never be skipped

- redaction bounds (a regression could leak a credential)
- store round-trip (everything else depends on it)
- capability trust order (the resolver must prefer system over experimental)
- plugin lab create → validate → activate → rollback

If you change one of these four, run the full suite before committing.