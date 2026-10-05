# Domain model — v2 contracts (WP-003)

The vocabulary of PROJECT_BOOK §7 is now code: `packages/contracts/metaharness_contracts`.
Python is the source of truth; the TypeScript mirror and the JSON Schema are
generated from it and their parity is a test, not a promise.

| Term | Contract | Notes |
|---|---|---|
| Mission | `MissionSpec` | the largest durable unit; tasks link to it by `mis_` id |
| Agent identity | `AgentSpec` | runtime-independent: `runtime_policy` names a *preference*, not a binding |
| Agent version | `AgentVersion` | frozen; `ref` is `agt_…:vN`, which is what a mission records so a run is reproducible |
| Session | `SessionSpec` / `RuntimeSession` | disposable; `allowed_tools` is **required** |
| Run / Task | `TaskSpec` | carries budgets, dependencies, an acceptance gate and proof |
| Workspace policy | `WorkspacePolicy` | intent (`include`/`write`/`readonly`/`deny`/`network`/`secrets`) separated from `enforcement` |
| Artifact | `ArtifactRef` | a pointer with `sha256`; the bytes never enter the database |
| Usage | `UsageSample` | per-metric provenance; `unknown` is never `0` |
| Approval | `ApprovalRequest` | bound to one payload hash |
| Proposal | `ActionProposal` | authorises nothing by itself |
| Context Capsule | `ContextCapsule` | 16 structured fields; bounded; not a transcript |
| Plugin | `PluginManifest` | **no `trust` field**: trust is the system's judgement, not a self-declaration |

## Identifiers

Opaque strings with a type prefix: `agt_ mis_ tsk_ run_ ses_ art_ plg_ rt_ evt_ apr_`.
Nothing parses meaning out of the body (WP-003 decision 1); the generation
algorithm is an implementation detail and may change without a contract change.
Envelope fields carry typed ids, so a mission id can never be assigned to a task.

## What the contracts deliberately refuse

| Refusal | Why |
|---|---|
| `SessionSpec` without `allowed_tools` | an omitted list means "inherit every tool"; a test turn once wrote real memory that way (V1_CONFLICTS §C12) |
| `AcceptanceGate` with neither command nor criteria | "the model said it is done" is not a gate (BOOK §3.15/§81) |
| Pre-granted R4 `Permission` | R4 needs an approval per action, and no plugin may disable it (BOOK §40/§44) |
| `SecretPolicy` carrying a value | a policy refers to secrets by name; the broker resolves them |
| `PluginManifest.trust` | a plugin declaring its own trust defeats the safety floor (BOOK §18) |
| A capsule that grew past 64 KB or names a transcript field | a handoff that becomes a conversation dump defeats its own purpose |
| `unknown` metrics holding a number | `unknown` and `0` are different facts |

## Fail-closed vs forward-compatible

Two opposite choices, made on purpose:

- **Internal specs** (`SessionSpec`, `UsageSample`, `WorkspacePolicy`, …) use
  `extra="forbid"`: an unexpected field in our own data is a bug and should stop
  at the boundary.
- **External envelopes** (`CanonicalEvent`, `RuntimeInfo`, `RuntimeSession`) use
  `extra="allow"`: a newer producer may attach metadata this build has never
  heard of, and refusing it would make every version lockstep (WP-003 decision 12).

## Cross-platform

The contract layer contains no OS branch: no `os`, `sys`, `platform`,
`subprocess`, no path arithmetic, and a test enforces that with `ast`. Windows
and Linux can therefore not diverge semantically — the only OS-shaped concern
(file paths) is normalized on construction (`ArtifactRef.path` uses `/`), with
the original preserved in `metadata` by the producer.

## Where the rest lives

| Topic | Document |
|---|---|
| Event envelope, namespaces, payload versioning | `docs/architecture/EVENTS.md` |
| Runtime adapter, capabilities, conformance | `docs/architecture/RUNTIMES.md` |
| Storage (SQLite canonical, replay) | ADR-0003, WP-004 |
| Generated artifacts and how to regenerate them | `packages/contracts/README.md` |
