# Meta-Harness

A local extension for Hermes Agent and Hermes Desktop that demonstrates
multi-agent orchestration, capability routing, declarative topologies, engine
adapters, an event plane, validation/retry, and controlled plugin experiments.
Hermes remains the host; Meta-Harness supplies the project-specific backend,
desktop surface, topology runtime, and plugin-lab behavior.

This is an engineering MVP, not a claim of AGI, autonomous self-improvement, or
security isolation beyond the host's own controls.

## What the repository implements

- **Backend plugin**: a FastAPI router and `register(ctx)` entry under
  `hermes-plugin/`, installed into the configured Hermes plugin directory.
- **Desktop plugin**: a React-compatible plugin that registers a workspace route,
  sidebar navigation, command-palette actions, and a status-bar chip.
- **Event plane**: SQLite WAL storage, JSONL tailing, and polling REST access.
- **Engine adapters**: Hermes-native gateway RPC and optional Pi JSON-RPC over
  stdio. Missing Pi is reported as unavailable rather than treated as a failure
  of the core harness.
- **Topology executor**: declarative YAML for solo, sequence, parallel, gate,
  and retry flows.
- **Validation runtime**: bounded retries and feedback results.
- **Capability resolver**: host-compatible capability registration and routing.
- **Character packs and office view**: declarative presentation driven by runtime
  events, with a fallback animation path.
- **Plugin Lab**: inspect, version, validate, experimentally activate, and roll
  back model-authored plugin candidates.
- **Install/doctor/uninstall scripts**: Hermes-home autodetection, config backup,
  and installation diagnostics.

## Verification status

| Check | Result | Boundary |
|---|---|---|
| Core Python suite | **Verified locally** — 21 tests passed | `python tests/run_all.py` |
| Desktop plugin loader simulation | **Verified locally** | `python scripts/check_plugin_load.py` registered 5 contributions |
| Desktop React/jsdom render probe | **Verified locally** | `python scripts/check_plugin_render.py` rendered HTML without exceptions; React emitted a non-blocking component-casing warning |
| Installer/doctor in isolated Hermes home | **Verified locally** — doctor returned `PASS with 1 warning` because the selected `python3` lacks optional FastAPI | Uses a temporary `HERMES_HOME`; never changes the user's real Hermes installation during this audit |
| Current Hermes host installation | **Not verified in this audit** | The plugin was not installed into the user's real Hermes home, so host startup and desktop loading remain an external integration step |
| Hosted CI | **Not configured in this repository** | Local checks are the available evidence; no hosted badge is claimed |

The render and loader probes validate the plugin boundary with shims. They do not
replace a full Hermes Desktop session.

## Install

Set `HERMES_HOME` to the Hermes home you intend to modify, or pass it as the
first argument. The installer creates a timestamped `config.yaml` backup before
patching only the required plugin entry.

```bash
# POSIX / Git Bash
bash scripts/install.sh "${HERMES_HOME:-$HOME/.hermes}"

# Windows batch alternative
scripts\\install.bat

# Diagnose the target installation
bash scripts/doctor.sh "${HERMES_HOME:-$HOME/.hermes}"
```

After installation, restart Hermes/Hermes Desktop and reload desktop plugins if
the navigation entry is not visible. The installer does not copy Python bytecode
caches and does not intentionally overwrite unrelated configuration keys.

## Use

1. Open Hermes Desktop.
2. Open **Meta-Harness** from the sidebar.
3. Choose a topology and role, enter a task, and press **Run**.
4. Inspect Agents, Timeline, Office, Characters, and Plugins.
5. Use Plugin Lab only for controlled, reversible experiments.

The visible workspace is an adapter to host events; it is not an independent
runtime or a replacement for Hermes permissions.

## Uninstall

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" bash scripts/uninstall.sh
```

The uninstall script removes the installed backend/desktop plugin directories and
reverts the config patch according to its documented behavior. Keep the generated
Hermes config backup until the installation has been checked.

## Project layout

```text
meta-harness/
├── hermes-plugin/        # Python backend installed into Hermes
├── desktop-plugin/       # Desktop plugin source
├── core/                 # capability registry, event plane, topology, validators
├── adapters/             # Hermes and Pi engine adapters
├── topologies/           # declarative YAML topologies
├── roles/                # role prompt packs
├── character-packs/      # default and sample packs
├── scripts/              # install, uninstall, doctor, load/render probes
├── tests/                # core behavior tests
└── docs/                 # architecture, decisions, security, plugins, reconnaissance
```

## Reproduce the local checks

```bash
python tests/run_all.py
python scripts/check_plugin_load.py
python scripts/check_plugin_render.py

# Optional isolated install test; use an absolute temporary Hermes home.
bash scripts/install.sh "$HERMES_HOME"
bash scripts/doctor.sh "$HERMES_HOME"
```

The render probe bootstraps `jsdom`, `react`, and `react-dom` into
`scripts/node_modules`; that directory is test tooling, not a runtime dependency
of Hermes.

## Boundaries and limitations

- Hermes Agent/Hermes Desktop provide the host lifecycle, permissions, and plugin
  loader. Meta-Harness does not claim to replace or strengthen those controls.
- The renderer is intentionally minimal Canvas2D presentation code.
- Pi integration requires a compatible `pi` executable on `PATH`; the core
  harness continues with Hermes-native execution when Pi is unavailable.
- The event store and polling API are local components; no distributed event bus,
  hosted control plane, or production HA claim is made.
- Credentials remain backend-side and the renderer receives masked identities.
- Model-authored plugin activation is explicitly experimental and should remain
  behind validation and rollback controls.
- A full host integration test requires the user's Hermes installation and an
  intentional restart; it was not performed automatically here.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
