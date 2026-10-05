# ADR-0016 — Hermes transport: ACP over stdio is the primary channel

**Status:** accepted (2026-10-05) · **Decision owner:** project owner + Architect
**Relates to:** ADR-0014 (structured protocols only), ADR-0001 (independent control plane), v1 ADR-0005 (`delegate_task` / `sessions.create` gateway)
**Format:** PROJECT_BOOK §91.

---

## DECISION

Which transport the Hermes runtime adapter uses to drive sessions (`D-C`).

## CHOICE

**Primary: ACP over stdio (JSON-RPC 2.0)** — the control plane spawns `hermes acp` and speaks ACP directly through the `agent-client-protocol` client that **already ships inside the Hermes venv** (`spawn_agent_process`). No Node wrapper, no `acpx` on the critical path, no Hermes core patch.

**Declared alternative, adopted only on a measured capability gap: the Hermes TUI Gateway JSON-RPC** (BOOK §14's stated preference). If, and only if, the conformance suite shows a *required* capability missing from ACP, the adapter adds the gateway path.

**Last resort for a daemon-hosted Hermes: HTTP + SSE.** Pull-oriented and more moving parts for streaming; kept as a documented option, not a plan.

**Forbidden: the in-process `_HOST_REQUEST` shim.** It does not exist on any current host build; v1's engine has been in `"stub mode"` since it was written.

**The control plane — not the runtime — owns conversation state.** Sessions are disposable (BOOK §5.2); the daemon assembles each turn from its own canonical state.

## ALTERNATIVES

| Option | Assessment |
|---|---|
| **TUI Gateway JSON-RPC** (Book's preference) | Nothing in this repo or its documentation ever verified it. Capability coverage unknown, and no measurement exists on the recon host. Not rejected on merit — rejected as the *primary* purely because it is unverified while ACP is measured |
| **HTTP + SSE** | Works, but streaming becomes a long-lived HTTP response with reconnection semantics and the request/response pairing the gateway already provides must be rebuilt. Higher complexity for the same observable events |
| **ACP over stdio** (chosen) | Measured end-to-end on this host, typed events, per-turn usage, and the same protocol the other runtimes speak |
| **`acpx` CLI as the transport** | Rejected for the critical path: one process per invocation, so session state does not survive between turns (measured: `session/resume` always returns `refusal`). Kept as a **diagnostic/comparison tool** only |
| **`_HOST_REQUEST` / `delegate_task` gateway** | Rejected: dead on current hosts (see evidence) |

## WHY

1. **Capability coverage** — the observed event surface satisfies the v2 requirements directly: streaming, thinking, tool start/progress, session info, available commands, and per-turn `Usage` including `cached_read` (which makes cache-hit and cost accounting *measured* rather than estimated, per BOOK §17/§51).
2. **One client for several runtimes** — the same protocol serves Pi, OpenClaw and OpenCode. Each additional runtime stops being a new client implementation.
3. **No new dependency and no Node wrapper** — `agent-client-protocol` 0.9.0 with a client side is already in the Hermes venv.
4. **The in-process session limitation stops mattering** — because the control plane owns state, "sessions are disposable" (BOOK §5.2) turns the one real defect of the transport into a non-issue.
5. **It obeys ADR-0014** — typed JSON-RPC, no scraping, capability negotiation, loud failure on drift.

## REVERSIBILITY

**High.** The `RuntimeAdapter` contract is transport-agnostic (BOOK §12): `probe / create_session / send / steer / follow_up / events / interrupt / cancel / usage / artifacts / close`. Swapping ACP for the TUI gateway changes one adapter's internals and nothing above it. That is precisely why the transport may be chosen on evidence rather than dogma.

## EVIDENCE

| Claim | Evidence |
|---|---|
| ACP works for Hermes today | `~/projetos/harness-console/FASE0-EVIDENCIA.md` §3: `acpx --agent "hermes acp" --deny-all --allowed-tools "" exec -- "Responda apenas PONG"` → `PONG` · `[done] end_turn` |
| Two turns on one connection, one session, no `refusal` | FASE0 §10: `probes/acp_client_probe.py` with `acp.spawn_agent_process` → both turns `end_turn` (16.7 s and 2.0 s) |
| Per-turn usage is real, not estimated | FASE0 §10 table: turn 1 `input 47 161 / cached_read 44 800 / output 1 955`; turn 2 `input 71 825 / cached_read 69 376 / output 1 960` |
| The client library is already present | FASE0 §5: Hermes venv ships `agent-client-protocol` 0.9.0 with `Client`, `connect_to_agent`, `spawn_agent_process`, `spawn_stdio_connection` |
| Session state does not survive across processes | FASE0 §4: persistent session with `hermes acp` always returns `stopReason: refusal` (sessions are tracked in memory by the adapter process) |
| The event surface is rich enough | FASE0 §10: `AgentMessageChunk`, `AgentThoughtChunk`, `ToolCallStart`, `ToolCallProgress`, `AvailableCommandsUpdate`, `SessionInfoUpdate`, `UsageUpdate` — all observed in a real run |
| v1's approach is dead | `engines/hermes.py:77-78` (`available()` ← `_HOST_REQUEST`), `:113` (`"stub mode"`), `test_hermes_unavailable_when_no_host`; `PROJECT_STATE.md` admits the host never surfaces the shim |
| Approval semantics exist in the protocol | FASE0/`PLANO.md` §8: Hermes ACP exposes `allow_once` / `allow_session` / `allow_always`, so approvals can be routed rather than invented |

### Honest capability gaps (marked `unknown`, never assumed)

The probe proved **streaming, tool events, usage, commands, session info** and turn-per-turn execution. It did **not** prove, for Hermes over ACP:

- `steering` / `follow_up` (no mid-turn steering observed);
- `model_switch` (the `model` slash command is advertised, but switching mid-session was not exercised);
- `session_resume` / `session/list` semantics (the in-process limitation applies);
- `approvals` **routing** (the modes exist; no approval round-trip was driven);
- `compaction` **observability** (the `compress` command is advertised; the resulting events were not captured).

The Hermes `CapabilitySet` therefore declares those as `unknown`/`false`, the UI must not assume them, and each one is a **revisit trigger** for this ADR.

## RISKS

| Risk | Mitigation |
|---|---|
| `hermes acp` is a young surface and may change | Pin the Hermes version, verify in `probe()`, fail loudly on drift (ADR-0014) |
| A required capability turns out to be missing | The declared alternative (TUI gateway JSON-RPC) exists for exactly this case; swapping is adapter-local |
| The in-process session model leaks into our design | Constraint: the control plane owns state; no adapter may assume resume |
| Approvals silently auto-passing | Approvals are routed through the kernel's approval model (BOOK §40/§44); an adapter may never decide alone |
| A test turn writing real memory/state (this happened during FASE0) | Every `session/new` carries an **explicit tool restriction**; inheriting the full tool set by omission is a defect |

## HOW TO VALIDATE

1. **Runtime conformance suite** (BOOK §69) for Hermes: `probe`, `create_session`, `send`, `stream`, tool event, `cancel`, `close` — and, conditionally, `usage`, `steering`, `compaction`, `approval`, each returning `UnsupportedCapability` when absent rather than a fabricated success.
2. **Usage provenance**: a conformance assertion that per-turn `Usage` is recorded with provenance `provider_reported` (or `runtime_reported`), never `estimated`.
3. **Tool-restriction assertion**: a test that a session created without an explicit tool list is refused.
4. **Gap revisit**: when conformance marks any capability `unsupported` that the product needs, this ADR is reopened and the TUI gateway path is evaluated with the same evidence standard.
