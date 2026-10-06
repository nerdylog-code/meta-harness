# First wave — dependency DAG and work packages

**Status (2026-10-05):** WP-001 **done** · WP-002 **done, gate closed** · WP-003 **done and signed off** · WP-004 **done** (canonical store, A1–A9 green, A10 in CI) · WP-005 **done, gate closed** · **daemon↔store wiring done** (not a WP of its own: the API persists through the store and the duplicate envelope is deleted) · WP-006 **done** (web shell: router, query, one websocket, event inspector, honest shells; DOM verified in headless Chromium). Next: **the Pi RuntimeAdapter and the M1 slice**; WP-007 follows it.

**Source:** PROJECT_BOOK §72 (*First engineering wave*), §75 (parallelism rule: 3–5 builders per wave), §67 (work-package format).
**Baseline:** `nerdylog-code/meta-harness @ 3ef4a5c`, no tags, `master` only.
**Rule that gates everything:** *Do not begin the Pi adapter until WP-003, WP-004 and WP-005 are green* (BOOK §72).

---

## DAG

```
                            ┌──────────────┐
                            │   WP-001     │  Freeze V1
                            │  (cheap)     │  tag + branch + purge + inventory
                            └──────┬───────┘
                                   │ hard gate: nothing downstream may start early
                            ┌──────▼───────┐
                            │   WP-002     │  V2 repository skeleton
                            │   (Luna)     │  apps/ packages/ scripts/*.py, /health /version WS
                            └──┬────┬───┬──┘
                               │    │   │
              ┌────────────────┘    │   └────────────────┐
              ▼                     ▼                    ▼
      ┌───────────────┐     ┌───────────────┐    ┌───────────────┐
      │   WP-003      │     │   WP-005      │    │   WP-006      │
      │  Contracts    │     │ ProcessSuper- │    │  Web skeleton │
      │ (strong +     │     │ visor         │    │  (Flash)      │
      │  Architect    │     │ (Luna)        │    │               │
      │  review)      │     │               │    │               │
      └───────┬───────┘     └───────────────┘    └───────┬───────┘
              │  contracts are a hard dependency for     │
              │  anything that persists or crosses a     │
              │  process boundary                        │
      ┌───────▼───────┐                                  │
      │   WP-004      │                                  │
      │ Event store v2│                                  │
      │ (Flash)       │                                  │
      └───────────────┘                                  │
                                                         │
                                                 ┌───────▼───────┐
                                                 │   WP-007      │
                                                 │ Tauri shell   │
                                                 │ (Luna)        │
                                                 └───────────────┘

  Legend:  ──►  hard dependency (start only when the parent's gate is green)
           WP-004 ∥ WP-005  (independent; BOOK §72 says they run in parallel)
```

Edges, explicitly:

| From | To | Kind | Reason |
|---|---|---|---|
| WP-001 | WP-002 | hard | The skeleton must be built on a tagged, cleaned baseline, or the migration is undiffable |
| WP-002 | WP-003 | hard | Contract files live in `packages/contracts`; the directory must exist first |
| WP-002 | WP-005 | hard | The supervisor is delivered as a package inside the monorepo |
| WP-002 | WP-006 | hard | The web app is `apps/web` |
| WP-003 | WP-004 | hard | The event store persists contract types; no contract, no schema |
| WP-003 | all later adapters | hard | RuntimeAdapter v2 is a contract (CONFLICTS C2) |
| WP-005 | all later adapters | hard | Every adapter spawns a child process; orphan-free cancel is a precondition (BOOK §79) |
| WP-006 | WP-007 | soft | Tauri needs the web build output, not the full UI (BOOK §77) |
| WP-004 ∥ WP-005 | — | none | Deliberately independent: one is data, one is processes |

**Blocked before WP-003 (see CONFLICTS):** the Hermes adapter transport (C3), the Pi adapter transport (C4), the trunk question (C12), and the ADR renumbering (C7) are all *decision* inputs to the contracts. WP-003 must not invent answers to them.

---

## Waves

| Wave | Packages | Parallel builders | Exit criterion |
|---|---|---|---|
| **W1** | WP-001 | 1 (cheap) | Baseline tagged, purged, and its gate green |
| **W2** | WP-002 | 1 (Luna) | Windows + Linux both bootstrap and answer `/health` |
| **W3** | WP-003, WP-005, WP-006 | 3 | Contracts frozen (Architect-signed), zero orphans on cancel, web shows live health |
| **W4** | WP-004, WP-007 | 2 | Replay reconstructs projections; desktop shell launches daemon + web |
| **W5** | Pi transport / parser / adapter / conformance / chat UI / events UI (BOOK §74: WP-015…WP-020) | 3–5 | The M1 walking skeleton (BOOK §75) |

