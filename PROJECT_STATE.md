# Project State

Concise checkpoint. Not a diary. (BOOK §111/§112.)

**Branch:** `v2/control-plane` · **Historical tag:** `v0.1-hermes-hosted` → `3ef4a5c` (immutable)
**Authority order:** `PROJECT_BOOK.md` → accepted ADRs → this file → the current work package → code → tests.
`PROJECT_BOOK.md` now lives in the repository root (sha256 `d248ebbab24fcac2…`, 3145 lines), so
the first item of the authority order is readable from a clone instead of only from the owner's
machine. It is the owner's document, unedited.

---

## Current phase

**M1 — Living Agent — is complete and proven against the real runtime.** A mission, the agent
*Nova*, a Pi session on provider `opencode-go` / model `kimi-k3`, a real `read` tool call, its
events and its `provider_reported` usage in the canonical store, a proven cancel with no surviving
process, and a daemon restart after which Nova, the mission, the session history and every event
are still there. `scripts/e2e_m1.py` is that proof, run by hand because it needs the binary, a
provider and real credit.

**S1 — Execution Boundary — is complete and proven against the real runtime on Linux.** It is a
security gate, not the Book's M3: **M3 in `PROJECT_BOOK.md` §77 is Multi-Runtime Team**, which stays
ahead. This gate was reached first because the M2 run proved an agent could leave its workspace, and
no amount of team-building is safe before the control plane can refuse. The
`RequestedPolicy`/`EffectivePolicy`/`EnforcementEvidence` split (ADR-0019), the five canonical
budgets, and the `RuntimeAdapter → ExecutionEnvironment → SandboxProvider → ProcessSupervisor`
chain exist, and the first strong proof is Hermes: a real `hermes acp` session runs inside a
bubblewrap namespace where the repository, the real HOME and a host-only canary file are all
unreachable, and it still authenticates and serves a real model. The budgets fire on real events:
`wall_time` is `strong` (the supervisor kills the tree and reports survivors), `tool_calls` is
`moderate` (counted from real `tool.started` events, stopped best-effort), `tokens`/`cost` are
`weak` (the provider reports after the turn, so the record says so). `scripts/e2e_m3.py` is the
before/after proof: the same escape prompt that found the host repository in M2 reads a canary
token with no sandbox and cannot reach it with a strong one.

**M2 — Runtime Migration — is complete and proven with the real runtimes.** `scripts/e2e_m2.py`
exits 0: Nova runs on real Pi (`opencode-go` / `kimi-k3`), reads a real file with a real tool call,
hands over a **verified Context Capsule**, the Pi session is archived with its process released,
the *same* `agt_` id gets version 2 pointing at Hermes, a real `hermes acp` session continues the
same mission with the capsule attached, and after a daemon restart Nova is still Nova with the Pi
history preserved and the capsule still verifiable. The Hermes turn answered in the same language
the capsule was written in, stating the objective and the identity/runtime distinction back.

The transfer object is the capsule, never the text: `context.capsule.attached` is the canonical
link (capsule, source session, destination session, agent, mission, digest), the text handed to the
runtime is recorded as derived from it, and the migration is a lifecycle
(`migration.requested → capsule_verified → destination_created → capsule_attached →
source_archived → completed`, or `migration.failed` with the stage it reached). ADR-0018.

**The first real run found the boundary that does not exist yet.** Given an open-ended "continue
the work", the destination agent read the capsule, escaped its scratch workspace with an `ls`,
found the repository, read `PROJECT_STATE.md` and this project's own E2E logs, and began writing a
watcher for the run -- 24 tool calls, a 15-minute turn, ended only by our safety timeout. That is
the migration working (it carried operational intent, not data) and it is also proof that ACP's
missing tool allowlist (`tools_enforced: false`) is an operational gap, not a documentation note:
nothing today bounds what a migrated agent may execute. The handoff rendering now asks for
acknowledgement instead of open work, which is the right ask for a transfer -- but a scratch
directory is not a sandbox, and the script says so.

**354 tests green locally** across 8 suites (v1 21, unit 34, contracts 113, store 57, integration
63, replay 21, conformance 34, desktop 11) and the CI matrix is green on `ubuntu-latest` and
`windows-latest`. `master` and the tag `v0.1-hermes-hosted` are untouched.

