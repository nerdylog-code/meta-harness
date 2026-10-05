# ADR-0014 — Structured protocols only; no ANSI scraping

**Status:** accepted (2026-10-05) · **Decision owner:** project owner + Architect (Book §Appendix A #14)

---

## DECISION

How the control plane talks to every runtime it drives (Pi, Hermes, OpenClaw, OMP, future ones).

## CHOICE

**Structured protocols only.** A runtime is driven through a typed, machine-checkable channel — JSON-RPC 2.0 / NDJSON over stdio, or an equivalent typed protocol with declared messages. **Parsing a runtime's human-facing terminal output is forbidden whenever a structured mode exists.**

Concretely for this project:

- **Pi**: structured RPC (`pi --mode rpc`, NDJSON over stdio). v1 already does this and v1 ADR-0006 already forbids scraping.
- **Hermes**: structured external protocol — see ADR-0016 for which one.
- **OpenClaw**: the Gateway's public/operator protocol.
- **Any runtime offering only ANSI output**: it is **not a first-class runtime**. It may be integrated later as a degraded, text-only tier, clearly labelled as such in the UI — never as a peer of the structured ones.

A runtime that exposes no structured mode must still be **honest about it**: it reports `UnsupportedCapability`, it does not get emulated by screen-scraping.

## ALTERNATIVES

1. **ANSI / terminal scraping.** Rejected: it cannot carry tool events, approvals, per-turn usage or cancellation semantics in a trustworthy form; it breaks silently on any version bump, colour code, width change or localisation; and it makes "did the task actually finish?" a guess — which the Book forbids as proof (BOOK §1/§3.15, §82).
2. **In-process host shims** (v1's approach: `engines/hermes.py` resolving a module-level `_HOST_REQUEST`). Rejected: v1 proved it does not exist on any current host build, so the engine is permanently in `"stub mode"`. A shim that only exists on the author's machine is not an integration.
3. **Reimplement the runtime** (own loop, own tool layer). Rejected: that is a fork in spirit and defeats the entire premise (BOOK §2, ADR-0001).
4. **A bespoke protocol per runtime, invented here.** Rejected unless nothing typed exists: it multiplies clients, drift and test surface for no gain.

## WHY

- Typed messages are the only way to get **measured** usage, tool events and cancellations instead of inferences.
- Capability negotiation becomes possible: a runtime declares what it can do, and the UI stops assuming (BOOK §12).
- Version drift becomes **detectable** rather than mysterious: a protocol handshake fails loudly, while a scraped text change fails quietly.
- It is what makes the "same conformance suite for every runtime" gate real (BOOK §69).

## REVERSIBILITY

**High.** This is a rule about the boundary, not a data commitment. Adding a new structured adapter is additive; the only thing it forbids is a specific, low-quality implementation technique.

## EVIDENCE

| Claim | Evidence |
|---|---|
| v1 already chose correct for Pi | `engines/pi.py`: `pi --mode rpc`, JSON-RPC over stdio, docstring *"We never scrape Pi terminal output"*; v1 ADR-0006 |
| v1's Hermes path is a dead in-process shim, not a protocol | `engines/hermes.py:30-42,77-78,113`; `test_hermes_unavailable_when_no_host`; `PROJECT_STATE.md` "Honest limitations" |
| A common structured protocol already works across runtimes on this host | `~/projetos/harness-console/FASE0-EVIDENCIA.md` §3: `hermes acp`, `pi` and `openclaw acp` all answered through the same client, same event shape |
| The structured channel carries the semantics scraping cannot | FASE0 §10: observed `AgentMessageChunk`, `AgentThoughtChunk`, `ToolCallStart`, `ToolCallProgress`, `AvailableCommandsUpdate`, `SessionInfoUpdate`, `UsageUpdate` — including per-turn `Usage` with `cached_read` |
| Not every runtime qualifies today | FASE0 §2: `dsh`'s ACP is automation-only — new sessions only, no resume/list/fork, no streaming, no tool presentation, no MCP |

## RISKS

| Risk | Mitigation |
|---|---|
| Protocol churn in fast-moving targets (Hermes, OpenClaw, Pi all move) | Pin versions, verify at `probe()`, degrade **loudly** (BOOK §82); the conformance suite runs per adapter |
| A runtime dropping a capability we depend on | `CapabilitySet` is negotiated per session, not assumed; unsupported ⇒ `UnsupportedCapability`, never a fake success |
| Temptation to "just parse the output" for a quick win | This ADR makes it a defect, not a shortcut. A degraded text-only tier may only be added as its own labelled tier |
| Losing rich semantics by picking a *narrow* structured protocol | Capability coverage is the selection criterion — see ADR-0016 |

## HOW TO VALIDATE

1. Every `RuntimeAdapter` passes the same conformance suite (BOOK §69): probe, create session, send, stream, tool event, cancel, close — plus usage/steering/compaction/approval where supported.
2. A test asserts no adapter reads a child process's raw stdout as text for semantic extraction (only for debug logging at a bounded size).
3. A conformance run against a deliberately mismatched protocol version fails **loudly** rather than silently degrading.
