# Architecture Decision Records — index

Numbering policy: **numbers reserved by PROJECT_BOOK §Appendix A are kept for the decisions the Book named there**, even while unwritten. Nothing is renumbered and no number is reused, so a reference to "ADR-0003" always means the same decision (this is the fix for `V1_CONFLICTS` C7; the v1 series now lives in `docs/adr/v1/` when it is migrated).

| # | Decision | Status | File |
|---|---|---|---|
| 0001 | Meta-Harness becomes an independent control plane (not a Hermes plugin) | **accepted** 2026-10-05 | `ADR-0001-control-plane.md` |
| 0002 | Agent identity is runtime-independent | reserved (BOOK A#2) — not yet written | — |
| 0003 | SQLite is the canonical source of state and events; JSONL is export/debug only | **accepted** 2026-10-05 | `ADR-0003-sqlite-canonical-state.md` |
| 0004 | Web-first UI + Tauri shell | reserved (BOOK A#4) | — |
| 0005 | Python daemon | reserved (BOOK A#5) | — |
| 0006 | RuntimeAdapter v2 | reserved (BOOK A#6) — written by WP-003 | — |
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

**0016 exists because the Book's Appendix A does not name a decision for transport selection.** Rather than reuse a reserved number (which would recreate the ambiguity C7 was about), the next free number is used.

## Decisions recorded elsewhere (not ADR numbers)

| Decision | Where |
|---|---|
| Trunk = meta-harness v2; `harness-console` becomes Phase-0 evidence and adapter source | `ADR-0001` decision log + `docs/architecture/V1_CONFLICTS.md` §C12 |
| Freeze `v0.1-hermes-hosted` on `3ef4a5c` byte-for-byte (no cleanup commit) | `ADR-0001` decision log + `V1_RECON_REPORT.md` §4.b |
| Pi transport: structured RPC (`pi --mode rpc`), no ANSI scraping | `ADR-0014` (rule) + v1 ADR-0006 (prior art) — the Pi-specific adapter contract arrives with WP-003 |
| Dashboard tab retired (`tab.hidden`), REST door kept | `V1_INVENTORY.md` D3 disposition |

## Open architectural questions (real blockers, not preferences)

| # | Question | Needs |
|---|---|---|
| Q1 | `RuntimeAdapter` v2 exact surface + `CapabilitySet` taxonomy | Architect sign-off, WP-003 |
| Q2 | Canonical event envelope finalization + namespace freeze | Architect sign-off, WP-003 |
| Q3 | Whether Pi's adapter follows ACP (one client) or stays on `pi --mode rpc` | WP-003/WP-015 — the Book's §13 prefers `--mode rpc`; ACP is measured. Reversible either way |
| Q4 | OMP and OpenClaw adapter transports | later phases; not blocking WP-002/WP-003 |
