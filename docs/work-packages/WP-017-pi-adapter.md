# WP-017 — Pi RuntimeAdapter

**Owner:** strong builder · **Wave:** W5 · **Depends on:** WP-015, WP-016 · **Blocks:** WP-018, WP-019, WP-020

---

## Objective

Implement `RuntimeAdapter` v2 (ADR-0006) over the Pi transport and parser, so Pi is driven
exactly like any other runtime and the daemon never learns Pi-specific vocabulary.

## Allowed files

```
apps/daemon/metaharness/runtimes/pi/adapter.py        (new)
apps/daemon/metaharness/runtimes/pi/__init__.py
apps/daemon/metaharness/runtimes/registry.py          (new: id → adapter factory)
tests/integration/runtimes/test_pi_adapter.py
```

## Forbidden files

```
packages/contracts/**                                  (frozen interface)
apps/daemon/metaharness/store/**                       (the adapter emits; it never writes projections)
```

## Required reading

ADR-0006 · `docs/protocols/PI_RPC.md` §7 · the frozen conformance helper
(`packages/contracts/metaharness_contracts/conformance.py`).

## Architecture constraints

1. **The adapter emits events through the store** (`EventBus.publish`), so every runtime action
   is persisted before the UI hears about it. It never touches the store's internals.
2. **Unsupported capabilities are explicit.** `usage.cost` on a provider that reports no cost
   returns `UnsupportedCapability`, never a zero.
3. **`send` resolves on `disposition`**, and the *run* is considered finished only on
   `agent_settled`. A caller waiting for completion waits for settled.
4. **Session directories live under the platformdirs data root**, one per session, so resume and
   audit work and nothing is written outside our root.
5. **No model name is hardcoded.** The provider/model comes from the session spec; the adapter
   asks `get_available_models` and reports what it found.
6. **Cancellation is `abort` → `clear_queue` → verify**, then `ProcessSupervisor` if the process
   does not settle within the grace budget.

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Contract conformance | `run_conformance` against the real adapter reports no shape violations |
| A2 | Scripted session | against the fake RPC peer: create → send → events → settled → close, with the expected event kinds in order |
| A3 | Real probe (opt-in) | one real `pi --mode rpc` session answers a trivial prompt, streams a `text_delta`, and reports provider usage — skipped with a clear message when no provider is configured, never faked |
| A4 | Cancel | `cancel()` mid-run ends with `agent_settled` or a killed tree with `orphan_check == True`, and no orphan survives |
| A5 | Resume | a session created with an explicit dir can be reopened and `get_state` agrees with what was persisted |
| A6 | Capability honesty | a capability the probe did not confirm reports `UnsupportedCapability` |
| A7 | Both OSes | A2/A4/A6 run on Linux and Windows CI |

## Expected events

```
runtime.session.created   { runtime_id: rt_pi, session_dir, provider, model }
runtime.session.attached  { session_id }
runtime.session.closed    { reason }
usage.sampled             { ... }
runtime.unreachable       { reason }        (probe failure, never silent)
```

## Risks

| Risk | Mitigation |
|---|---|
| A3 silently skipping and being read as green | the skip message names the missing provider, and the M1 gate requires the real run explicitly |
| Adapter creeping into Pi's session files as a second store | resume goes through RPC commands; our store stays canonical |
