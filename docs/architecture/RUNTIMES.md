# Runtime contract — adapter surface, capabilities, conformance

Source of truth: `packages/contracts/metaharness_contracts/runtime.py`
(WP-003 decisions 3 and 4). This document is the map; the tests are the law.

## The adapter

```
probe() -> RuntimeInfo
capabilities() -> CapabilitySet
models() -> list[ModelInfo]
create_session(SessionSpec) -> RuntimeSession
send(session_id, Message) -> None
steer(session_id, str) -> None
follow_up(session_id, Message) -> None
events(session_id) -> AsyncIterator[CanonicalEvent]
interrupt(session_id) -> None
cancel(session_id) -> None
usage(session_id) -> UsageSample
artifacts(session_id) -> list[ArtifactRef | str]
close(session_id) -> None
```

Properties the contract insists on:

- **Async, stable, vendor-free.** No adapter method takes a vendor-shaped
  argument, and nothing in the interface mentions which runtime is speaking.
- **An absent capability raises `UnsupportedCapability`** — never a fabricated
  success, an empty list that looks like "nothing happened", or a no-op. This is
  the single most repeated rule in the Book (§12, §69, §82), and conformance
  checks it.
- **`SessionSpec.allowed_tools` is required.** Omitting the list means "inherit
  every tool", which is how a Phase-0 test turn once wrote real memory. `[]` is a
  legitimate value and means "no tools".
- **`events()` is a plain method returning an async iterator**, not a coroutine —
  a detail that breaks every implementation that gets it wrong, so the
  conformance suite asserts the shape.

## Capabilities

A `CapabilitySet` is a mapping of dotted id → `CapabilityInfo(supported, version,
metadata, note)`. Not a wall of booleans on the adapter, not a vendor enum:
unknown ids are allowed (a newer runtime may advertise more than this build
knows), malformed ids are refused, and every entry is versionable.

```
session.streaming   session.steer       session.follow_up   session.resume
tool.events         usage.tokens        usage.cost          approval.native
context.compaction  model.switch        agent.subagents     workspace.worktree
voice.native
```

The UI's rule: **ask, never assume.** `capabilities.supports(x)` is the only
legitimate source for whether a control may be offered.

## Conformance (BOOK §69)

`metaharness_contracts.conformance` runs the same suite against every adapter:

1. static shape — every method present, and `isinstance(adapter, RuntimeAdapter)`
2. `probe()` returns a `RuntimeInfo` with the right `runtime_id` and availability
3. `capabilities()` returns a `CapabilitySet` with dotted, versioned ids
4. a session created **without** a tool restriction is refused
5. `create_session` returns a `ses_` id; `send` is accepted
6. `events()` yields canonical envelopes
7. `usage()` returns a `UsageSample` with per-metric provenance, and **unknown is
   not zero**; if `usage.tokens` was advertised, token usage must actually be
   reported
8. an unsupported capability raises rather than returning
9. `cancel` and `close` are accepted

`ConformanceReport.summary()` prints the failures with the check that failed, so a
partial adapter is visible rather than approximately working.

## The fake adapter

`FakeRuntimeAdapter` implements the protocol in-process: deterministic, offline,
no paid API (WP-003 decision 14). It supports `session.streaming`, `tool.events`,
`usage.tokens`, `session.follow_up` and deliberately **refuses**
`session.steer` and `context.compaction`, so the unsupported path is exercised
rather than assumed. It is what the conformance suite is demonstrated against —
and what later work packages use to drive the kernel before a real runtime is
attached.

`tests/contracts/test_runtime_conformance.py` also runs a deliberately broken
adapter ("advertises steering, does nothing; claims `usage.tokens`, reports
nothing") and asserts conformance **fails** it: a suite that cannot fail proves
nothing.

## What is not decided here

Transport per runtime is a separate decision: ADR-0014 (structured protocols
only, no ANSI scraping) and ADR-0016 (Hermes via ACP over stdio). Pi's transport
is still open between `pi --mode rpc` (BOOK §13) and ACP — recorded as open
question Q3 in `docs/adr/README.md`, to be settled when the adapter is written,
not now.
