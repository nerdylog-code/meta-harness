# Storage & replay — the canonical store (WP-004)

**Status:** implemented and green. Supersedes v1's dual SQLite+JSONL design (CONFLICTS C1,
ADR-0003). Code: `apps/daemon/metaharness/store/`, migrations in
`apps/daemon/metaharness/migrations/`, export in `apps/daemon/metaharness/export/`,
reconciliation in `apps/daemon/metaharness/reconcile/`.

---

## 1. What is canonical

| Thing | Where it lives | Who may write it |
|---|---|---|
| The event log | SQLite `events` | `Store.append` only, one transaction per event |
| Projections (`runs`, `artifacts`) | SQLite | the projection that owns the table, inside that same transaction |
| Artifact payloads | filesystem, content-addressed | `store.artifacts`, never the database |
| Migration history | SQLite `schema_migrations` | the migration runner |
| JSONL | a file produced by `metaharness.export` | nobody's authority; delete it and nothing changes |

One source of truth, by construction: every state change is an event, and an event and its
projections commit together or not at all.

## 2. The log

`events` carries exactly the 14 frozen envelope keys of ADR-0017 plus one derived column,
`namespace`, so per-namespace queries stay cheap without parsing JSON.

**Append-only is a property of the schema.** Two triggers abort any `UPDATE` or `DELETE` on
`events`; a correction is a new event. The store exposes no delete/update/rewrite API at
all, and `tests/unit/store/test_append_only.py` asserts both facts — including that the
protection lives in a migration file, so weakening it requires a new migration whose hash
is recorded.

**`seq` belongs to the store.** It is assigned as `MAX(seq)+1` *inside* the same
`BEGIN IMMEDIATE` transaction that inserts the row, which makes it monotonic and gapless
per store instance. It is deliberately not `AUTOINCREMENT`: a rolled-back append must not
burn a number, and the tests assert exactly that. Callers build events with any placeholder
`seq`; `append` replaces it.

**Appends are idempotent by event id.** An adapter that reconnects and re-sends gets the
stored event back with `inserted=false` — no second projection pass, no `seq` movement, no
duplicate row. This is the one behaviour every future adapter depends on, so it is tested
directly rather than assumed.

## 3. Transactions

`db.transaction()` opens `BEGIN IMMEDIATE` — the lock is taken when the transaction opens,
not when the first statement runs, so two writers can never interleave their reads of
`MAX(seq)`. Nested use joins the outer transaction instead of opening a second one, because
SQLite has no real nesting and a projection that writes twice must still be atomic.

A projection that refuses an event raises; the append then rolls back **including the
event**, which is what makes "an event whose projection failed" unrepresentable rather than
merely discouraged (A2). The refusal is translated into `ProjectionError` carrying the
projection, the kind, the seq and the id.

## 4. WAL, `synchronous`, and what A7 actually proves

WAL mode with `synchronous=NORMAL`. The honest contract that buys:

* the database is never left corrupt;
* there is never a partially-written event;
* the **tail** of the log may be lost in a hard crash or power cut.

`FULL` would add an fsync per append and shrink that window to zero. That trade is stated
here rather than assumed, and A7 asserts the bound instead of a wish: after
`kill_tree` mid-append (uncatchable — `SIGKILL` on POSIX, `TerminateProcess` on Windows),
the store must reopen, pass `PRAGMA integrity_check`, keep a gap-free `seq`, and have
projections that still equal a fresh replay of the log. What it must *not* do is claim that
nothing was lost; a test that asserted that would be lying about WAL.

## 5. Artifacts

`put_artifact` hashes the payload, writes it under `<artifacts>/ab/<sha256>` via a
temporary file and `os.replace`, then emits `artifact.created`; the projection writes the
index row in the same transaction. Consequences:

* **no blob column** — asserted by inspecting the declared column types, and by storing
  5 MiB and checking the database file stays small (measured: 4 KiB);
* identical bytes are stored once (content addressing) while each registration is its own
  row;
* a crash between the file write and the append leaves an unreferenced file, never a row
  pointing at a missing file;
* reads verify the content address, so a corrupted artifact is reported as corrupt rather
  than returned as bytes.

The artifact id travels in the payload, not the envelope: the envelope's id fields are the
frozen 14 and include no artifact slot. That is a deliberate property of ADR-0017.

## 6. Migrations

`NNNN_name.sql`, contiguous from 1, applied in one transaction each, hash recorded on
apply and re-checked on every later open. Editing an applied migration, deleting it, or
renaming it aborts startup **before anything executes**; a failure inside a migration rolls
back the whole thing, so a half-migrated schema cannot exist. Bookkeeping lives in
`schema_migrations` (version, name, sha256, applied_at, duration_ms).

