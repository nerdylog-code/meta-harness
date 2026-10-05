# ADR-0001 (v2) — Meta-Harness becomes an independent control plane

**Status:** **accepted by the project owner on 2026-10-05** (human decision, recorded in `docs/architecture/V1_CONFLICTS.md` §C12 resolution). Architect sign-off on the *contracts* this ADR presupposes is still required before WP-003.
**Date:** 2026-10-05
**Supersedes:** nothing. **Relates to:** v1 `docs/DECISIONS.md` ADR-0001 (*"Hermes-first integration, no fork"*, 2026-08-24) and ADR-0002 (*"Standalone desktop-plugin door"*), both of which assumed Hermes would remain the host.
**Format:** PROJECT_BOOK §91 (DECISION / CHOICE / ALTERNATIVES / WHY / REVERSIBILITY / EVIDENCE / RISKS / HOW TO VALIDATE).

---

## DECISION

Whether Meta-Harness v2 continues as a **Hermes plugin** (v1's shape: backend plugin + desktop plugin + dashboard door, no process of its own) or becomes a **standalone, runtime-neutral control plane** (its own local daemon, its own web UI, Hermes reduced to one `RuntimeAdapter` among several).

## CHOICE

**Standalone control plane.** v1's Hermes-hosted form is frozen at tag `v0.1-hermes-hosted` and kept installable as the legacy MVP. v2 is a monorepo (`apps/daemon`, `apps/web`, `apps/desktop`, `packages/contracts`) that runs without Hermes and treats Hermes, Pi, OpenClaw and OMP as adapters.

## ALTERNATIVES

1. **Stay a Hermes plugin, grow inside it.** Cheapest path; reuses v1 wholesale; no new process model, no packaging, no UI rewrite.
   Rejected: it makes the central product thesis impossible. Identity cannot be runtime-independent while the host *is* the runtime, and the Book's own §2 forbids being *"frontend alternativo para apenas um desses agentes"*. It also inherits the Hermes desktop import allowlist (`@hermes/plugin-sdk` + `react*`) as a permanent UI ceiling.
2. **Fork Hermes.** Rejected without discussion — v1 ADR-0001 already refused it, and the Book §2 repeats the refusal.
3. **Thin CLI/library over `acpx`, no daemon.** Rejected as the *product*: it has no system of record, no projections, no approvals, no usage accounting, no plugin kernel — it would be a CLI, not an operating layer. Retained as a **diagnostic** tool (see the Console evidence below).
4. **Console-shaped Hermes plugin** (the `~/projetos/harness-console` design: 3-column plugin inside Hermes Desktop, ACP as the only bus). Rejected as the trunk for the same reason as (1), but its *measurements* are adopted (see EVIDENCE).

## WHY

The Book's thesis (§1, §115) is that an agent's identity outlives the runtime that executes it. That is only testable if the layer that stores identity, missions, tasks and context capsules is **not** any runtime's host. v1's structure guarantees the opposite: `plugin.yaml` declares hooks the host owns, `store.py` writes into `$HERMES_HOME`, the API is mounted on the host's dashboard, and the only engine that ever resolved was the host's own. v2's PHASE 1–4 walking skeleton (BOOK §77–§80) is also impossible inside that shape: it requires a daemon with `/health`, `/version` and a WebSocket, a web app it serves, and a `ProcessSupervisor` to own child runtimes.

## REVERSIBILITY

High, if the freeze is respected.

- v1 stays reachable: tag `v0.1-hermes-hosted`, unchanged plugin, unchanged install path.
- The v2 tree is additive: new `apps/`, `packages/`, `plugins/` directories alongside the frozen v1 directories until WP-004 makes them vestigial.
- The default bind stays `127.0.0.1` with a per-launch secret (BOOK §65), so nothing new is exposed by existing.
- Cost of reversal: v2's value is concentrated in `packages/contracts` + the daemon kernel. Reverting to plugin form would salvage the contracts and lose the kernel — i.e. reversal costs roughly PHASE 2 onward, not PHASE 0–1.

## EVIDENCE

All measured on this host on 2026-10-05, in this repository at `3ef4a5c` (see `V1_INVENTORY.md` §4 for the full command log):

| Claim | Evidence |
|---|---|
| v1's Hermes engine never resolves | `engines/hermes.py:77` (`available()` → `_resolve_host_request() is not None`), `:113` (`"stub mode"`); `test_hermes_unavailable_when_no_host` asserts False; `PROJECT_STATE.md` "Honest limitations" admits it |
| v1 has never run as installed | `~/.hermes/meta-harness` absent; plugin status `not enabled` (`hermes plugins list`); no `topologies/`/`character-packs/` in the installed tree |
| v1's built-in content cannot resolve | `characters.py:181`, `topology.py:132` (`parents[2]`), `runtime.py:80` (`parents[3]`) — all layout-dependent, all wrong for the installed tree |
| v1's UI is host-constrained | `docs/RECONNAISSANCE.md`: the loader rejects any import outside `@hermes/plugin-sdk` and `react*`; ADR-0011 then caps the renderer at Canvas2D + SVG |
| v1 has real, reusable assets | 21/21 tests pass (0.094 s, PyYAML 6.0.3); loader probe PASS (5 contributions); CI green (run `36610866187`, 15 s) |

**External evidence (not in this repository, but measured on the same host on the same day)** — `~/projetos/harness-console/FASE0-EVIDENCIA.md`:

| Claim | Bearing on this ADR |
|---|---|
| The same ACP client drives `hermes acp`, `pi-acp` and `openclaw acp` with a normalized event stream | Multi-runtime coordination is reachable **without** a bespoke protocol per runtime — it weakens the "RuntimeAdapter must be invented from scratch" premise and strengthens "the daemon owns state" |
| `hermes acp` keeps sessions in process memory, so named-session resume always returns `refusal` when the client opens a new process per invocation | A runtime-neutral layer *must* own conversation state. This is the empirical justification for the Book's "Session is disposable" (§5.2) |
| The Hermes venv already ships `agent-client-protocol` 0.9.0 **with `spawn_agent_process`** | A Python ACP client needs no new dependency, no Node wrapper, and no Hermes core patch |
| Per-turn `Usage` (`input / cached_read / output / thought / total`) arrives in the protocol | `UsageSample` can be **measured** from turn one instead of estimated — directly serves BOOK §17 and §51 |
| `dsh`'s ACP is automation-only (no resume/list/fork/stream/tools/MCP) | `dsh` cannot be a first-class UI runtime; the Book should not assume it can |
| `openclaw` is a pacman-owned package; `openclaw update` fails by design | Packaging/integration assumptions for the OpenClaw adapter must be host-package-driven |

## RISKS

| Risk | Mitigation |
|---|---|
| Scope explosion: v2 is 29 phases, v1 is a working MVP. A standalone control plane can become infrastructure with no product | BOOK §98/§116: nothing outranks the walking skeleton; PROJECT_STATE.md stays a checkpoint, not a diary |
| Losing a working artifact for a hypothetical one | v1 is tagged and installable; the freeze is explicit and testable in WP-001's gate |
| Duplicating an already-measured effort (`harness-console`) | CONFLICTS C12 recommends resolving the relationship before WP-003, not after |
| Cross-platform regression while adding a process boundary (spawn, signals, WebSocket on Windows) | WP-005 exists solely for this, with a zero-orphan gate on both OSes, before any runtime adapter is written |
| Freezing an engine that users might want patched | The freeze covers *features*, not security fixes; a security fix to the frozen form gets its own tag |

## HOW TO VALIDATE

Per BOOK §81, no prose closes this ADR. The decision is validated when:

1. **WP-001 gate** — `git tag --list` shows `v0.1-hermes-hosted`, the tag is installable, and `python tests/run_all.py` is green on the tagged commit.
2. **WP-002 gate** — a fresh clone runs `python scripts/dev.py` and `python scripts/test.py` on **Windows and Linux** with no Bash, and the daemon answers `GET /health` + `GET /version`, with `WS /events/ws` accepting a connection.
3. **M1 (BOOK §75)** — create Agent "Nova" → runtime Pi → send a message → see tokens stream → see a tool event → cancel → close → reopen → Nova and the Mission still exist.
4. **M2 (BOOK §76)** — migrate Nova Pi → Hermes with identity, Mission and capsule preserved. This is the ADR's real test: if M2 is impossible, v2 is only another harness.

---

## Decision log

| Date | Who | Decision |
|---|---|---|
| 2026-10-05 | project owner (human) | **Trunk = meta-harness v2 (this ADR's standalone control plane).** The parallel `~/projetos/harness-console` effort becomes Phase-0 evidence and a source of adapters; its measured invariants — per-turn `Usage` from the protocol, the control plane (not each harness) owning conversation state, and an explicit tool restriction on every `session/new` — are carried into v2's contracts. Recorded in `docs/architecture/V1_CONFLICTS.md` §C12. |
| 2026-10-05 | project owner (human) | **Freeze in the raw form:** tag `v0.1-hermes-hosted` on `3ef4a5c` byte-for-byte, with no cleanup commit and no local change mixed into it. The cleaned-commit variant described in `V1_RECON_REPORT.md` §4 was explicitly declined. |
