# WP-004 — Event store v2

**Owner:** Flash-class builder · **Wave:** W4 · **Depends on:** WP-003 (frozen contracts) · **Runs in parallel with:** WP-005

---

## Objective

Implement the daemon's storage kernel: a single canonical, append-only event log in SQLite (WAL), ordered, migrated, projected and replayable. This supersedes v1's dual SQLite+JSONL design (CONFLICTS C1) and produces the state every other v2 component reads from.

## Dependencies

WP-003 green: `CanonicalEvent`, the id types and the event catalogue are frozen and Architect-signed.

## Allowed files

```
apps/daemon/metaharness/store/**            (new — the deliverable)
apps/daemon/metaharness/migrations/**       (numbered, ordered, immutable once merged)
apps/daemon/metaharness/projections/**
apps/daemon/metaharness/export/**           (JSONL exporter — the compat path for C1)
tests/unit/store/**  tests/integration/replay/**
docs/architecture/EVENTS.md                 (append a "storage & replay" section)
docs/architecture/STORAGE.md                (new)
docs/adr/ADR-0003-sqlite-canonical-state.md
```

## Forbidden files

```
packages/contracts/**     (frozen by WP-003 — a needed change goes back to the Architect)
apps/web/**  apps/desktop/**
hermes-plugin/hermes_plugin/store.py      (v1's store stays frozen as reference)
```

## Required reading

`PROJECT_BOOK.md` §8 (event plane; SQLite is the system of record), §9 (storage/tables), §13 (event envelope + namespaces), §82 (display failures), §83 (recovery), §84 (migrations) · `docs/architecture/V1_INVENTORY.md` §5 (`store.py` row: what to reuse) · `docs/architecture/V1_CONFLICTS.md` C1 · v1 `store.py` (as a reference for WAL/`event_index` discipline, not as a base).

## Architecture constraints

1. **One source of truth.** SQLite is canonical. JSONL exists only as an export/replay *artifact* produced from SQLite (BOOK §8). No component may read JSONL as authority.
2. **Explicit SQL, numbered migrations, no ORM** (BOOK §4). Migration files are immutable after merge: a mistake is a new migration, never an edit (BOOK §84).
3. **`seq` is monotonic and gapless per store instance**; `id` is the ULID-style contract id. Every write is one transaction that also updates the affected projections — an event and its projection must not be able to diverge.
4. **Replay reconstructs projections.** `replay(events) == live_state` is a test, not a hope (BOOK §78 gate).
5. **Large payloads do not live in the database.** Artifacts are filesystem objects; the DB stores `id, path, sha256, mime, size, metadata, origin` (BOOK §9). A blob column is forbidden.
6. **Restart preserves state** — the daemon must reopen the store and resume without a manual step (BOOK §78 gate).
7. **Failures are visible** (BOOK §82): a migration that cannot run must abort startup with a clear message, never silently continue on a half-migrated schema.
8. **Startup reconciliation** (BOOK §83): on boot, mark orphaned `running` runs, check real processes, release stale leases, preserve artifacts, and never auto-resume a dangerous side effect.
9. The 22 table names of BOOK §9 are the target; the tables this package creates must be exactly the subset its §78 scope covers plus the event log — the rest arrive with their features, and creating empty speculative tables is a scope violation.

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Append + read | events append with monotonic `seq`; a range query returns them in order |
| A2 | Transactional projection | an event whose projection update fails is rolled back entirely — no orphan event, no orphan projection |
| A3 | **Replay equivalence** | replaying the log from empty reproduces byte-identical projections to the live database |
| A4 | **Restart persistence** | kill the daemon, restart, and the previous state is present with no manual migration step |
| A5 | Migration discipline | a fresh DB runs migrations `0001..N` in order; an already-migrated DB is a no-op; a tampered migration hash is rejected |
| A6 | Artifact externalization | a 5 MB payload lands on the filesystem with a recorded `sha256`; the DB row contains no blob |
| A7 | Crash safety | killing the process mid-write leaves the DB readable (WAL semantics) and no partially-applied event |
| A8 | JSONL export is derived | exporting produces a file whose contents re-import to the same state; deleting the export changes nothing |
| A9 | Reconciliation | a DB with a stale `running` run boots into a state where the run is marked orphaned, with the reason recorded as an event |
| A10 | Both OSes | A1–A9 pass on Linux **and** Windows CI (path and file-lock behaviour differ) |

## Expected output

The store package (connection/transaction layer, migration runner, event append + query, projection updater, JSONL exporter, startup reconciliation) with a passing replay-equivalence test, plus `docs/architecture/STORAGE.md` and the superseding ADR for C1.

## Expected events

Emitted by the store/kernel itself:

```
system.store.opened        { path, schema_version, migrations_applied }
system.store.migrated      { from_version, to_version, duration_ms }
system.replay.completed    { events, projections, duration_ms }
system.reconcile.completed { orphans_marked, leases_released, artifacts_preserved }
```

Consumed but not produced here: every namespace in BOOK §13 (the store persists them; it must not care what they mean).

## Windows requirements

- WAL mode on Windows: verify concurrent read during write, and that the daemon does not leave `-wal`/`-shm` files locked after shutdown (a known Windows file-handle trap).
- The data root comes from `platformdirs` (WP-002) — never a hardcoded path; `scripts\doctor.py` prints it.
- Killing the daemon in A7 must use a real Windows kill (no SIGTERM emulation) so the WAL test is honest.

## Linux requirements

- Same tests with POSIX signals; WAL files land in the platformdirs data root.
- A7 uses `SIGKILL` (uncatchable) to prove the WAL assumption rather than a graceful path.

## Exact acceptance commands

POSIX:

```bash
git checkout v2/control-plane
uv sync
python scripts/test.py --suite store
python scripts/test.py --suite replay
python -c "
import asyncio, metaharness_store as s
async def m(): print(await s.replay_equivalence_check())
asyncio.run(m())"                       # expect: {'equal': True, ...}
python scripts/doctor.py                # prints resolved data root + schema_version
```

Windows (PowerShell):

```powershell
git checkout v2/control-plane
uv sync
python scripts\test.py --suite store
python scripts\test.py --suite replay
python -c "import asyncio, metaharness_store as s; asyncio.run(s.replay_equivalence_check())"
python scripts\doctor.py
```

## Known risks

| Risk | Mitigation |
|---|---|
| Speculative tables (missions/agents/tasks/approvals/capsules) created "because the Book lists them" | Forbidden above: create a table when its feature lands, or an empty schema becomes untested dead weight |
| Replay test passing on a toy log but failing on real streams (thousands of events, out-of-order reads) | A3 must run against a generated log of ≥10 000 events with interleaved ids, not three fixtures |
| Windows file locking making A4/A7 flaky | Run those two tests in CI (A10), not only locally; a flaky gate is a failing gate |
| Silent half-migration on startup | Constraint 7 + A5's hash check |
| A builder "improving" v1's `store.py` instead of writing v2's | v1's store is forbidden; it is a reference for discipline, not a base |
