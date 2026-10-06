# Project State

Concise checkpoint. Not a diary. (BOOK §111/§112.)

**Branch:** `v2/control-plane` · **Historical tag:** `v0.1-hermes-hosted` → `3ef4a5c` (immutable)
**Authority order:** `PROJECT_BOOK.md` → accepted ADRs → this file → the current work package → code → tests.
`PROJECT_BOOK.md` now lives in the repository root (sha256 `d248ebbab24fcac2…`, 3145 lines), so
the first item of the authority order is readable from a clone instead of only from the owner's
machine. It is the owner's document, unedited.

---

## Current phase

**WP-004 complete — SQLite is the canonical store.** A single append-only event log with
migrations, transactional projections, artifact externalization, replay equivalence, a
derived JSONL export and boot reconciliation. A1–A9 pass locally (store 54 tests, replay 21
tests, ~7 s combined); A10 is the two-OS CI matrix.
**The CI matrix is green on `ubuntu-latest` and `windows-latest`** (run `37377861032`), which
closed the WP-002 and WP-005 gates with measured evidence instead of a written contract.
`master` and the tag `v0.1-hermes-hosted` are untouched.

| WP | State |
|---|---|
| WP-001 — Freeze V1 | **done** — tag on `3ef4a5c` byte-for-byte, branch `v2/control-plane`, bytecode untracked, dashboard tab retired |
| WP-002 — V2 repository skeleton | **done, gate closed** — daemon, event plane, layout, four cross-platform scripts, **two-OS CI green (ubuntu + windows)**, web placeholder |
| WP-005 — ProcessSupervisor | **done, gate closed** — the same suite passes on **Windows and Linux**; zero orphans verified on both (BOOK §79 gate) |
| WP-003 — Contracts foundation | **done and signed off** — 24 wire contracts, per-metric provenance, payload-bound approvals, `RuntimeAdapter` v2, `FakeRuntimeAdapter` + conformance suite, generated JSON Schema + TypeScript mirror in parity (113 tests, runs on both OSes) |
| WP-004 — Event store v2 | **done** — SQLite canonical (ADR-0003 implemented): 3 migrations, append-only log enforced by triggers, transactional projections, idempotent appends, content-addressed artifacts, replay equivalence on 10 007 events, JSONL export, boot reconciliation in its own package |
| WP-006 — Web shell | **done** — TanStack Router + Query, one websocket in context with an honest three-state connection, event inspector (live or durable backlog, labelled), mission/agents/system shells that state what does not exist yet; DOM verified in headless Chromium |
| WP-007 — Tauri shell | **after the M1 slice** — it packages something that already works |

## Working (verified in this checkout)

- **Daemon** `apps/daemon/metaharness`: `GET /health`, `GET /version`, `GET /v1/events`, `WS /v1/events/ws` (+ `/events/ws` alias), loopback-only guard, optional static mount of the web bundle.
- **Event plane**: the frozen contract (ADR-0017) with no local copy of it; `kind` must be
  namespaced; `provenance.method` is required and validated against the Book's vocabulary.
  The bus persists through the store before delivering, and the websocket replays the
  durable backlog to a late subscriber.
- **Store wiring**: the daemon opens the canonical store in its lifespan, runs boot
  reconciliation, and serves `/health` with the store's schema version, event count and
  journal mode. Verified end to end by launching the real daemon twice against one data
  root: `last_seq` 3 → 7, two `system.daemon.started` events in the history, no migration
  re-run on reopen.
- **Layout**: `platformdirs` data root (`~/.local/share/MetaHarness` on Linux; `%LOCALAPPDATA%\MetaHarness` on Windows), 7 subdirectories, `METAHARNESS_DATA_DIR` override; repo root *discovered*, never assumed.
- **Scripts**: `python scripts/dev.py | test.py | doctor.py | package.py` — no Bash, no `shell=True`, works from PowerShell and POSIX.
- **Canonical store** `apps/daemon/metaharness/store` (WP-004): SQLite WAL, three numbered migrations, `BEGIN IMMEDIATE` per append, `seq` assigned inside the transaction and gap-free, append-only enforced by triggers, idempotent by event id, content-addressed artifacts with no blob column, JSONL export that is derived only. One call proves replay: `uv run python -c "import asyncio, metaharness.store as s; print(asyncio.run(s.replay_equivalence_check()))"`.
- **Boot reconciliation** `apps/daemon/metaharness/reconcile`: reads persisted state, asks a `ProcessProbe`, appends `run.interrupted` with `orphaned=true`, and never resumes work. Deliberately outside the store.
- **Tests**: v1 regression 21/21 · v2 unit 27/27 · contracts 113/113 · **store 54/54** · integration 16/16 · **replay 21/21** (real server, real websockets, real process trees, real hard kills; the whole default run is ~80 s).
- **ProcessSupervisor** `apps/daemon/metaharness/process`: one interface, two OS implementations, pre-signal tree snapshot, verified kill (`orphan_check` inside the emitted event), bounded streams, wall-timeout budget. Design and the orphan bug it fixed: `docs/architecture/PROCESS_SUPERVISION.md`.
- **Web shell** `apps/web` (WP-006): Vite + React + TS + TanStack Query + TanStack Router (code-based routes), one websocket owned by a context provider, connection state that distinguishes live from degraded from connecting, an event inspector that always says whether it is showing the live socket or the durable backlog, and a sidebar that marks unbuilt surfaces as `soon` instead of linking to nowhere. Built bundle is served by the daemon with an SPA fallback; deep links work and path traversal is refused (403, verified with `curl --path-as-is`). DOM verified in headless Chromium: the shell renders, the badge reads `live`, and the log's events appear.
- **CI** `.github/workflows/ci.yml`: matrix `ubuntu-latest` + `windows-latest`, six suites (v1, unit, contracts, store, integration, replay), plus a web job gated on `apps/web/package.json`.

