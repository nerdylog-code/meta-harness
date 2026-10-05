# ADR-0017 — Event envelope: fixed keys, versioned payloads, tolerant edges

**Status:** accepted (2026-10-05, WP-003 architectural sign-off) · **Decision owner:** project owner (Architect)
**Relates to:** ADR-0003 (SQLite canonical), PROJECT_BOOK §13 · **Numbering:** 0017 because Appendix A of the Book does not reserve a number for the envelope

---

## DECISION

The shape of the canonical event: which keys are fixed, how a payload evolves, and what happens when a producer newer than the consumer adds fields.

## CHOICE

1. **The 14 keys of BOOK §13 are fixed** and asserted by a test: `id, seq, ts, kind, mission_id, task_id, run_id, agent_id, session_id, runtime_id, correlation_id, causation_id, payload, provenance`.
2. **Ids in the envelope are typed**: `evt_`, `mis_`, `tsk_`, `run_`, `agt_`, `ses_`, `rt_`. A mis-typed id is refused at construction, so a mission id can never be assigned to a task.
3. **`kind` is `namespace.name`**: both sides non-empty, no whitespace. The namespace list of BOOK §13 is documented but **not enforced** — a plugin may introduce its own.
4. **`payload` declares its own version** with an integer `"v" ≥ 1`, and there is **no universal payload schema**. `build()` stamps `v: 1` when absent; the model refuses a payload without it.
5. **`seq` is monotonic in the canonical store** (ADR-0003 / WP-004). The envelope carries it; the contract does not assign it.
6. **The envelope is `extra="allow"`**: unknown keys from an external producer are preserved and round-tripped, never fatal.
7. **Internal specs are the opposite** (`extra="forbid"`): an unexpected field in our own data is a bug that should stop at the boundary.

## ALTERNATIVES

| Option | Assessment |
|---|---|
| **A universal payload schema** (`EventPayload` covering every kind) | Rejected by the Book and by sense: it grows without limit, forces every consumer to know every kind, and turns a payload change anywhere into a schema change everywhere |
| **No payload version at all** | Rejected: a consumer then cannot know whether `task_id` means what it meant last month, and silent misreads are the failure mode with the worst diagnosis cost |
| **Version in the envelope instead of the payload** | Rejected: it would add a 15th key to a set the Book fixes, and it would version the envelope (which is stable) rather than the payload (which is not) |
| **`extra="forbid"` on the envelope** | Rejected: any producer that adds one metadata field would break the runtime. Tolerating unknown keys at the edge is what keeps versions from having to move in lockstep |
| **`extra="allow"` everywhere** | Rejected: it would also swallow typos in our own specs, where a wrong field name must be a loud error |
| **Free-form `kind` strings** | Rejected: namespacing is what makes `plugin.*` and `system.*` distinguishable, and it is what the UI groups by |

## WHY

- A fixed envelope plus versioned payloads keeps the number of things that must agree **small and stable**, while letting individual event kinds evolve.
- Typed id fields turn an entire category of integration bug (a mission id where a task id belongs) into a construction-time failure.
- The asymmetry between "tolerant at the edge, strict inside" is the only combination that is both forward-compatible and fail-closed.

## REVERSIBILITY

**Low for the key set, high for everything else.** Adding an envelope key would touch every producer and consumer and needs a new ADR; adding a payload version, a namespace or a kind is routine. That asymmetry is intentional: the envelope is the one part that should be boring.

## EVIDENCE

| Claim | Evidence |
|---|---|
| The key set comes from the Book, not from taste | PROJECT_BOOK §13; `tests/contracts/test_ids_events_serialization.py::test_envelope_keys_match_project_book_section_13` |
| v1 already had the envelope concept and it survives | `hermes-plugin/hermes_plugin/engines/base.py` (`EVENT_SCHEMA`), v1 `docs/ARCHITECTURE.md` "Event plane" |
| v1's payload handling lacked versioning | v1 events are free-form dicts keyed by `event` with no version; the skeleton daemon needed a `v` stamp added when the contract froze |
| Unknown-field tolerance is required by real producers | FASE0 §10: the runtime emits event types this build does not model (`AvailableCommandsUpdate`, `SessionInfoUpdate`); a strict envelope would have rejected them |
| Typed ids prevent a real class of bug | `test_typed_id_fields_are_validated` |

## RISKS

| Risk | Mitigation |
|---|---|
| `extra="allow"` becoming a dumping ground that nobody validates | Unknown keys are preserved but never interpreted; anything the core needs becomes a typed field with a test |
| Namespaces proliferating without ownership | `docs/architecture/EVENTS.md` lists the Book's namespaces and the kinds emitted so far; a plugin's namespace is its own |
| Payload versions drifting from their consumers | Each payload version is a deliberate act (`v` is explicit); WP-004's replay test replays a 10 000-event log, which surfaces a mis-versioned consumer as a failure |
| Envelope edits sneaking in | The key set is asserted against the Book, so an edit fails a test rather than a review |

## HOW TO VALIDATE

1. `python scripts/test.py --suite contracts` (envelope keys, typed ids, payload versioning, unknown-field round-trip).
2. WP-004's replay-equivalence test on a generated 10 000-event log: projections reconstructed from the log must equal the live state (BOOK §78 gate).
3. A conformance run against a real runtime whose event set is larger than this build models — nothing may be rejected for being unknown.
