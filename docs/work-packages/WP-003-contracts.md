# WP-003 — Contracts foundation

**Owner:** strong builder · **Wave:** W3 · **Depends on:** WP-002 · **Reviewer:** Architect (mandatory — BOOK §74/§Appendix E) · **Blocks:** WP-004 and every runtime adapter

---

## Objective

Define the v2 domain contracts in `packages/contracts`: identifiers, canonical events, and the type surface every other component depends on. This is the package the Book singles out as requiring the strongest reasoning and an Architect signature, because a mistake here propagates into storage, adapters, UI and benchmarks.

## Dependencies

WP-002 green (monorepo, `packages/contracts` package importable, CI matrix running).

**Decision prerequisites — do not start before these are resolved** (CONFLICTS):

| Prereq | Question | Where decided |
|---|---|---|
| C12 | Is meta-harness v2 the trunk, with `harness-console` as Phase-0 evidence? | Architect ADR |
| C1 | SQLite as single source of truth, JSONL as export | Architect ADR (supersedes v1 ADR-0003) |
| C2 | RuntimeAdapter v2 replaces v1's `Engine` protocol | Architect ADR (this package implements it) |
| C3 | Hermes transport: TUI gateway JSON-RPC vs HTTP+SSE vs ACP-over-stdio | Architect ADR (shapes `CapabilitySet`) |
| C4 | Pi transport: `--mode rpc` vs ACP | Architect ADR (shapes the Pi adapter, not the contract) |
| C7 | v1 ADRs renumbered out of the way | WP-001 |

A builder who cannot point to a resolved decision for C1–C4 must **stop**, not choose.

## Allowed files

```
packages/contracts/**                     (the deliverable)
docs/architecture/DOMAIN.md               (domain vocabulary, BOOK §7)
docs/architecture/EVENTS.md               (canonical event catalogue, BOOK §13)
docs/architecture/RUNTIMES.md             (RuntimeAdapter v2 + capability matrix, BOOK §12)
docs/adr/ADR-0006-runtime-adapter-v2.md + the event-envelope ADR   (this package freezes them; see docs/adr/README.md for numbering)
tests/contracts/**                        (schema round-trip, validation, versioning)
```

## Forbidden files

```
apps/daemon/**       (WP-004 consumes contracts; it does not define them)
apps/web/**  apps/desktop/**
hermes-plugin/**  desktop-plugin/**
plugins/runtimes/**
```

## Required reading

`PROJECT_BOOK.md` §3/§5 (non-negotiable principles), §7 (vocabulary), §8/§13 (events), §9 (storage), §10/§17/§18 (plugin kernel + invariants), §12 (RuntimeAdapter), §15 (workspace policy), §17 (UsageSample), §22 (Context Capsule), §27 (workspace policy), §40 (risk levels), Appendix A (ADR list), Appendix B (contract list) · `docs/architecture/V1_INVENTORY.md` §5 (what to reuse) · `docs/architecture/V1_CONFLICTS.md` C1–C4, C12.

## Architecture constraints

