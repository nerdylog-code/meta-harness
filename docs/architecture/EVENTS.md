# Event contract — envelope, namespaces, payload versioning

The envelope is `metaharness_contracts.CanonicalEvent` and its key set is asserted
against PROJECT_BOOK §13 by `tests/contracts/test_ids_events_serialization.py`.

## Envelope

```
id            evt_…        typed, opaque body
seq           int ≥ 1      monotonic in the canonical store (ADR-0003, WP-004)
ts            float        unix seconds, > 0
kind          str          "namespace.name", no whitespace, both sides non-empty
mission_id?   mis_…
task_id?      tsk_…
run_id?       run_…
agent_id?     agt_…
session_id?   ses_…
runtime_id?   rt_…
correlation_id?  groups the events of one logical operation
causation_id?    the event that caused this one
payload       object       MUST contain "v": int ≥ 1
provenance    object       how the payload was obtained
```

### Payload versioning

There is **no universal payload schema** (WP-003 decision 2). Each payload
declares its own version with `"v"`, validated at construction:

```python
CanonicalEvent.build("task.completed", {"task_id": "tsk_…", "proof": ["…"]}, …)
# payload becomes {"v": 1, "task_id": …} — the version is stamped if absent
```

`event.payload_version` reads it; `event.payload_body` returns the payload
without it. A new kind of payload may therefore change shape by bumping `v`
without touching the envelope, and a consumer that only understands `v: 1`
degrades explicitly instead of misreading a field.

### Provenance

`provenance` is a small open mapping. By convention it carries
`{"method": …, "origin": …}` where `method` is one of the vocabulary shared with
`UsageSample`: `measured`, `provider_reported`, `runtime_reported`, `estimated`,
`unknown`. Numeric claims inherit the same rule as metrics: an unmeasured value
must not be presented as zero.

### Unknown fields

The envelope is `extra="allow"`: an external producer that adds metadata does not
break the runtime, and the extra keys round-trip untouched. They are never
interpreted by the core.

## Namespaces (BOOK §13)

`system · runtime · mission · agent · task · run · session · message · tool ·
approval · workspace · artifact · context · memory · rag · heartbeat · usage ·
plugin · channel · voice · eval · benchmark`

A namespace outside this list is **not** an error: a plugin may introduce its own.
The list exists for documentation and drift diagnosis; a test asserts that every
namespace the Book names is present here.

## Kinds emitted so far

| Kind | Producer | Payload |
|---|---|---|
| `system.daemon.started` | daemon lifespan | version, git_sha, host, port, data_root |
| `system.daemon.stopping` | daemon lifespan | reason |
| `system.session.attached` | event stream | subscribers, backlog |
| `system.process.spawned` | ProcessSupervisor | pid, cwd, argv_count |
| `system.process.stdout` / `.stderr` | ProcessSupervisor | pid, bytes, truncated |
| `system.process.exited` | ProcessSupervisor | pid, returncode, signal, duration_ms, requested |
| `system.process.kill_tree` | ProcessSupervisor | root_pid, killed, escalated, survivors, escaped, **orphan_check** |
| `session.*`, `message.*`, `run.*` (fake runtime) | FakeRuntimeAdapter | session lifecycle, echoed messages, cancellation |

The supervisor's `kill_tree` payload carries `orphan_check` on purpose: the BOOK
§79 gate is proof of zero orphans, so the proof travels with the event rather
than living only in the test suite.

## Storage & replay (WP-004)

Durability arrived with WP-004: SQLite is now canonical and JSONL is a derived export
(ADR-0003). The store lives in `apps/daemon/metaharness/store/`, and the full contract —
transaction discipline, `seq` ownership, artifact externalization, migration rules, replay
equivalence and the reconciliation boundary — is in
[`STORAGE.md`](STORAGE.md).

Two things about the event plane that this changes:

- the store assigns `seq` (monotonic, gapless, inside the append transaction). The
  in-memory plane's own counter is no longer the reference the daemon will report;
- `system.store.opened`, `system.store.migrated`, `system.replay.completed` and
  `system.reconcile.completed` join the emitted kinds below.

The daemon is wired to this store: `EventBus.publish` persists through `Store.emit` before
delivering, `/v1/events` and the websocket backlog are read from the log (so a restarted
daemon shows real history), and boot reconciliation runs before the daemon announces itself.
WP-002's in-memory ring and its hand-rolled `CanonicalEvent` are gone; two tests keep the
duplicate envelope deleted.

## Consumers

- `GET /v1/events` returns the daemon's bounded ring — the polling fallback.
- `WS /v1/events/ws` streams the same envelope, replaying the last 50 events to a
  fresh subscriber so it can never look like a hang.
- `metaharness.store.Store` (canonical, WP-004): append, ordered range reads, projection
  snapshots, replay, export and boot reconciliation. See [`STORAGE.md`](STORAGE.md).
