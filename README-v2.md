# Meta-Harness v2 — control plane (branch `v2/control-plane`)

A local-first control plane for heterogeneous AI agents. An agent has an
**identity** that outlives the runtime, model and session that execute it; the
control plane owns missions, tasks, workspaces, context and accounting, and
treats Pi, Hermes, OpenClaw and OMP as adapters.

> **Status: WP-002 skeleton.** The daemon boots, answers HTTP, streams canonical
> events over a WebSocket and lays out its data directory on Windows and Linux.
> There are no runtime adapters yet, no storage, no missions and no UI — the
> first vertical slice (BOOK §80/§116) is WP-002 → WP-003/004/005 → the Pi
> adapter. Nothing in this README claims more than that.

## What exists today

| Piece | State |
|---|---|
| Daemon (`apps/daemon`) | `GET /health`, `GET /version`, `GET /v1/events`, `WS /v1/events/ws`, loopback-only guard |
| Event plane | canonical envelope (BOOK §13), provenance required, bounded in-memory ring |
| Layout | `platformdirs` data root + explicit subdirs; repo root *discovered*, never assumed |
| Scripts | `python scripts/dev.py \| test.py \| doctor.py \| package.py` — no Bash required |
| Tests | v1 regression suite (21) + v2 unit + v2 integration (real server, real websockets) |
| CI | `ubuntu-latest` + `windows-latest` (`master`-time CI was Linux-only) |
| Web UI (`apps/web`) | not built yet — WP-006 |
| Desktop shell | not built yet — WP-007 |

The previous, Hermes-hosted MVP is frozen at tag **`v0.1-hermes-hosted`** and
remains installable and documented in its own tree (`hermes-plugin/`,
`desktop-plugin/`, `docs/`). It is kept, not deleted.

## Quick start

```bash
uv sync                                   # create the environment from the lockfile
python scripts/doctor.py                  # diagnose: python, deps, layout, port, toolchain
python scripts/dev.py                     # run the daemon (opens a browser unless --no-browser)
python scripts/test.py                    # v1 regression + v2 unit + v2 integration
```

Windows (PowerShell) — identical, without Bash or WSL:

```powershell
uv sync
python scripts\doctor.py
python scripts\dev.py --no-browser
python scripts\test.py
```

Then:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/version
```

## Layout

```
apps/daemon/metaharness/     the daemon package (app, events, paths, version)
apps/web/                    the web UI            (WP-006)
apps/desktop/                the Tauri shell       (WP-007)
packages/contracts/          domain contracts      (WP-003)
packages/{plugin-sdk,ui-kit,benchmark-spec}/
plugins/runtimes/            runtime adapters      (after WP-003/004/005)
scripts/                     dev / test / doctor / package (Python, cross-platform)
tests/{unit,integration,contracts,e2e}/
docs/architecture/           inventory, conflicts, DAG, work packages, this design
docs/adr/                    decision records (see docs/adr/README.md)
```

## Documentation

| Document | What it answers |
|---|---|
| `docs/architecture/ARCHITECTURE.md` | what the skeleton is, what it deliberately is not, and the gaps it leaves open |
| `docs/architecture/CROSS_PLATFORM.md` | the Windows/Linux contract: paths, processes, ports, signals |
| `docs/architecture/V1_RECON_REPORT.md` | the §99 reconnaissance: inventory, evidence, WPs, blockers |
| `docs/architecture/V1_CONFLICTS.md` | every conflict between the old code and the Project Book |
| `docs/architecture/WP_DAG.md` | dependency DAG, waves, model allocation |
| `docs/work-packages/` | WP-001 … WP-007, each with real acceptance commands |
| `docs/adr/` | ADR-0001 (control plane), 0003 (SQLite canonical), 0014 (structured protocols), 0016 (Hermes transport) |

`PROJECT_BOOK.md` (the product/architecture book) is the highest authority for
intent; ADRs settle the decisions it left open; `PROJECT_STATE.md` records where
the work actually is.

## Development rules that matter here

- **No core feature may require Bash or WSL.** Every command above runs from
  PowerShell and from a POSIX shell.
- **No `shell=True`.** Child processes are `exec(["argv"], cwd=…)`.
- **No `__file__` arithmetic for installed content.** Assets come from the data
  root; a source checkout is discovered, not assumed (this is the v1 D1 defect).
- **Events carry provenance.** `measured` never masquerades as `estimated`, or
  the other way round.
- **Failures are visible.** Unsupported capabilities and degraded modes are
  reported, never silently substituted.
- **Validate workflows locally before pushing.** A malformed workflow fails at
  0 s with "workflow file issue" and no job output — the least diagnosable
  failure GitHub produces. `actionlint .github/workflows/` catches it in a
  second (context availability, expression syntax, action inputs).

## License

Apache-2.0 (see `LICENSE`). The v1 code, its tests and its character assets in
this repository are original work.