| WP | State |
|---|---|
| WP-001 — Freeze V1 | **done** — tag on `3ef4a5c` byte-for-byte, branch `v2/control-plane`, bytecode untracked, dashboard tab retired |
| WP-002 — V2 repository skeleton | **done, gate closed** — daemon, event plane, layout, four cross-platform scripts, **two-OS CI green (ubuntu + windows)**, web placeholder |
| WP-005 — ProcessSupervisor | **done, gate closed** — the same suite passes on **Windows and Linux**; zero orphans verified on both (BOOK §79 gate) |
| WP-003 — Contracts foundation | **done and signed off** — 24 wire contracts, per-metric provenance, payload-bound approvals, `RuntimeAdapter` v2, `FakeRuntimeAdapter` + conformance suite, generated JSON Schema + TypeScript mirror in parity (113 tests, runs on both OSes) |
| WP-004 — Event store v2 | **done** — SQLite canonical (ADR-0003 implemented): four numbered migrations (0001–0004), append-only log enforced by triggers, transactional projections, idempotent appends, content-addressed artifacts, replay equivalence on 10 007 events, JSONL export, boot reconciliation in its own package |
| WP-006 — Web shell | **done** — TanStack Router + Query, one websocket in context with an honest three-state connection, event inspector (live or durable backlog, labelled), mission/agents/system shells that state what does not exist yet; DOM verified in headless Chromium |
| WP-015 — Pi transport | **done** — long-lived `pi --mode rpc` JSONL over stdin/stdout under `ProcessSupervisor`; `--mode json` is observation only; no ANSI scraping |
| WP-016 — Pi parser | **done** — records → `CanonicalEvent`; deltas folded into messages and never persisted; usage keeps `provider_reported` provenance and an absent metric stays `null` |
| WP-017 — Pi RuntimeAdapter | **done** — `send()` returns at the runtime's disposition; completion is `runtime.pi.settled`; provider/model come from the spec or the runtime's own answer, never from a literal |
| WP-018 — Runtime conformance | **done** — 18 tests against a scripted peer that speaks the *captured* wire format, including the RPC tool-event names; runs in the CI matrix on both OSes |
| WP-019 — Agent/Session projections + API + Chat UI | **done** — migration 0004, three projections, `/v1/missions`, `/v1/agents`, `/v1/sessions`, messages, cancel, events; the roster and the agent page with a composer, streaming text, usage with provenance, tool list and cancel |
| WP-020 — Runtime events UI | **done, folded into the agent page** — usage with per-metric provenance, bounded tool previews, and settled/open/cancelled shown as three different states |
| WP-007 — Tauri shell | **done** — a window plus a Python host that owns the daemon through WP-005's supervisor; A2/A3/A5/A6 run in CI with no Rust, A1/A3 proven by `apps/desktop/smoke_shell.py` against a real window, A7 produced an AppImage (97 MiB) and a deb (1.33 MiB) here; Windows bundle **not** verified (no Windows host involved). Logic and decisions: ADR-0009, `docs/architecture/PACKAGING.md` |

## Working (verified in this checkout)

- **Daemon** `apps/daemon/metaharness`: `GET /health`, `GET /version`, `GET /v1/events`, `WS /v1/events/ws` (+ `/events/ws` alias), loopback-only guard, optional static mount of the web bundle.
- **Event plane**: the frozen contract (ADR-0017) with no local copy of it; `kind` must be
  namespaced; `provenance.method` is required and validated against the Book's vocabulary.
  The bus persists through the store before delivering, and the websocket replays the
  durable backlog to a late subscriber.
- **Store wiring**: the daemon opens the canonical store in its lifespan, runs boot
  reconciliation, and serves `/health` with the store's schema version, event count and
  journal mode. Verified end to end by launching the real daemon twice against one data
  root: `last_seq` 3 → 7, two `system.daemon.started` events in the history, no migration
  re-run on reopen.
- **Layout**: `platformdirs` data root (`~/.local/share/MetaHarness` on Linux; `%LOCALAPPDATA%\MetaHarness` on Windows), 7 subdirectories, `METAHARNESS_DATA_DIR` override; repo root *discovered*, never assumed.
- **Scripts**: `python scripts/dev.py | test.py | doctor.py | package.py` — no Bash, no `shell=True`, works from PowerShell and POSIX.
- **Canonical store** `apps/daemon/metaharness/store` (WP-004): SQLite WAL, three numbered migrations, `BEGIN IMMEDIATE` per append, `seq` assigned inside the transaction and gap-free, append-only enforced by triggers, idempotent by event id, content-addressed artifacts with no blob column, JSONL export that is derived only. One call proves replay: `uv run python -c "import asyncio, metaharness.store as s; print(asyncio.run(s.replay_equivalence_check()))"`.
- **Boot reconciliation** `apps/daemon/metaharness/reconcile`: reads persisted state, asks a `ProcessProbe`, appends `run.interrupted` with `orphaned=true`, and never resumes work. Deliberately outside the store.
- **Execution boundary (S1)**: `packages/contracts/metaharness_contracts/policy.py` and
  `apps/daemon/metaharness/{budget.py,sandbox/}`; three providers (container → namespace → none) that
  say why they are unavailable, five canonical budgets with per-dimension enforcement levels, and
  `tests/integration/sandbox/test_execution_boundary.py` (9 tests, 1 skip where no strong provider
  exists). `scripts/e2e_m3.py` exits 0 with 31 checks against the real runtime.
