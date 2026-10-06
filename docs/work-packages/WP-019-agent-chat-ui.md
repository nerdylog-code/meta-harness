# WP-019 — Agent chat UI

**Owner:** flash-class builder · **Wave:** W5 · **Depends on:** WP-017, WP-006 · **Feeds:** the M1 gate

---

## Objective

The first surface where a human drives a real agent: create an agent identity, start a session,
send a message, watch it stream, cancel it, and find it still there after a restart.

## Allowed files

```
apps/daemon/metaharness/api/agents.py       (new: /v1/agents, /v1/agents/{id}, /v1/sessions)
apps/daemon/metaharness/api/__init__.py
apps/daemon/metaharness/projections/agents.py   (new projection + migration 0004)
apps/daemon/metaharness/migrations/0004_agents.sql
apps/web/src/routes/agents.tsx              (replace the shell's empty state with the roster)
apps/web/src/routes/agent.tsx               (new: the chat surface)
apps/web/src/api.ts                         (types for the new endpoints)
tests/integration/api/test_agents.py
tests/unit/store/test_agents_projection.py
```

## Forbidden files

```
packages/contracts/**      (AgentSpec is frozen; a needed field goes to the Architect)
docs/protocols/PI_RPC.md
```

## Required reading

BOOK §61 (agent screen) · §75 (M1) · §116 (first engineering objective) · ADR-0003 (the log is
canonical) · `docs/architecture/STORAGE.md`.

## Architecture constraints

1. **The agent is a projection of the log**, like runs: `agent.created`, `agent.version.created`
   and `session.opened` events, folded by a projection. No table is written outside `apply_event`.
2. **Identity is runtime-independent** (ADR-0002): the API stores an `AgentSpec` with a runtime
   policy; the session records which runtime actually served it.
3. **The UI never invents state.** A session shows streaming only while frames arrive; a
   cancelled run shows the corrective event's reason.
4. **`POST /v1/agents` is the only mutation in this package**, and it is R1 (local, reversible).
   Nothing here pushes, deletes or spends beyond the model call the user asked for.
5. Restart must bring the roster and the last session's status back from the log alone.

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | create agent | `POST /v1/agents` emits `agent.created` and the projection answers on `GET /v1/agents` |
| A2 | identity is versioned | creating a second version keeps one identity with two versions (ADR-0002) |
| A3 | start session | a session row appears with `runtime_id: rt_pi` and the model actually used |
| A4 | streaming | a scripted run streams at least one `message.completed` and one `usage.sampled` |
| A5 | cancel | cancelling mid-run marks the run and records why; no orphan process |
| A6 | restart | restarting the daemon leaves the agent, its versions and the last run's state intact |
| A7 | replay | `replay_equivalence()` still holds with the agents projection registered |
| A8 | no fake state | the UI shows nothing for a capability the daemon does not have |

## Expected events

```
agent.created            { agent_id, display_name, role }
agent.version.created    { agent_id, version, runtime_policy, model_policy }
session.opened           { session_id, agent_id, runtime_id, model }
session.closed           { session_id, reason }
```

## Windows requirements

Same suite. Session directories resolve through `platformdirs`, never a hardcoded path.

## Risks

| Risk | Mitigation |
|---|---|
| A "create agent" that only writes a row and cannot run | A3 requires a real session against the adapter |
| Chat state living only in the browser | A6 is the gate the user asked for by name |
