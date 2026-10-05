# ADR-0006 — RuntimeAdapter v2: one async interface, explicit capabilities

**Status:** accepted (2026-10-05, WP-003 architectural sign-off) · **Decision owner:** project owner (Architect)
**Supersedes:** v1 `engines/base.py` (`Engine` protocol) · **Relates to:** ADR-0001 (control plane), ADR-0014 (structured protocols), ADR-0016 (Hermes transport)

---

## DECISION

What interface every runtime must satisfy for the control plane to drive it, and how a caller discovers what a runtime actually supports.

## CHOICE

**`metaharness_contracts.RuntimeAdapter`** — a 13-method async protocol:

```
probe · capabilities · models · create_session · send · steer · follow_up
events · interrupt · cancel · usage · artifacts · close
```

with these binding rules:

1. **An absent capability raises `UnsupportedCapability`.** Never a fabricated success, an empty list that reads as "nothing happened", or a silent no-op.
2. **Capabilities are a `CapabilitySet`**: a mapping of dotted, versionable ids (`session.streaming`, `usage.tokens`, `approval.native`, …) to `CapabilityInfo(supported, version, metadata)`. Not a wall of booleans spread across the adapter, and unknown ids are tolerated so a newer runtime is not version-locked.
3. **`SessionSpec.allowed_tools` is required.** `[]` means "no tools"; omitting the field is refused.
4. **`events()` is a plain method returning an async iterator**, not a coroutine.
5. **A vendor is an id string, never a schema branch**: nothing in the interface, the envelope or the capability set names one.

## ALTERNATIVES

| Option | Assessment |
|---|---|
| **v1's bespoke `Engine` protocol** (`start/send/stream/cancel/close`) | Rejected: no capability negotiation, no partial-result contract, no usage/artifacts surface. v1's own consequence was an engine that reported `available=false` forever and a topology that could not tell "unsupported" from "empty" |
| **A thin wrapper per vendor SDK** | Rejected: the vendor's types then leak upward, and every new runtime reshapes the kernel. This is precisely what ADR-0001 forbids by refusing to make any runtime the host |
| **Capability booleans on the adapter** (`supports_steering: bool`, …) | Rejected in favour of ids: booleans cannot be versioned, cannot carry metadata, and grow a combinatorial interface that every adapter must implement |
| **Duck typing with no Protocol** | Rejected: `isinstance(adapter, RuntimeAdapter)` is a cheap, real check (`runtime_checkable`), and the conformance suite needs a shape to assert |
| **`steer`/`follow_up` omitted for runtimes that cannot do them** | Rejected: an absent method raises `AttributeError`, which callers cannot distinguish from a bug. A method that raises `UnsupportedCapability` is a *stated* refusal |

## WHY

- The Book's central claim (identity outlives the runtime) is only testable if the interface is runtime-neutral. A vendor-shaped parameter anywhere defeats it.
- A partial adapter is the normal case, not the exception. Explicit capability negotiation makes "degraded" a visible state instead of a mystery.
- The same suite can check every runtime (BOOK §69), which is what turns "we should verify Pi behaves like Hermes" into a command.
- Forgetting the tool restriction caused a real incident during Phase 0 (a test turn wrote a memory). Making the field required removes the failure mode structurally rather than documenting it.

## REVERSIBILITY

**Medium-high.** The interface is a contract, so changing it later needs a new ADR and touches every adapter — but adapters are few and thin, and everything above them (missions, tasks, context, UI) talks only to this surface, so a change is contained. Additive changes (a new method with a capability id) are cheap; renames are not.

## EVIDENCE

| Claim | Evidence |
|---|---|
| v1's protocol could not express negotiation | `hermes-plugin/hermes_plugin/engines/base.py`; `capabilities.py` exists *separately* for the topology layer, i.e. the missing piece was already felt |
| v1's Hermes engine could not tell "absent" from "empty" | `PROJECT_STATE.md` "Honest limitations": `available=false` on every current host, with topologies silently lacking an engine |
| A required tool restriction matters | `~/projetos/harness-console/FASE0-EVIDENCIA.md` §10: a turn executed a real `Memory manage` tool and wrote persistent state because the session was created without a restriction |
| Capability coverage is measurable per runtime | FASE0 §10: streaming, tool events, usage, commands and session info observed for Hermes over ACP; steering/model-switch **not** observed |
| The contract is satisfiable offline | `tests/contracts/test_runtime_conformance.py`: `FakeRuntimeAdapter` passes the full suite with no paid API, and a deliberately broken adapter fails it |
| The surface is vendor-free | `tests/contracts/test_contract_hygiene.py`: `ast` scan and serialized-schema scan for platform/vendor coupling |

## RISKS

| Risk | Mitigation |
|---|---|
| Adapters "approximate" a capability instead of refusing it | Conformance check 8 fails an adapter that returns normally for an unsupported call, and the fake deliberately refuses two capabilities so the path is exercised |
| `CapabilitySet` drifting into a boolean wall by accretion | Ids must be dotted and versionable; a test asserts the known list; metadata replaces boolean proliferation |
| Contract churn while runtimes move fast | `probe()` is the version gate; `UnsupportedCapability` is the degradation path; capability ids are additive |
| A runtime that speaks a protocol we cannot map | ADR-0014: it is not first-class until it does. No screen-scraping fallback |

## HOW TO VALIDATE

1. `python scripts/test.py --suite contracts` — FakeRuntime passes conformance; the broken adapter fails it.
2. The generated TypeScript exposes the same shapes (`python packages/contracts/scripts/generate.py --check` + `tsc`).
3. The first real adapter runs the same suite unchanged (BOOK §69), and any capability it cannot support appears as `unsupported` rather than being quietly dropped.
4. M2 (BOOK §76) — migrating an agent between runtimes without touching the mission — remains the end-to-end test of this decision.
