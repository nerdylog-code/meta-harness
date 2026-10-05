# Project State

Concise checkpoint. Not a diary. (BOOK §111/§112.)

**Branch:** `v2/control-plane` · **Historical tag:** `v0.1-hermes-hosted` → `3ef4a5c` (immutable)
**Authority order:** `PROJECT_BOOK.md` → accepted ADRs → this file → the current work package → code → tests.

---

## Current phase

**WP-002 complete — the v2 control plane skeleton boots on both target platforms.**
**The CI matrix is green on `ubuntu-latest` and `windows-latest`** (run `37377861032`, all four suites on both OSes), which closes the WP-002 and WP-005 gates with measured evidence instead of a written contract. The remote branch is `v2/control-plane`; `master` and the tag `v0.1-hermes-hosted` are untouched.
WP-001, WP-002, WP-003 and WP-005 are closed with their gates met. Next: WP-004 (event store) and WP-006 (web shell), which are independent and can run in parallel.

| WP | State |
|---|---|
| WP-001 — Freeze V1 | **done** — tag on `3ef4a5c` byte-for-byte, branch `v2/control-plane`, bytecode untracked, dashboard tab retired |
| WP-002 — V2 repository skeleton | **done, gate closed** — daemon, event plane, layout, four cross-platform scripts, **two-OS CI green (ubuntu + windows)**, web placeholder |
| WP-005 — ProcessSupervisor | **done, gate closed** — the same suite passes on **Windows and Linux**; zero orphans verified on both (BOOK §79 gate) |
| WP-003 — Contracts foundation | **done and signed off** — 24 wire contracts, per-metric provenance, payload-bound approvals, `RuntimeAdapter` v2, `FakeRuntimeAdapter` + conformance suite, generated JSON Schema + TypeScript mirror in parity (113 tests, runs on both OSes) |
| WP-004 — Event store v2 | **next** — unblocked by the frozen contracts (ADR-0003, ADR-0017) |
| WP-006 / WP-007 — Web / Tauri | WP-006 unblocked (parallel with WP-004); WP-007 follows WP-006 |

## Working (verified in this checkout)

- **Daemon** `apps/daemon/metaharness`: `GET /health`, `GET /version`, `GET /v1/events`, `WS /v1/events/ws` (+ `/events/ws` alias), loopback-only guard, optional static mount of the web bundle.
- **Event plane**: canonical envelope with all 14 BOOK §13 keys; `kind` must be namespaced; `provenance.method` is required and validated against the Book's vocabulary; bounded ring replays the last 50 events to a late subscriber.
- **Layout**: `platformdirs` data root (`~/.local/share/MetaHarness` on Linux; `%LOCALAPPDATA%\MetaHarness` on Windows), 7 subdirectories, `METAHARNESS_DATA_DIR` override; repo root *discovered*, never assumed.
- **Scripts**: `python scripts/dev.py | test.py | doctor.py | package.py` — no Bash, no `shell=True`, works from PowerShell and POSIX.
- **Tests**: v1 regression 21/21 · v2 unit 27/27 · v2 integration 16/16 (real uvicorn server + real websockets client, plus a real process-tree suite, ~66 s total).
- **ProcessSupervisor** `apps/daemon/metaharness/process`: one interface, two OS implementations, pre-signal tree snapshot, verified kill (`orphan_check` inside the emitted event), bounded streams, wall-timeout budget. Design and the orphan bug it fixed: `docs/architecture/PROCESS_SUPERVISION.md`.
- **Web placeholder** `apps/web`: Vite + React + TS + TanStack Query; `pnpm typecheck` clean, `pnpm build` produces `dist/` and the daemon serves it (verified: `/health` reports the bundle, `GET /` returns index.html, the JS asset answers 200).
- **CI** `.github/workflows/ci.yml`: matrix `ubuntu-latest` + `windows-latest`, three suites, plus a web job gated on `apps/web/package.json`.

## Not yet built (explicitly)

No runtime adapter, no SQLite store, no missions/tasks/approvals, no context engine, no plugin kernel, no secrets broker, no channels, no voice, no RAG, no Tauri shell, no auth token enforcement (the enforced property today is **loopback-only**).

## Next

1. **WP-003 — contracts** (Architect sign-off required; ADR-0006 + the event-envelope ADR). Prerequisites already resolved: ADR-0003 (SQLite canonical), ADR-0014 (structured protocols), ADR-0016 (Hermes transport). This is the only remaining blocker before adapters.
2. **WP-004 — event store** starts only after WP-003 freezes.
3. **WP-005 on Windows** closes with the first CI run (needs push authorisation) — the POSIX half is green and the gate is the same suite on both OSes.
4. Then the first adapter (Pi) → the M1 walking skeleton (BOOK §75/§116).

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

---

## Historical: v1 (Hermes-hosted MVP), frozen at `v0.1-hermes-hosted`

Kept verbatim in intent, useful as reference; **not** the v2 line.

- Backend plugin `hermes-plugin/` (installs to `~/.hermes/plugins/meta-harness/`) and desktop plugin `desktop-plugin/plugin.js`; 21 unit tests; capability registry, topology executor, plugin lab, character packs.
- Known defects recorded in `docs/architecture/V1_INVENTORY.md` §7: built-in content never resolves after install (D1), installer copies no content (D2), tracked bytecode (D4, fixed on this branch), stale README (D8), permanently-unavailable Hermes engine (D7).
- The v1 dashboard tab is retired on this branch; its REST door (`dashboard/plugin_api.py`) is **live** and was deliberately kept (D3).
- v1 ADRs live in `docs/DECISIONS.md` and are superseded where they conflict with v2 (see `docs/architecture/V1_CONFLICTS.md`). They will be migrated to `docs/adr/v1/` when that rename is scheduled.