`executescript` is not used: it commits any pending transaction before running, which would
silently destroy the "one transaction per migration" rule. Statements are split and run one
at a time inside our own transaction by `split_statements`, which understands trigger
bodies (`BEGIN … END`), string literals that contain `;`, escaped quotes and `--` comments.
The splitter has its own tests, including one over the real migration files.

Ship check (measured, not assumed): `uv build --wheel` puts all three `.sql` files inside
the wheel (`metaharness/migrations/…`), so a packaged daemon is not migration-less.

## 7. Replay equivalence

`replay_into(path)` appends the whole log into a fresh store, in `seq` order, and compares
the two stores with one sha256 over a canonical, sorted snapshot of every projection table.
`replay_equivalence_check()` is the same thing on a temporary store, for the acceptance
command.

Measured on the generated stream used by A3 (10 007 events with interleaved namespaces and
reused run ids): appended in ~2.4 s, replayed in ~2.2 s, identical digest. The target store
is opened with `emit_open_event=False` on purpose — otherwise the target's own boot event
would shift every following `seq` and the comparison would prove less than it claims.

## 8. Boot reconciliation is *not* in the store

The Architect's boundary, kept literally:

```
EventStore      records and replays the truth that was persisted
BootReconciler  reads it, asks the outside world, appends a corrective event
```

`metaharness.reconcile.BootReconciler` takes a `ProcessProbe` (psutil-backed in production,
injectable in tests), finds runs left in `running` whose pid is gone, and emits
`run.interrupted` with `orphaned=true` — a **new** event. The projected state becomes
`orphaned` and carries the reason, while the original `run.started` event is untouched: the
log only grows. It never resumes anything (BOOK §83), it never writes to a projection
directly, and a leaked `psutil` call inside the store is impossible because the store has
no such import.

Two honest details:

* A run whose process is genuinely alive is left alone and reported in `skipped_alive`.
  Guessing there would be worse than doing nothing.
* `leases_released` is reported as `0` with a note in the payload: the lease surface
  arrives with the worktree feature (BOOK §26/§28). Reporting a number we do not have would
  be fake green.

## 9. Acceptance results (WP-004)

| # | Test | Where | Result |
|---|---|---|---|
| A1 | Append + ordered range reads | `tests/unit/store/test_append_and_seq.py` | pass |
| A2 | Transactional projection (rollback covers event *and* projections) | `tests/unit/store/test_transactional_projection.py` | pass |
| A3 | Replay equivalence on ≥10 000 generated events | `tests/integration/replay/test_replay_equivalence.py` | pass |
| A4 | Restart persistence with no manual step | `tests/integration/replay/test_restart_and_crash.py` | pass |
| A5 | Migration order, immutability, atomicity, tamper rejection | `tests/unit/store/test_migrations.py` | pass |
| A6 | Artifact externalization, no blob, sha256 recorded | `tests/unit/store/test_artifacts.py` | pass |
| A7 | Crash safety under a hard kill mid-append | `tests/integration/replay/test_restart_and_crash.py` | pass |
| A8 | JSONL export derived, lossless, deletable | `tests/unit/store/test_export_jsonl.py` | pass |
| A9 | Reconciliation marks a stale run and records why | `tests/integration/replay/test_reconciliation.py` | pass |
| A10 | A1–A9 on Linux **and** Windows | CI matrix | see CI |

## 10. Honest limitations

* **`Store` is synchronous.** SQLite is an embedded synchronous engine and pretending
  otherwise would hide where the blocking happens; the async surface
  (`replay_equivalence_check`, and the daemon wiring to come) runs it on a worker thread.
  A long replay therefore occupies a thread, not the event loop.
* **One writer per store handle.** A `Store` owns one connection and serialises access with
  a lock. Multiple processes are supported by SQLite (WAL) but each needs its own handle,
  and only one may write at a time — which is the intended model for a local daemon.
* **Only two projections exist** (`runs`, `artifacts`) because only those have features and
  acceptance tests. Missions, tasks, sessions, approvals, capsules and leases arrive with
  their own work packages; an empty speculative table would be untested dead weight
  (WP-004 risk row).
* **Heartbeats do not touch the `runs` projection.** A liveness tick is not a lifecycle
  transition; tracking liveness per run arrives with the heartbeat feature (BOOK §24,
  PHASE 15). `heartbeat.*` events are persisted and ignored by the projection today, and a
  test pins that behaviour.
* **No retention policy.** The log grows forever. Compaction/archival of old events is not
  in scope for this package; the JSONL exporter is the backup story until then.
* **The daemon still uses its own in-memory event plane** (`apps/daemon/metaharness/events.py`,
  from WP-002) rather than this store. Wiring the API to the store is the next slice and is
  deliberately not part of WP-004, whose scope is the storage kernel. Until then the two
  envelopes coexist, and the daemon's hand-rolled `CanonicalEvent` is a duplicate of the
  frozen contract that must be deleted rather than maintained.
* **`synchronous=NORMAL`** means a power cut can lose the log's tail (see §4).
