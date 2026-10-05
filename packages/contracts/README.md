# Domain contracts (WP-003) — the single logical specification

```
metaharness_contracts/         the source of truth (pydantic v2, Python 3.12+)
schema/contracts.schema.json   GENERATED — JSON Schema for every wire contract
ts/contracts.ts                GENERATED — the TypeScript mirror
scripts/generate.py            the generator (and --check for CI)
```

**Python is the source of truth.** The schema and the TypeScript file are derived,
and `tests/contracts/test_parity.py` runs the generator in `--check` mode and
compares bytes, so a hand edit to a generated file is a failing test rather than a
divergence discovered later by the UI.

```bash
python packages/contracts/scripts/generate.py            # write both artifacts
python packages/contracts/scripts/generate.py --check    # verify (used by tests + CI)
npx --yes typescript@5.6.3 tsc -p packages/contracts/ts/tsconfig.json
python scripts/test.py --suite contracts                 # 113 tests, incl. parity + tsc
```

## Modules

| Module | Contents |
|---|---|
| `ids.py` | typed, opaque ids (`agt_ mis_ tsk_ run_ ses_ art_ plg_ rt_ evt_ apr_`) |
| `enums.py` | `Provenance`, `RiskLevel`, `Enforcement`, `TrustLevel`, `TaskState`, capability ids, `UnsupportedCapability`, `ContractError` |
| `usage.py` | `Metric` (per-value provenance) and `UsageSample` (19 metrics + identity) |
| `events.py` | `CanonicalEvent` — the 14-key envelope, typed ids, versioned payloads |
| `domain.py` | `MissionSpec`, `TaskSpec`, `AcceptanceGate`, `Budget`, `ArtifactRef` |
| `agent.py` | `AgentSpec`, `AgentVersion`, `RuntimePolicy`, `ModelPolicy`, `Permission` |
| `workspace.py` | `WorkspacePolicy`, `NetworkPolicy`, `SecretPolicy`, enforcement reporting |
| `approval.py` | `ActionProposal`, `ApprovalRequest` (payload-bound), `payload_hash` |
| `capsule.py` | `ContextCapsule` + its 16 structured parts and size ceiling |
| `runtime.py` | `RuntimeAdapter` protocol, `CapabilitySet`, `SessionSpec`, `RuntimeInfo` |
| `plugin.py` | `PluginManifest` (no `trust` field), `PluginKind` |
| `serialization.py` | JSON-safe conversion, canonical dumps, round-trip helper |
| `fake.py` | `FakeRuntimeAdapter` — deterministic, offline |
| `conformance.py` | the runtime conformance suite (BOOK §69) |

## Design rules encoded in the tests, not just in comments

- `unknown` is never `0`; a metric that was not measured has `value = None`.
- provenance is **per metric**, never per sample.
- an approval authorises exactly one payload hash.
- a capability that is absent raises `UnsupportedCapability`.
- a session without an explicit tool restriction is refused.
- a plugin cannot declare its own trust.
- the envelope carries no vendor branch; the contract layer has no OS branch.
- the capsule is a handoff (bounded, structured), not a transcript.

Read `docs/architecture/DOMAIN.md`, `EVENTS.md` and `RUNTIMES.md` for the prose,
and `docs/adr/ADR-0006-runtime-adapter-v2.md` / `ADR-0017-event-envelope.md` for
why the shape is what it is.
