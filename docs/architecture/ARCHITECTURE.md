# Architecture — v2 control plane (WP-002 skeleton)

Authority order: `PROJECT_BOOK.md` → accepted ADRs → `PROJECT_STATE.md` → the
current work package → code → tests → the most reasonable reversible choice.

This document describes **what exists and what is deliberately absent**. It is
not a roadmap; the roadmap is `WP_DAG.md` and `docs/work-packages/`.

## 1. Shape

```
                      scripts/dev.py  (canonical entry point)                             │
                             ▼
   ┌──────────────────────────────────────────────────────────┐
   │ apps/daemon/metaharness                                  │
   │                                                          │
   │  app.py      FastAPI surface + loopback guard            │
   │  events.py   durable event bus (publishes via the store) │
   │  store/      canonical SQLite log, projections, replay    │
   │  reconcile/  boot reconciliation (asks the outside world) │
   │  export/     derived JSONL export                         │
   │  paths.py    platformdirs data root + repo discovery      │
   │  version.py  identity (version, git sha, runtime)        │
   │  process/    ProcessSupervisor (one interface, two OSes) │
   └──────────────────────────────────────────────────────────┘
                             │
        ┌────────────────────┼─────────────────────┐
        ▼                    ▼                     ▼
   SQLite store         Runtime adapters      Web / desktop UI
   (WP-004, live)       (after WP-003)        (WP-006, WP-007)
```

The daemon is the **system of record** (BOOK §5.5). Anything the UI shows is a
projection of daemon state; the UI may never own domain state.

## 2. HTTP surface

| Method | Path | Meaning |
|---|---|---|
| GET | `/health` | liveness, identity, resolved data root, event counters, web-bundle presence |
| GET | `/version` | version, git sha/branch, Python, platform, `api_version` |
| GET | `/v1/events?limit=N` | bounded recent-event read (the polling fallback for the UI) |
| WS | `/v1/events/ws` | canonical live event stream (BOOK §64) |
| WS | `/events/ws` | alias kept so the WP-002 gate text and the Book agree |
| GET | `/` | the built web bundle, when one exists (mounted only then) |

Conventions that the rest of the system will rely on:

- **Loopback-only.** A pure-ASGI guard refuses every non-loopback peer on both
  HTTP and WebSocket scopes. Binding is `127.0.0.1`; remote access is a later,
  explicit, separately designed feature (BOOK §66).
- **A late subscriber is never blind.** The bus replays the last 50 events of the
  *durable* log on connect, so a fresh client sees `system.daemon.started` — including
  after a daemon restart, when memory would have been empty.
- **A slow subscriber is dropped, not allowed to stall the plane**, and the
  drop is observable through the subscriber count.

## 3. Event plane

The envelope is the frozen contract (`metaharness_contracts.CanonicalEvent`, ADR-0017) —
14 keys, verified by tests on both sides. There is exactly one envelope: the daemon's
WP-002 copy is deleted, and a test walks the daemon's source to keep it deleted.

The vocabulary is enforced where it belongs:

1. `kind` must be namespaced (`system.daemon.started`) — the contract refuses anything else.
2. `provenance.method` must be one of `measured`, `provider_reported`,
   `runtime_reported`, `estimated`, `unknown` — the bus refuses anything else.

Both raise rather than defaulting, because a silently-unlabelled measurement is
worse than a crash: every later benchmark claim (BOOK §51/§52) depends on this
label being true.

**Storage is here, and it is canonical.** `EventBus.publish` persists through
`metaharness.store` before delivering, so an event that was not written is never
announced; `/v1/events` and the websocket backlog are read from the log. The store assigns
`seq`, owns the schema, and is the only place state lives (ADR-0003, `STORAGE.md`).
Reconciliation of what a restart finds is `metaharness.reconcile`, deliberately outside the
store: the store records, the reconciler asks.

## 4. Layout and the D1 lesson

v1 resolved its built-in assets with `Path(__file__).resolve().parents[N]`,
which was correct inside the checkout and wrong in every installation
(`V1_INVENTORY.md` D1). v2 uses two mechanisms and no arithmetic:

- **user data** ← `platformdirs.user_data_dir("MetaHarness")`, overridable by
  `METAHARNESS_DATA_DIR` (tests, portable installs). Subdirectories: `config`,
  `data`, `cache`, `logs`, `plugins`, `workspaces`, `artifacts`.
- **repository root** ← `find_repo_root()` walks up looking for a `pyproject.toml`
  containing `name = "metaharness"`, and returns `None` when there is none. A
  checkout is an optional context; an installed wheel never pretends to have one.

`tests/unit/test_paths.py` contains a regression test that fails if the data
root ever starts being derived from source position again.

## 5. What this skeleton deliberately does NOT have

Stated plainly so nothing here is mistaken for done (BOOK §82 — no fake green):

| Missing | Arrives with |
|---|---|
| Auth token enforcement (BOOK §65) | WP-006 (UI session). Today the enforced property is *loopback-only*, nothing more |
| Durable storage, migrations, projections, replay | WP-004 |
| Missions, tasks, task graph, approvals, work graph | WP-006 and the mission phases |
| `RuntimeAdapter` and any runtime at all | WP-003 (contract), then the Pi adapter |
| Context engine, capsules, compaction, budgets | CONTEXT phases |
| Secrets broker, plugins, channels, voice, RAG | their phases |
| Web UI, desktop shell | WP-006, WP-007 |
| Structured/JSON logging, log shipping | when shipping exists — adding a logging dependency before then would be dependency inflation |

## 6. Testing

Three suites, all runnable with one command (`python scripts/test.py`):

- **`v1`** — the frozen MVP's 21 unit tests, kept as a regression asset. They
  must stay green: they are the only surviving executable description of v1's
  behaviour.
- **`unit`** — 27 tests: layout, provenance discipline, envelope shape vs the
  Book, HTTP surface, loopback guard. Nothing of ours is mocked.
- **`integration`** — 16 tests: a **real** uvicorn server on a free port in a
  thread with a real `websockets` client, plus the `ProcessSupervisor` suite,
  which spawns real process trees and independently verifies what survived
  (see `docs/architecture/PROCESS_SUPERVISION.md`). The WP-002 and WP-005 gates
  are impossible to satisfy honestly without this.

Suites live under `tests/{unit,integration}` **without `__init__.py`**, so v1's
own discovery (`tests/run_all.py`) keeps finding exactly what it found before.

## 7. Configuration

| Env var | Effect |
|---|---|
| `METAHARNESS_DATA_DIR` | override the data root (tests, portable installs) |
| `METAHARNESS_HOST` | bind host (default `127.0.0.1`; anything else is refused by the guard) |
| `METAHARNESS_PORT` | preferred port (default 8765; `dev.py` picks a free one if busy) |

No configuration file exists yet. It belongs with the config subsystem, not in
the skeleton's argument parser.