- **Tests**: v1 regression 21/21 · unit 34/34 · contracts 113/113 · store 57/57 · integration 63/63 (includes the 9 execution-boundary tests) · replay 21/21 · conformance 34/34 (2 skips, by design) · desktop 11/11 — **354 measured by `scripts/test.py`**, which is the only number to trust: earlier notes in this file quoted a total that was never counted, and this one was read off the runner's own output. The suites exercise a real server, real websockets, real process trees and real hard kills; the whole default run is ~2 min.
- **ProcessSupervisor** `apps/daemon/metaharness/process`: one interface, two OS implementations, pre-signal tree snapshot, verified kill (`orphan_check` inside the emitted event), bounded streams, wall-timeout budget. Design and the orphan bug it fixed: `docs/architecture/PROCESS_SUPERVISION.md`.
- **Web shell** `apps/web` (WP-006): Vite + React + TS + TanStack Query + TanStack Router (code-based routes), one websocket owned by a context provider, connection state that distinguishes live from degraded from connecting, an event inspector that always says whether it is showing the live socket or the durable backlog, and a sidebar that marks unbuilt surfaces as `soon` instead of linking to nowhere. Built bundle is served by the daemon with an SPA fallback; deep links work and path traversal is refused (403, verified with `curl --path-as-is`). DOM verified in headless Chromium: the shell renders, the badge reads `live`, and the log's events appear.
- **CI** `.github/workflows/ci.yml`: matrix `ubuntu-latest` + `windows-latest`, seven suites (v1, unit, contracts, store, integration, replay, conformance), plus a web job gated on `apps/web/package.json`.

## Not yet built (explicitly)

No context engine, no plugin kernel, no secrets broker, no channels, no voice, no RAG, no auth
token enforcement (the enforced property today is **loopback-only**), no approvals table, no tasks
table, no worktree allocator.

**Runtimes:** Pi (`pi --mode rpc`) and **Hermes (`hermes acp`)** are implemented adapters with
conformance suites (ADR-0006, ADR-0016, `docs/protocols/`). OpenClaw and OMP are still adapters on
paper. The desktop shell exists: a window plus a Python host (ADR-0009,
`docs/architecture/PACKAGING.md`).

## Next

**Tasks / Work Graph** (the current one). A Mission stops being a grouping and gains a canonical
work graph: tasks with dependencies, states, acceptance criteria and evidence, where a Run executes
a Task and a Task is not a Session. The core lands in the daemon first (events, projection, API) and
the UI is a projection of it. Order after that: **Approvals → Artifact Inspector → Worktree
Allocator → Workboard → Canvas.**

**S2 — Selective Egress + Control Plane Auth** is registered as the next security gate, before
Swarm/Factory, and deliberately not built yet: the S1 run showed an agent with an open network
reaching the control plane's own unauthenticated API. **Swarm stays forbidden** until the Work
Graph, worktrees, approvals and S2 exist.

**M2 — Runtime Migration** (done). The point was proving that Nova survives a change of body.
`Nova/Pi → verified Context Capsule → Nova/Hermes`, same `agt_` identity, same mission, the Pi
session archived and a Hermes session continuing the work.

Order approved by the Architect:

