# Architecture Decision Records — index

Numbering policy: **numbers reserved by PROJECT_BOOK §Appendix A are kept for the decisions the Book named there**, even while unwritten. Nothing is renumbered and no number is reused, so a reference to "ADR-0003" always means the same decision (this is the fix for `V1_CONFLICTS` C7; the v1 series now lives in `docs/adr/v1/` when it is migrated).

| # | Decision | Status | File |
|---|---|---|---|
| 0001 | Meta-Harness becomes an independent control plane (not a Hermes plugin) | **accepted** 2026-10-05 | `ADR-0001-control-plane.md` |
| 0002 | Agent identity is runtime-independent | reserved (BOOK A#2) — not yet written | — |
| 0003 | SQLite is the canonical source of state and events; JSONL is export/debug only | **accepted** 2026-10-05 | `ADR-0003-sqlite-canonical-state.md` |
| 0004 | Web-first UI + Tauri shell | reserved (BOOK A#4) | — |
| 0005 | Python daemon | reserved (BOOK A#5) | — |
| 0006 | RuntimeAdapter v2 — one async interface, explicit capabilities | **accepted** 2026-10-05 | `ADR-0006-runtime-adapter-v2.md` |
| 0007 | Progressive tool disclosure | reserved (BOOK A#7) | — |
| 0008 | Tool output virtualization | reserved (BOOK A#8) | — |
| 0009 | Context Capsule architecture | reserved (BOOK A#9) | — |
| 0010 | Single-writer invariant | reserved (BOOK A#10) | — |
| 0011 | Plugin safety floor | reserved (BOOK A#11) | — |
| 0012 | Work Graph is canonical | reserved (BOOK A#12) | — |
| 0013 | Windows + Linux first-class | reserved (BOOK A#13) | — |
| 0014 | Structured protocols only; no ANSI scraping | **accepted** 2026-10-05 | `ADR-0014-structured-protocol-over-ansi-scraping.md` |
| 0015 | Self-improvement requires staging/eval | reserved (BOOK A#15) | — |
| 0016 | Hermes transport: ACP over stdio primary; TUI gateway JSON-RPC as declared alternative | **accepted** 2026-10-05 | `ADR-0016-hermes-transport-acp-stdio.md` |
| 0017 | Event envelope: fixed keys, versioned payloads, tolerant edges | **accepted** 2026-10-05 | `ADR-0017-event-envelope.md` |

**0016 and 0017 exist because the Book's Appendix A does not name a decision for transport selection or for the envelope's payload-versioning rule.** Rather than reuse a reserved number (which would recreate the ambiguity C7 was about), the next free numbers are used.

## Decisions recorded elsewhere (not ADR numbers)

| Decision | Where |
|---|---|
| Trunk = meta-harness v2; `harness-console` becomes Phase-0 evidence and adapter source | `ADR-0001` decision log + `docs/architecture/V1_CONFLICTS.md` §C12 |
| Freeze `v0.1-hermes-hosted` on `3ef4a5c` byte-for-byte (no cleanup commit) | `ADR-0001` decision log + `V1_RECON_REPORT.md` §4.b |
| Pi transport: structured RPC (`pi --mode rpc`), no ANSI scraping | `ADR-0014` (rule) + v1 ADR-0006 (prior art) — the Pi-specific adapter contract arrives with WP-003 |
| Dashboard tab retired (`tab.hidden`), REST door kept | `V1_INVENTORY.md` D3 disposition |
| Desktop shell = a window plus a Python host; the renderer is granted no capability and receives no secret | `ADR-0009` (WP-007) + `docs/architecture/PACKAGING.md` |
| The Context Capsule is the migration transfer object; the text handed to the runtime is derived from it | `ADR-0018` (M2) + `docs/protocols/HERMES_ACP.md` |

## Open architectural questions (real blockers, not preferences)

| # | Question | Needs |
|---|---|---|
| Q3 | Whether Pi's adapter follows ACP (one client) or stays on `pi --mode rpc` | **Settled by WP-015 + WP-007 (2026-10-06).** Pi stays on `pi --mode rpc`: its adapter is implemented, conformant and proven end to end. ACP was measured directly against the installed `hermes acp` (agentInfo `hermes-agent 0.21.5`) — protocol version 1, JSON-RPC 2.0 over stdio, `session/update` notifications, provider-reported usage — recorded verbatim in `docs/protocols/HERMES_ACP.md`. ACP is the Hermes transport (ADR-0016), not the Pi one: two runtimes, two observed protocols, one adapter interface. |
| Q4 | OMP and OpenClaw adapter transports | later phases; not blocking WP-004 |

**Resolved by WP-003 (2026-10-05):** Q1 (`RuntimeAdapter` surface + `CapabilitySet` taxonomy → ADR-0006) and Q2 (canonical envelope + namespace freeze → ADR-0017).