1. **Pydantic v2** models, frozen/validated, no ORM. Contracts are data, not behaviour.
2. **IDs are typed and prefixed** (`mis_`, `agt_`, `tsk_`, `run_`, `ses_`, `evt_`, `art_`, `plg_`, `rt_`) and sortable (ULID-style). A bare string id anywhere is a defect.
3. **Event envelope is exactly** BOOK §13: `id, seq, ts, kind, mission_id, task_id, run_id, agent_id, session_id, runtime_id, correlation_id, causation_id, payload, provenance`. `provenance` must express **how** the payload was obtained (`provider_reported`, `runtime_reported`, `measured`, `estimated`, `unknown`) — never an unlabelled number.
4. **`UsageSample` carries per-field provenance** (BOOK §17/§49). Mixing `estimated` and `provider_reported` silently is a correctness bug, not a formatting issue.
5. **`RuntimeAdapter` returns `UnsupportedCapability`, never a silent success** (BOOK §12, §69). The `CapabilitySet` is a declared feature-flag list, and the UI must be able to ask "does this runtime support steering?" without guessing.
6. **Kernel invariants are non-delegable** (BOOK §11/§18): identity, event ordering, audit trail, permission floor, secret isolation, redaction, resource budgets, plugin trust, approval enforcement. No contract may be shaped so a plugin could satisfy one of these itself.
7. **Risk levels R0–R4** (BOOK §40) are part of the contract, and R4 approval cannot be disabled by a plugin.
8. **`ContextCapsule` schema is frozen as data** (BOOK §22) — including `self_assessment`, `resume_instruction` and `invariants`.
9. **Backwards compatibility:** every contract carries `schema_version`; breaking changes need a new ADR. v1's `EVENT_SCHEMA = "meta-harness.event/v1"` string is retired rather than reused.
10. **No adapter-specific field leaks into a shared contract.** If the Hermes adapter needs a field the Pi adapter cannot express, that field belongs in a namespaced `extensions` map.

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Contract set complete | `AgentId, MissionId, TaskId, RunId, SessionId, ArtifactId, PluginId, RuntimeId` and `AgentSpec, AgentVersion, MissionSpec, TaskSpec, WorkspacePolicy, SessionSpec, RuntimeInfo, CapabilitySet, RuntimeEvent, CanonicalEvent, UsageSample, ArtifactRef, ContextCapsule, ApprovalRequest, ActionProposal, PluginManifest` all exist and import |
| A2 | Round-trip | every contract survives `model_dump_json()` → `model_validate_json()` byte-equivalent on a fixture |
| A3 | Validation is fail-closed | malformed ids, unknown enum values, missing provenance and negative budgets are rejected — with tests asserting the rejection |
| A4 | Provenance is enforced | a `UsageSample` built without provenance raises; `estimated` and `provider_reported` are distinguishable in the serialized form |
| A5 | Capability negotiation | a fake adapter that supports only `streaming` reports exactly `{streaming: true, …false}`; requesting `steering` from it yields `UnsupportedCapability` (not `None`, not a fabricated event) |
| A6 | Event catalogue frozen | `docs/architecture/EVENTS.md` lists every namespace from BOOK §13, and a test asserts the code's enum matches the document (drift is a test failure) |
| A7 | Invariant guard | a test asserts that no contract exposes a field capable of disabling the kernel invariants of BOOK §11 (e.g. no `bypass_redaction`, no `trust_override`, no `approval_disabled`) |
| A8 | Architect sign-off | the PR/report carries an explicit Architect approval line for the frozen contract set |

## Expected output

`packages/contracts` (installable, versioned), `docs/architecture/DOMAIN.md`, `EVENTS.md`, `RUNTIMES.md`, and ADRs `0002`–`0006` recording the four transport/source-of-truth decisions this package depends on.

## Expected events

The contract set *defines* the catalogue; this package emits nothing at runtime. `EVENTS.md` must fix names for the full BOOK §13 namespace list, including the ones nobody emits yet (`mission.*`, `task.*`, `context.*`, `usage.*`, `approval.*`, `plugin.*`, `heartbeat.*`, `eval.*`, `benchmark.*`).

## Windows requirements

- Contract tests run on `windows-latest` in CI with the same fixtures (paths are irrelevant to contracts — which is itself the point, and the test suite should prove it with a fixture containing a Windows path).
- No `pathlib` behaviour may differ between OSes for `ArtifactRef.path` (normalize on construction, keep the original in `metadata`).

## Linux requirements

- Same fixtures, same results. The contract package must have **zero** OS-conditional code.

## Exact acceptance commands

POSIX:

```bash
git checkout v2/control-plane
uv sync
python scripts/test.py --suite contracts        # expect: all contract tests pass
python -c "import metaharness_contracts as c; print(c.CanonicalEvent.model_json_schema()['title'])"
python scripts/doctor.py                        # expect: contracts import + version reported
```

Windows (PowerShell):

```powershell
git checkout v2/control-plane
uv sync
python scripts\test.py --suite contracts
python -c "import metaharness_contracts as c; print(c.CanonicalEvent.model_json_schema()['title'])"
python scripts\doctor.py
```

Cross-check that both OSes agree:

```bash
gh run list -R nerdylog-code/meta-harness --workflow ci --limit 5   # ubuntu + windows, both success
```

## Known risks

| Risk | Mitigation |
|---|---|
| Over-modelling: 30 contracts written before one adapter exists, none validated against reality | A5 requires a *fake adapter* against the real interface, and every contract must have at least one consumer scheduled in WP-004/WP-005 |
| Freezing a capability matrix that assumes features no runtime has | Derive `CapabilitySet` from the measured evidence (`Usage` per turn from ACP, streaming, tool events) and mark unverified flags as `unknown`, not `true` |
| The provenance requirement being softened "for now" | A4 is a gate, not a guideline: the Book's §51 benchmarks are meaningless without it |
| Contract drift between code and `EVENTS.md`/`DOMAIN.md` | A6 makes drift a failing test |
| Choosing a transport (C3/C4) inside the contract work | Explicitly a prerequisite; the ADR must exist before this package starts |