1. **WP-007 — desktop packaging.** Done (see the table above).
2. **M2 — Runtime Migration**: **done and proven** (see the phase above). The migration surface is
   in the UI (Agent Inspector + Move Runtime modal + the migrations table), and
   `scripts/e2e_m2.py` is the real-binary proof, run by hand because it needs two providers and
   real credit.

   The precondition the Architect set is met: the ACP protocol was **observed**, not inferred —
   `tools/probe_hermes_acp.py`, capture and analysis in `docs/protocols/HERMES_ACP.md`, including
   what the handshake advertises but no call exercised. The transfer object is the verified
   capsule; copying chat history is explicitly not the mechanism, and runtime stays out of agent
   identity: Pi → Hermes is a new **session**, never a new agent.

   Required proof, and nothing less: create Nova on Pi → do meaningful work → verified capsule →
   archive the Pi session → switch the runtime policy → create a Hermes session → inject the
   capsule → continue the same mission → same `agt_` id → previous session still visible → event
   lineage intact → usage attributable per runtime/session → restart the daemon → Nova exists on
   Hermes with the Pi session preserved as history.

3. **The migration UI**: the endpoints exist and are tested; the Control UI does not surface them
   yet, so a migration is currently an API call.
4. **Event-log retention** — the log has no compaction or archival policy yet.

## Important decisions

- `docs/adr/README.md` is the index. Accepted: **ADR-0001** (independent control plane), **ADR-0003** (SQLite canonical, JSONL derived), **ADR-0014** (structured protocols only), **ADR-0016** (Hermes via ACP over stdio).
- Numbers reserved by BOOK Appendix A stay reserved, so no ADR reference is ever ambiguous.
- The parallel `~/projetos/harness-console` effort is Phase-0 evidence and an adapter source, not a competing trunk (ADR-0001 decision log).

## Verification commands

```bash
uv sync
python scripts/doctor.py                  # 0 pass / 2 warn / 1 fail
python scripts/test.py                    # v1 + unit + integration
python scripts/dev.py                     # daemon + web bundle on 127.0.0.1
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/version
git log --oneline --decorate -6
```

## Honest limitations

- The v2 suites run locally on Python 3.12; the v1 suite also passes under the Hermes venv interpreter (3.13). A bare 3.14 without PyYAML fails the v1 suite, which `scripts/test.py` now diagnoses with the interpreter path and the exact command to use.
- **The strong sandbox is verified on Linux only.** The boundary tests use bubblewrap; on Windows
  CI they skip with the exact reason (`strong sandbox real not verified here`), while the contracts,
  the policy engine, the budget engine and the lifecycle stay green there. The container provider
  reports `unavailable` with the reason when no daemon is reachable (`the daemon is not reachable at
  /var/run/docker.sock`) — it is not silently skipped.
- **The network is the weak dimension of the boundary, and the S1 run showed what that costs.** With
  `network: unrestricted` a sandboxed agent shares the host's loopback: it port-scanned, found the
  daemon's own `/v1/...` API and read another session's transcript (which is where the run's canary
  token was). The filesystem stayed strong — the repository was absent inside and the canary file
  unreadable — and `isolation: weak` was recorded for exactly this reason, so the record was right
  while the claim "the agent cannot leave" was not. With `network: restricted` the namespace has no egress — measured inside it, not
  read off the plan (`routes=0 connect=failed`) — so `network` and `isolation` are honestly `strong`,
  but **the runtime cannot reach a remote provider either**, which is the trade S2 has to remove.
  **Selective egress is not implemented** (no allowlisted proxy, no namespace egress rules), and
  **the control plane's API still has no authentication**: on an open network what an agent can
  reach includes the daemon itself.
- **A sandboxed runtime's credential is passed in its environment**, which puts it in the sandbox's
  argv: visible to the same user's processes. On a single-user desktop that is the same trust
  domain, and the runtime's own credentials file (`~/.hermes/.env`, staged 0600) is the mechanism
  used where the runtime supports it. Only credential *names* are recorded (`session.policy` →
  `credential_env`); values never enter the event log.
- **A runtime's installation is mounted read-only and its data root is a per-session copy.** Getting
  `hermes` to boot there needed its install state (`installs/<key>/facts.json`, staged with paths
  rewritten) and a writable tmpfs over its dependency environment, because it writes a lease into
  it. These are derived from the launcher and the state files, not hardcoded — but they are
  Hermes-specific facts and will need the same treatment for each new runtime.
- **The web page's DOM was exercised in headless Chromium for the WP-006 shell.** The agent
  page (WP-019) was verified over HTTP and by its own integration tests, not yet in a browser.
- **The real provider call is verified on Linux only.** On Windows the CI matrix runs the
  conformance suite, the parser and the transport lifecycle against the scripted peer — so
  *protocol/conformance verified on Windows* — but the `pi` binary, a configured provider and a
  model are not available on the runner, so **a real provider call is NOT verified on Windows**.
  Saying otherwise would be fake green.