Maximum concurrent builders: 3 in W3, 4 in W5 — within the Book's 3–5 envelope.

---

## Model allocation (BOOK §65/§74)

| Package | Owner | Why |
|---|---|---|
| WP-001 | cheap builder | Mechanical: tag, branch, purge, correct two stale documents. No judgment |
| WP-002 | Luna-class | Real cross-platform plumbing (entry points, health/WS, CI matrix) but fully specified |
| WP-003 | **strong builder + Architect review** | Domain contracts; the Book marks contract redesign as premium-only and Architect-owned |
| WP-004 | Flash-class | Specified schema + migrations + replay test. Mechanical once contracts are frozen |
| WP-005 | Luna-class | Genuinely tricky on Windows (process groups, psutil tree discovery, cancel semantics) but bounded and testable |
| WP-006 | Flash-class | Standard Vite/React/TS scaffold with one health view |
| WP-007 | Luna-class | Tauri v2 sidecar wiring; small surface, platform-sensitive |

---

## Shared conventions for every package

- **Report format** (BOOK §67/§110): `STATUS · FILES CHANGED · DECISIONS · TESTS RUN · RESULTS · KNOWN LIMITATIONS · ARCHITECTURE DEVIATIONS · FOLLOW-UPS`.
- **Any architecture deviation ⇒ STOP** and escalate (BOOK §110).
- **Acceptance commands are the gate; a report saying "done" is not** (BOOK §81).
- **Failures are displayed, never hidden** — runtime unavailable, unsupported capability, budget exceeded, weak isolation, estimated usage (BOOK §82).
- **No Bash requirement in the core path** (BOOK §5/§10). `python scripts/*.py` must work from PowerShell and POSIX.
- **No push, no remote creation, no branch protection changes** without explicit human authorization. Local commits are allowed; the DAG's gates run locally in CI.

---

## Package index

| ID | Title | File |
|---|---|---|
| WP-001 | Freeze V1 | `docs/work-packages/WP-001-freeze-v1.md` |
| WP-002 | V2 repository skeleton | `docs/work-packages/WP-002-repo-skeleton.md` |
| WP-003 | Contracts foundation | `docs/work-packages/WP-003-contracts.md` |
| WP-004 | Event store v2 | `docs/work-packages/WP-004-event-store.md` |
| WP-005 | Cross-platform ProcessSupervisor | `docs/work-packages/WP-005-process-supervisor.md` |
| WP-006 | Web skeleton | `docs/work-packages/WP-006-web-skeleton.md` |
| WP-007 | Tauri skeleton | `docs/work-packages/WP-007-tauri-skeleton.md` |

## W5 — the Pi wave (BOOK §74)

Written after the protocol was observed on this machine rather than guessed; the spec is
`docs/protocols/PI_RPC.md` (Pi 0.99.2's own `docs/rpc.md` + `docs/rpc-commands.md`, plus two
live probes).

| ID | Title | File | Depends on |
|---|---|---|---|
| WP-015 | Pi transport (JSONL framing, correlation, cancel) | `docs/work-packages/WP-015-pi-transport.md` | WP-005, WP-004 |
| WP-016 | Pi record parser (events + usage samples) | `docs/work-packages/WP-016-pi-parser.md` | WP-015 |
| WP-017 | Pi RuntimeAdapter v2 | `docs/work-packages/WP-017-pi-adapter.md` | WP-015, WP-016 |
| WP-018 | Runtime conformance (both OSes) | `docs/work-packages/WP-018-pi-conformance.md` | WP-017 |
| WP-019 | Agent chat UI + agent/session projection | `docs/work-packages/WP-019-agent-chat-ui.md` | WP-017, WP-006 |
| WP-020 | Runtime events UI (usage, tools, settled) | `docs/work-packages/WP-020-runtime-events-ui.md` | WP-019 |

The M1 gate (BOOK §75) is met when WP-015…WP-019 are green **and** one real Pi run has been
observed end to end: create Nova → session → stream → tool → usage → cancel → restart → Nova
and the mission still exist.