## Not yet built (explicitly)

No runtime adapter, no missions/tasks/approvals tables, no context engine, no plugin
kernel, no secrets broker, no channels, no voice, no RAG, no Tauri shell, no auth token
enforcement (the enforced property today is **loopback-only**), and the daemon does not yet
read or write through the store (WP-004 delivered the storage kernel; the wiring is the
next slice).

## Next

1. **The Pi wave (WP-015 → WP-019) and the M1 gate.** The protocol is no longer a guess: Pi
   `0.99.2` is installed here, its own `docs/rpc.md`/`docs/rpc-commands.md` are the spec, and two
   live probes confirmed both transports — `--mode json -p` for a one-shot event stream, and
   `--mode rpc` as a long-lived JSONL protocol (which produces *no* output when misused as a
   print mode). A real turn ran on provider `opencode-go` / model `kimi-k3` and reported
   per-message `usage` + `cost`, which map onto `UsageSample` as `provider_reported`.
   Spec: `docs/protocols/PI_RPC.md`. Packages: `docs/work-packages/WP-015…WP-020`.
2. **WP-007 — Tauri shell** (after the slice; it packages something that already works).

## Important decisions

- `docs/adr/README.md` is the index. Accepted: **ADR-0001** (independent control plane), **ADR-0003** (SQLite canonical, JSONL derived), **ADR-0014** (structured protocols only), **ADR-0016** (Hermes via ACP over stdio).
- Numbers reserved by BOOK Appendix A stay reserved, so no ADR reference is ever ambiguous.
- The parallel `~/projetos/harness-console` effort is Phase-0 evidence and an adapter source, not a competing trunk (ADR-0001 decision log).

## Verification commands

```bash
uv sync
python scripts/doctor.py                  # 0 pass / 2 warn / 1 fail
python scripts/test.py                    # v1 + unit + integration
python scripts/dev.py                     # daemon + web bundle on 127.0.0.1
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/version
git log --oneline --decorate -6
```

## Honest limitations

- The v2 suites run locally on Python 3.12; the v1 suite also passes under the Hermes venv interpreter (3.13). A bare 3.14 without PyYAML fails the v1 suite, which `scripts/test.py` now diagnoses with the interpreter path and the exact command to use.
- The web page's **DOM was not exercised in a browser** — only the HTTP delivery of the bundle was verified. Visual QA belongs to WP-006.
- `StaticFiles(html=True)` does **not** provide SPA fallback: an unknown path returns 404 rather than index.html. The router arrives with WP-006 and must add the fallback.
- Durability is absent by design: the event ring is in memory, so a restart loses events. WP-004 makes SQLite canonical (ADR-0003).
- `doctor.py` exits 2 when only warnings remain (missing pnpm/node/web bundle), which is information, not failure; CI calls it with `--report-only` because a runner is expected to be partial.
- CI prints two deprecation notices from third-party actions (Node 20 → 24) and one runner-label migration notice. Neither affects our code; the action versions are pinned and will be bumped deliberately.

- **`synchronous=NORMAL`.** A power cut can lose the tail of the log. What it cannot do is
  corrupt the database or leave a partial event, and the crash test asserts exactly that
  bound rather than "nothing was lost" (`docs/architecture/STORAGE.md` §4).
- **The daemon still speaks through its own in-memory envelope.** `apps/daemon/metaharness/events.py`
  (WP-002) carries a hand-rolled `CanonicalEvent` that duplicates the frozen contract, and
  the API does not yet read or write the store. That duplication is a defect to delete in
  the wiring slice, not a design.
- **The event log has no retention policy**, so it grows forever until compaction or
  archival is specified. The JSONL export is the backup story until then.
- **Boot reconciliation reports `leases_released: 0`** with an explanatory note, because the
  lease surface does not exist yet (it arrives with the worktree feature, BOOK §26/§28).
  Reporting a number we do not have would be fake green.

---

## Historical: v1 (Hermes-hosted MVP), frozen at `v0.1-hermes-hosted`

Kept verbatim in intent, useful as reference; **not** the v2 line.

- Backend plugin `hermes-plugin/` (installs to `~/.hermes/plugins/meta-harness/`) and desktop plugin `desktop-plugin/plugin.js`; 21 unit tests; capability registry, topology executor, plugin lab, character packs.
- Known defects recorded in `docs/architecture/V1_INVENTORY.md` §7: built-in content never resolves after install (D1), installer copies no content (D2), tracked bytecode (D4, fixed on this branch), stale README (D8), permanently-unavailable Hermes engine (D7).
- The v1 dashboard tab is retired on this branch; its REST door (`dashboard/plugin_api.py`) is **live** and was deliberately kept (D3).
- v1 ADRs live in `docs/DECISIONS.md` and are superseded where they conflict with v2 (see `docs/architecture/V1_CONFLICTS.md`). They will be migrated to `docs/adr/v1/` when that rename is scheduled.