- **Pi names its tool events differently per transport mode**: `tool_start`/`tool_end` in
  `--mode json`, `tool_execution_start`/`tool_execution_end` in `--mode rpc`. The parser accepts
  both and the fake peer emits the RPC spelling; the divergence cost a real run to find
  (`docs/protocols/PI_RPC.md` §Tool calls).
- **A wrong tool name fails silently.** Pi accepts `--tools read_file` and starts a session with
  no tools at all; the model then answers that its tool list is empty. Only the real run showed
  it. Pi's built-ins are `read`, `bash`, `edit`, `write`.
- **`duration_ms` is `null` in RPC mode** because Pi reports no duration there. Timing the tool
  ourselves would be our clock wearing the tool's name, so the field stays null and the UI prints
  `?` rather than a number we did not measure.
- **The Windows desktop bundle is not verified.** The Linux bundles were built and one of them was
  smoke-tested through a real window; on Windows the shell's Python host shares the supervisor the
  Windows CI already exercises, but that is an argument, not evidence. No Windows host was
  involved, so the honest statement is *not verified on Windows*.
- **The desktop shell emits no events yet.** `system.desktop.started` and
  `system.desktop.daemon_failed` need a daemon-side surface, and WP-007 forbids modifying
  `apps/daemon/**`. The shell's own log (`<data root>/logs/desktop-shell.log`) is the record until
  that package exists.
- **Nothing bounds what a migrated agent may execute.** ACP exposes permission modes, not a tool
  allowlist, so a Hermes session has its full toolset (`tools_enforced: false` is recorded on the
  session). The first real migration had the destination agent leave its scratch workspace, find
  the repository and start running project tooling on real provider credit. The control plane must
  be able to bound execution before an agent is migrated into a workspace that matters; today it
  cannot, and a scratch directory is not a sandbox.
- **There is no per-launch auth token.** The enforced property remains loopback-only; the shell
  passes four `METAHARNESS_*` variables to the daemon and a test asserts it passes nothing else.
  BOOK §65's Rust-side proxying waits for the daemon to have a token to proxy.
- `StaticFiles(html=True)` does **not** provide SPA fallback: an unknown path returns 404 rather than index.html. The router arrives with WP-006 and must add the fallback.
- Durability is absent by design: the event ring is in memory, so a restart loses events. WP-004 makes SQLite canonical (ADR-0003).
- `doctor.py` exits 2 when only warnings remain (missing pnpm/node/web bundle), which is information, not failure; CI calls it with `--report-only` because a runner is expected to be partial.
- CI prints two deprecation notices from third-party actions (Node 20 → 24) and one runner-label migration notice. Neither affects our code; the action versions are pinned and will be bumped deliberately.

- **`synchronous=NORMAL`.** A power cut can lose the tail of the log. What it cannot do is
  corrupt the database or leave a partial event, and the crash test asserts exactly that
  bound rather than "nothing was lost" (`docs/architecture/STORAGE.md` §4).
- **The daemon has one envelope, and it is the frozen contract.** The hand-rolled duplicate was
  deleted in the wiring slice; `events.py` re-exports the contract type, and two tests keep the
  duplicate from coming back (an identity check and an AST walk over the module).
- **The event log has no retention policy**, so it grows forever until compaction or
  archival is specified. The JSONL export is the backup story until then.
- **Boot reconciliation reports `leases_released: 0`** with an explanatory note, because the
  lease surface does not exist yet (it arrives with the worktree feature, BOOK §26/§28).
  Reporting a number we do not have would be fake green.

---

## Historical: v1 (Hermes-hosted MVP), frozen at `v0.1-hermes-hosted`

Kept verbatim in intent, useful as reference; **not** the v2 line.

- Backend plugin `hermes-plugin/` (installs to `~/.hermes/plugins/meta-harness/`) and desktop plugin `desktop-plugin/plugin.js`; 21 unit tests; capability registry, topology executor, plugin lab, character packs.
- Known defects recorded in `docs/architecture/V1_INVENTORY.md` §7: built-in content never resolves after install (D1), installer copies no content (D2), tracked bytecode (D4, fixed on this branch), stale README (D8), permanently-unavailable Hermes engine (D7).
- The v1 dashboard tab is retired on this branch; its REST door (`dashboard/plugin_api.py`) is **live** and was deliberately kept (D3).
- v1 ADRs live in `docs/DECISIONS.md` and are superseded where they conflict with v2 (see `docs/architecture/V1_CONFLICTS.md`). They will be migrated to `docs/adr/v1/` when that rename is scheduled.
